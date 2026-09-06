"""Conversation session, restoration, deletion, and turn routes."""

from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, HTTPException, Request, Response, status

from .dependencies import COOKIE, _authorize
from .models import (
    CreateConversationRequest,
    CreateConversationResponse,
    RestoreConversationResponse,
    SendTurnRequest,
    SendTurnResponse,
)
from .restoration import message_view as _message_view
from .security import problem
from .turn_admission import TurnLimitReachedError

router = APIRouter()

TURN_FAILURES = {
    "LLM_TIMEOUT": {
        "code": "LLM_TIMEOUT",
        "message": "The AI service is taking longer than expected. Please retry.",
        "retryable": True,
    },
    "LLM_INVALID_RESPONSE": {
        "code": "LLM_INVALID_RESPONSE",
        "message": "I hit an internal response error before I could finish. Please retry.",
        "retryable": True,
    },
    "LLM_PLANNING_FAILED": {
        "code": "LLM_PLANNING_FAILED",
        # Legacy compatibility for turns persisted before planning failures became successful
        # clarification turns. Repeating identical input cannot repair a contract failure.
        "message": "I couldn’t reliably understand that request. Please rephrase it.",
        "retryable": False,
    },
    "LLM_PROVIDER_UNAVAILABLE": {
        "code": "LLM_PROVIDER_UNAVAILABLE",
        "message": "The AI service is temporarily unavailable. Please retry.",
        "retryable": True,
    },
    "LLM_REVIEW_FAILED": {
        "code": "LLM_REVIEW_FAILED",
        "message": "The AI response could not be validated safely. Please retry.",
        "retryable": True,
    },
}

@router.get("/vehicle-images/{vehicle_id}")
async def vehicle_image(vehicle_id: str, request: Request) -> Response:
    if len(vehicle_id) != 7 or not vehicle_id.startswith("veh-") or not vehicle_id[4:].isdigit():
        raise HTTPException(status_code=404, detail="Vehicle image not found")
    content, media_type = await request.app.state.dealership.get_vehicle_image(vehicle_id)
    return Response(content=content, media_type=media_type)


@router.post(
    "/conversations",
    status_code=status.HTTP_201_CREATED,
    response_model=CreateConversationResponse,
)
async def create_conversation(
    body: CreateConversationRequest, request: Request, response: Response
) -> dict:
    token = request.cookies.get(COOKIE)
    if not token or not request.app.state.conversations.has_session(token):
        token = secrets.token_urlsafe(32)
    conversation = request.app.state.conversations.create(
        token,
        body.pageContext.model_dump(),
        request.app.state.settings.webchat_retention_days,
    )
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=request.app.state.settings.webchat_cookie_secure,
        path="/api/chat",
    )
    return {
        "conversationId": conversation["id"],
        "createdAt": conversation["created_at"],
        "assistantMode": request.app.state.assistant_mode,
        "messages": [],
    }


@router.get("/conversations")
async def list_conversations(request: Request) -> dict:
    token = request.cookies.get(COOKIE)
    if not token:
        return {"items": []}
    items = request.app.state.conversations.list_for_session(token)
    return {
        "items": [
            {
                "conversationId": item["id"],
                "title": item["title"][:80],
                "messageCount": item["message_count"],
                "createdAt": item["created_at"],
                "updatedAt": item["updated_at"],
                "inProgress": _conversation_in_progress(item.get("state_json")),
            }
            for item in items
        ]
    }


def _conversation_in_progress(raw_state: str | None) -> bool:
    try:
        state = json.loads(raw_state or "{}")
    except (TypeError, ValueError):
        return False
    workflow = state.get("agentWorkflow")
    return bool(
        isinstance(workflow, dict)
        and workflow.get("activeWorkflow")
        and workflow.get("stage") not in {"completed", "cancelled"}
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=RestoreConversationResponse,
)
async def restore_conversation(conversation_id: str, request: Request) -> dict:
    _authorize(request, conversation_id)
    return {
        "conversationId": conversation_id,
        "assistantMode": request.app.state.assistant_mode,
        "messages": request.app.state.restorer.restore(conversation_id),
    }


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: str, request: Request) -> None:
    _authorize(request, conversation_id)
    request.app.state.conversations.delete(conversation_id)


@router.post("/conversations/{conversation_id}/turns", response_model=SendTurnResponse)
async def send_turn(conversation_id: str, body: SendTurnRequest, request: Request) -> dict:
    _authorize(request, conversation_id)
    request.app.state.conversations.update_context(conversation_id, body.pageContext.model_dump())
    client_message_id = str(body.clientMessageId)
    try:
        async with request.app.state.turn_admission.admit(conversation_id, client_message_id):
            turn_id, turn_status, messages = await request.app.state.orchestrator.run(
                conversation_id,
                client_message_id,
                body.text,
                body.action.model_dump(exclude_none=True) if body.action else None,
            )
    except TurnLimitReachedError as error:
        return problem(429, error.code, str(error))
    response = {
        "schemaVersion": 1,
        "turnId": turn_id,
        "status": turn_status,
        "messages": [_message_view(message) for message in messages],
        "cards": [],
        "quickReplies": [],
        "workflow": None,
        "pendingInteraction": None,
        "clientActions": [],
        "error": None,
        "assistantMode": request.app.state.assistant_mode,
    }
    try:
        response["stateVersion"] = request.app.state.conversations.get_state(
            conversation_id
        ).stateVersion
    except (KeyError, TypeError, ValueError):
        response["stateVersion"] = 0
    for message in response["messages"]:
        if message.get("viewType") == "receipt" and isinstance(message.get("view"), dict):
            response["cards"].append({"type": "receipt", "data": message["view"]})
            continue
        if message.get("viewType") != "grounded_presentation":
            continue
        presentation = message.get("view") or {}
        response["cards"].extend(presentation.get("cards") or [])
        response["quickReplies"].extend(presentation.get("quickReplies") or [])
    collector_types = {
        "secure_input",
    }
    collector = next(
        (
            message
            for message in reversed(response["messages"])
            if message.get("viewType") in collector_types
        ),
        None,
    )
    if collector:
        view = collector.get("view") or {}
        response["workflow"] = {
            "kind": _workflow_kind(collector["viewType"], view),
            "status": "collecting",
            "activation": view,
        }
    interactions = getattr(request.app.state, "protected_interactions", None)
    if interactions is not None:
        pending = interactions.active(conversation_id)
        if pending is not None:
            response["pendingInteraction"] = pending.model_dump(mode="json")
    completed_turn = request.app.state.turns.get_by_client_id(conversation_id, client_message_id)
    if completed_turn:
        try:
            actions = json.loads(completed_turn.client_actions_json or "[]")
            response["clientActions"] = actions if isinstance(actions, list) else []
        except (TypeError, ValueError):
            response["clientActions"] = []
    if turn_status == "failed":
        turn = request.app.state.turns.get_by_client_id(
            conversation_id, str(body.clientMessageId)
        )
        response["error"] = TURN_FAILURES.get(
            turn.error_category if turn else "",
            {
                "code": "TURN_FAILED",
                "message": "That message could not be completed. Please retry.",
                "retryable": True,
            },
        )
    return response


def _workflow_kind(view_type: str, view: dict) -> str:
    if view_type == "secure_input":
        return str(view.get("kind") or "workflow")
    if view_type == "private_booking_lookup":
        return "booking_lookup"
    if view_type == "part_exchange_estimate_form":
        return "part_exchange_estimate"
    if view_type == "slot_list":
        return "workshop_booking"
    if view_type == "test_drive_slot_picker":
        return "test_drive"
    return str(view.get("kind") or "workflow")
