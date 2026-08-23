"""Conversation session, restoration, deletion, and turn routes."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request, Response, status

from .dependencies import COOKIE, _authorize
from .models import CreateConversationRequest, SendTurnRequest
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
        "message": "The AI service could not complete that response safely. Please retry.",
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


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
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
            }
            for item in items
        ]
    }


@router.get("/conversations/{conversation_id}")
async def restore_conversation(conversation_id: str, request: Request) -> dict:
    _authorize(request, conversation_id)
    return {
        "conversationId": conversation_id,
        "messages": request.app.state.restorer.restore(conversation_id),
    }


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: str, request: Request) -> None:
    _authorize(request, conversation_id)
    request.app.state.conversations.delete(conversation_id)


@router.post("/conversations/{conversation_id}/turns")
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
        "turnId": turn_id,
        "status": turn_status,
        "messages": [_message_view(message) for message in messages],
    }
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
