"""Shared deterministic dependencies for the chat HTTP routers."""

from __future__ import annotations

import json
import uuid

from fastapi import HTTPException, Request, status

from webchat.domain.conversation_state import (
    ConversationStateReducer,
    PendingInteractionState,
    StateEvent,
)
from webchat.domain.interactions import interaction_from_view
from webchat.orchestration.state import WorkflowStateReducer, workflow_state
from webchat.persistence.repositories.workflows import StaleWorkflowDraftError

COOKIE = "northstar_chat"

_DRAFT_TOOL_KINDS = {
    "prepare_sales_enquiry": "sales_enquiry",
    "prepare_test_drive": "test_drive",
    "prepare_vehicle_interest": "vehicle_interest",
    "prepare_callback": "callback",
    "prepare_workshop_booking": "workshop_booking",
    "prepare_dealership_message": "dealership_message",
    "prepare_part_exchange": "part_exchange",
}


def _authorize(request: Request, conversation_id: str) -> None:
    token = request.cookies.get(COOKIE)
    if not token or not request.app.state.conversations.authorize(conversation_id, token):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")


def _workshop_state(active_workflow: str, stage: str, booking: dict | None = None) -> dict:
    booking = booking or {}
    entities = {}
    if booking.get("serviceTypeId"):
        entities["serviceTypeId"] = booking["serviceTypeId"]
    elif booking.get("serviceTypeName"):
        entities["serviceTypeName"] = booking["serviceTypeName"]
    constraints = {"dealershipId": booking["dealershipId"]} if booking.get("dealershipId") else {}
    if booking.get("status"):
        constraints["bookingStatus"] = booking["status"]
    return workflow_state(active_workflow, stage, entities=entities, constraints=constraints)


def _persist_receipt_once(request: Request, conversation_id: str, text: str, receipt: dict) -> None:
    identity = (
        receipt.get("kind"),
        receipt.get("reference"),
        receipt.get("status"),
    )
    for message in reversed(request.app.state.messages.list(conversation_id)):
        if message.role != "assistant" or message.view_type != "receipt":
            continue
        try:
            previous = json.loads(message.view_payload_json or "{}")
        except (TypeError, ValueError):
            continue
        if (
            previous.get("kind"),
            previous.get("reference"),
            previous.get("status"),
        ) == identity:
            return
    request.app.state.messages.add(
        conversation_id,
        "assistant",
        text,
        None,
        "receipt",
        json.dumps(receipt, separators=(",", ":")),
    )


async def _tool_view(
    request: Request, conversation_id: str, tool_name: str, arguments: dict
) -> dict:
    """Execute a validated application tool and expose only its public view contract."""
    replaces_draft_id = request.query_params.get("replacesDraftId")
    if replaces_draft_id:
        current_draft = request.app.state.workflow_repository.latest_active(conversation_id)
        active_interaction = request.app.state.protected_interactions.active(conversation_id)
        expected_kind = _DRAFT_TOOL_KINDS.get(tool_name)
        if (
            current_draft is None
            or str(current_draft.get("id")) != replaces_draft_id
            or (expected_kind is not None and current_draft.get("kind") != expected_kind)
            or (
                active_interaction is not None
                and active_interaction.activeDraftId != replaces_draft_id
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The review changed before its replacement was submitted.",
            )
    try:
        if replaces_draft_id:
            result = await request.app.state.tools.execute(
                tool_name,
                arguments,
                conversation_id,
                replacement_draft_id=replaces_draft_id,
            )
        else:
            result = await request.app.state.tools.execute(tool_name, arguments, conversation_id)
    except StaleWorkflowDraftError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The review changed before its replacement was submitted.",
        ) from exc
    current = request.app.state.conversations.get_workflow_state(conversation_id)
    next_state = WorkflowStateReducer().advance(
        current,
        tool_name,
        arguments,
        result.view_type,
        result.facts,
    )
    request.app.state.conversations.update_workflow_state(conversation_id, next_state)
    response = {
        "text": result.text,
        "viewType": result.view_type,
        "view": result.view_payload,
    }
    # Draft preparation happens over a privacy-preserving endpoint rather than /turns. Persist the
    # static review and its typed interaction here so natural confirmation can still use the
    # single deterministic /turns boundary and restoration remains authoritative.
    _persist_confirmation_interaction(request, conversation_id, result)
    return response


def _persist_confirmation_interaction(request: Request, conversation_id: str, result) -> None:
    if result.view_type != "confirmation" or not isinstance(result.view_payload, dict):
        return
    interaction = result.interaction or interaction_from_view(
        result.text,
        result.view_type,
        result.view_payload,
    )
    if interaction is None:
        return
    previous_interaction = request.app.state.protected_interactions.active(conversation_id)
    state = request.app.state.conversations.get_state(conversation_id)
    message_id = str(uuid.uuid4())
    protected = PendingInteractionState(
        interactionId=f"interaction-{uuid.uuid4().hex[:16]}",
        kind="protected_confirmation",
        originatingMessageId=message_id,
        createdAtStateVersion=state.stateVersion + 1,
        status="awaiting_confirmation",
        activeDraftId=interaction.draft_id,
        workflowKind=interaction.workflow_kind,
    )
    next_state = ConversationStateReducer().apply(
        state,
        StateEvent(
            type="INTERACTION_PROPOSED",
            turnId=message_id,
            data=protected.model_dump(mode="json"),
        ),
    )
    request.app.state.turn_commit.commit_protected_prompt(
        conversation_id=conversation_id,
        message_id=message_id,
        text=result.text,
        view_type=result.view_type,
        view=result.view_payload,
        interaction_json=interaction.as_json(),
        purpose="workflow_prompt",
        interaction=protected,
        expected_state_version=state.stateVersion,
        state=next_state,
        superseded_interaction=previous_interaction,
    )
