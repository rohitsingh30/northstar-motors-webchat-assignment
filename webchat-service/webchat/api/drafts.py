"""Generic protected-draft confirmation and cancellation routes."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, HTTPException, Request

from webchat.domain.conversation_state import (
    ConversationStateReducer,
    StateEvent,
    complete_agent_workflow,
)

from .dependencies import _authorize, _persist_receipt_once
from .models import ConfirmDraftRequest
from .restoration import receipt_text as _receipt_text

router = APIRouter()


def _transition_pending_draft(
    request: Request,
    conversation_id: str,
    draft_id: str,
    status: str,
) -> bool:
    """Keep direct compatibility endpoints aligned with the protected interaction state."""
    interaction = request.app.state.protected_interactions.active(conversation_id)
    if interaction is None or interaction.activeDraftId != draft_id:
        return False
    state = request.app.state.conversations.get_state(conversation_id)
    reducer = ConversationStateReducer()
    if status == "executed":
        next_state = reducer.apply(
            state,
            StateEvent(
                type="INTERACTION_CONFIRMED",
                turnId=str(uuid.uuid4()),
                data={"interactionId": interaction.interactionId},
            ),
        )
        next_state = reducer.apply(
            next_state,
            StateEvent(
                type="INTERACTION_EXECUTED",
                turnId=str(uuid.uuid4()),
                data={"interactionId": interaction.interactionId},
            ),
        )
    else:
        next_state = reducer.apply(
            state,
            StateEvent(
                type="INTERACTION_CANCELLED",
                turnId=str(uuid.uuid4()),
                data={"interactionId": interaction.interactionId},
            ),
        )
    request.app.state.turn_commit.transition_interaction_state(
        conversation_id=conversation_id,
        interaction_id=interaction.interactionId,
        expected_status="awaiting_confirmation",
        status=status,
        expected_state_version=state.stateVersion,
        state=next_state,
    )
    return True


def _supersede_pending_draft(request: Request, conversation_id: str, draft_id: str) -> None:
    interaction = request.app.state.protected_interactions.active(conversation_id)
    if interaction is None or interaction.activeDraftId != draft_id:
        return
    turn_id = str(uuid.uuid4())
    state = request.app.state.conversations.get_state(conversation_id)
    next_state = ConversationStateReducer().apply(
        state,
        StateEvent(
            type="INTERACTION_SUPERSEDED",
            turnId=turn_id,
            data={"interactionId": interaction.interactionId},
        ),
    )
    request.app.state.turn_commit.supersede_interaction(
        conversation_id=conversation_id,
        interaction_id=interaction.interactionId,
        turn_id=turn_id,
        expected_state_version=state.stateVersion,
        state=next_state,
    )

@router.post("/conversations/{conversation_id}/drafts/{draft_id}/confirm")
async def confirm_draft(
    conversation_id: str, draft_id: str, body: ConfirmDraftRequest, request: Request
) -> dict:
    _authorize(request, conversation_id)
    try:
        result = await request.app.state.workflows.confirm(
            conversation_id,
            draft_id,
            str(body.clientActionId),
            body.expectedKind,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    kind = str(result.get("kind") or "")
    transitioned = _transition_pending_draft(
        request, conversation_id, draft_id, "executed"
    )
    receipt_text = _receipt_text(kind)
    replaced = request.app.state.messages.replace_draft_with_receipt(
        conversation_id, draft_id, receipt_text, result
    )
    if not replaced:
        _persist_receipt_once(request, conversation_id, receipt_text, result)
    if not transitioned:
        current = request.app.state.conversations.get_workflow_state(conversation_id)
        request.app.state.conversations.update_workflow_state(
            conversation_id, complete_agent_workflow(current)
        )
    return {"status": result.get("status"), "reference": result.get("reference"), "result": result}
@router.post("/conversations/{conversation_id}/drafts/{draft_id}/cancel")
async def cancel_draft(conversation_id: str, draft_id: str, request: Request) -> dict:
    _authorize(request, conversation_id)
    try:
        draft = request.app.state.workflow_repository.cancel(conversation_id, draft_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    workshop_change_abandoned = draft.get("kind") in {"workshop_amend", "workshop_cancel"}
    kind = "workshop_change_abandoned" if workshop_change_abandoned else "request_cancelled"
    if workshop_change_abandoned:
        grant = request.app.state.workflow_repository.latest_grant(conversation_id)
        snapshot = json.loads(grant.get("booking_snapshot_json") or "{}") if grant else {}
        retained_fields = {
            key: snapshot.get(key)
            for key in (
                "reference",
                "slotId",
                "startsAt",
                "dealershipId",
                "dealershipName",
                "dealershipTown",
                "serviceTypeId",
                "serviceTypeName",
            )
            if snapshot.get(key) is not None
        }
        result = (
            {"kind": "workshop_booking", "status": "confirmed", **retained_fields}
            if retained_fields.get("reference")
            else {"kind": "workshop_change_abandoned"}
        )
    else:
        result = {"kind": "request_cancelled", "status": "cancelled"}
        result["requestKind"] = draft.get("kind")
    transitioned = _transition_pending_draft(
        request, conversation_id, draft_id, "cancelled"
    )
    receipt_text = _receipt_text(kind, result.get("requestKind"))
    replaced = request.app.state.messages.replace_draft_with_receipt(
        conversation_id, draft_id, receipt_text, result
    )
    if not replaced:
        _persist_receipt_once(request, conversation_id, receipt_text, result)
    if not transitioned:
        current = request.app.state.conversations.get_workflow_state(conversation_id)
        request.app.state.conversations.update_workflow_state(
            conversation_id, complete_agent_workflow(current)
        )
    return {
        "status": "unchanged" if workshop_change_abandoned else "cancelled",
        "result": result,
    }


@router.post("/conversations/{conversation_id}/drafts/{draft_id}/supersede")
async def supersede_draft(conversation_id: str, draft_id: str, request: Request) -> dict:
    _authorize(request, conversation_id)
    try:
        draft = request.app.state.workflow_repository.cancel(conversation_id, draft_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    _supersede_pending_draft(request, conversation_id, draft_id)
    request.app.state.messages.supersede_confirmation(
        conversation_id,
        draft_id,
        str(draft.get("kind") or "workflow"),
    )
    return {"status": "superseded"}
