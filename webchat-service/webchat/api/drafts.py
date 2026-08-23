"""Generic protected-draft confirmation and cancellation routes."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from .dependencies import _authorize, _persist_receipt_once, _workshop_state
from .models import ConfirmDraftRequest
from .restoration import receipt_text as _receipt_text

router = APIRouter()

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
    if kind.startswith("workshop_"):
        active_workflow = {
            "workshop_booking": "workshop_booking",
            "workshop_amend": "workshop_amendment",
            "workshop_cancel": "workshop_cancellation",
        }.get(kind, "workshop_booking")
        request.app.state.conversations.update_workflow_state(
            conversation_id,
            _workshop_state(active_workflow, "completed"),
        )
    receipt_text = _receipt_text(kind)
    replaced = request.app.state.messages.replace_draft_with_receipt(
        conversation_id, draft_id, receipt_text, result
    )
    if not replaced:
        _persist_receipt_once(request, conversation_id, receipt_text, result)
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
    receipt_text = _receipt_text(kind, result.get("requestKind"))
    replaced = request.app.state.messages.replace_draft_with_receipt(
        conversation_id, draft_id, receipt_text, result
    )
    if not replaced:
        _persist_receipt_once(request, conversation_id, receipt_text, result)
    return {
        "status": "unchanged" if workshop_change_abandoned else "cancelled",
        "result": result,
    }
