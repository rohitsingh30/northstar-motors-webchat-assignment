"""Workshop booking, amendment, cancellation, and private lookup routes."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from webchat.integrations.dealership import DealershipError
from webchat.orchestration.state import workflow_state

from .dependencies import _authorize, _tool_view, _workshop_state
from .models import (
    BookingLookupRequest,
    WorkshopAmendDraftRequest,
    WorkshopDraftRequest,
    WorkshopExistingActionRequest,
    WorkshopOptionsRequest,
)

router = APIRouter()

async def _prepare_existing_workshop_action(
    request: Request, conversation_id: str, mode: str
) -> dict:
    tool_name = "prepare_workshop_amendment" if mode == "amend" else "prepare_workshop_cancellation"
    result = await request.app.state.tools.execute(tool_name, {}, conversation_id)
    payload = result.view_payload or {}
    summary = payload.get("summary") if isinstance(payload, dict) else {}
    summary = summary if isinstance(summary, dict) else {}
    stage = "awaiting_confirmation" if result.view_type == "confirmation" else "collecting_changes"
    request.app.state.conversations.update_workflow_state(
        conversation_id,
        _workshop_state(
            "workshop_amendment" if mode == "amend" else "workshop_cancellation",
            stage,
            {
                "serviceTypeName": summary.get("service"),
                "dealershipName": summary.get("dealership"),
            },
        ),
    )
    return {
        "text": result.text,
        "viewType": result.view_type,
        "view": result.view_payload,
    }


async def _workshop_amendment_options_result(request: Request, conversation_id: str):
    grant = request.app.state.workflow_repository.latest_grant(conversation_id)
    if not grant:
        raise HTTPException(status_code=409, detail="Verify the workshop booking again.")
    snapshot = json.loads(grant.get("booking_snapshot_json") or "{}")
    service_type_id = snapshot.get("serviceTypeId")
    service_name = snapshot.get("serviceTypeName")
    if not service_type_id and not service_name:
        raise HTTPException(status_code=409, detail="The booking service could not be resolved.")
    filters = {"workflowMode": "amendment"}
    if service_type_id:
        filters["serviceTypeId"] = service_type_id
    else:
        filters["serviceTypeName"] = service_name
    return await request.app.state.tools.execute("list_workshop_slots", filters, conversation_id)
@router.post("/conversations/{conversation_id}/workshop-options")
async def workshop_options(
    conversation_id: str, body: WorkshopOptionsRequest, request: Request
) -> dict:
    _authorize(request, conversation_id)
    filters = body.model_dump(exclude_none=True)
    result = await request.app.state.tools.execute("list_workshop_slots", filters, conversation_id)
    entities = {"serviceTypeId": body.serviceTypeId}
    constraints = {}
    if body.dealershipId:
        constraints["dealershipId"] = body.dealershipId
    request.app.state.conversations.update_workflow_state(
        conversation_id,
        {
            **workflow_state(
                "workshop_booking",
                "choosing_time",
                entities=entities,
                constraints=constraints,
            ),
            "lastTool": "list_workshop_slots",
        },
    )
    return {"viewType": result.view_type, "view": result.view_payload}


@router.post("/conversations/{conversation_id}/workshop-drafts")
async def prepare_workshop_draft(
    conversation_id: str, body: WorkshopDraftRequest, request: Request
) -> dict:
    _authorize(request, conversation_id)
    return await _tool_view(
        request,
        conversation_id,
        "prepare_workshop_booking",
        body.model_dump(exclude_none=True),
    )
@router.post("/conversations/{conversation_id}/workshop-amendment-drafts")
async def prepare_workshop_amendment_draft(
    conversation_id: str, body: WorkshopAmendDraftRequest, request: Request
) -> dict:
    _authorize(request, conversation_id)
    selected_slot = None
    if body.slotId:
        options = await _workshop_amendment_options_result(request, conversation_id)
        selected_slot = next(
            (
                item
                for item in (options.view_payload or {}).get("items", [])
                if item.get("id") == body.slotId
            ),
            None,
        )
        if selected_slot is None:
            raise DealershipError(
                409,
                "SLOT_UNAVAILABLE",
                "That workshop appointment is no longer available.",
            )
    arguments = body.model_dump(exclude_none=True, exclude={"slotId"})
    trusted_arguments = None
    if selected_slot:
        trusted_arguments = {
            "slotId": selected_slot.get("id"),
            "selectedStartsAt": selected_slot.get("startsAt"),
            "selectedDealershipId": selected_slot.get("dealershipId"),
            "selectedDealershipName": selected_slot.get("dealershipName")
            or selected_slot.get("dealershipTown"),
            "selectedServiceTypeId": selected_slot.get("serviceTypeId"),
            "selectedServiceName": selected_slot.get("serviceName"),
        }
    result = await request.app.state.tools.execute(
        "prepare_workshop_amendment",
        arguments,
        conversation_id,
        trusted_arguments=trusted_arguments,
    )
    if selected_slot and result.view_payload:
        summary = result.view_payload.setdefault("summary", {})
        summary["newAppointment"] = selected_slot.get("startsAt")
        summary["newDealership"] = selected_slot.get("dealershipName") or selected_slot.get(
            "dealershipTown"
        )
    return {"viewType": result.view_type, "view": result.view_payload}


@router.post("/conversations/{conversation_id}/workshop-amendment-options")
async def workshop_amendment_options(conversation_id: str, request: Request) -> dict:
    _authorize(request, conversation_id)
    result = await _workshop_amendment_options_result(request, conversation_id)
    return {"viewType": result.view_type, "view": result.view_payload}
@router.post("/conversations/{conversation_id}/workshop-booking-lookup")
async def lookup_booking(
    conversation_id: str, body: BookingLookupRequest, request: Request
) -> dict:
    _authorize(request, conversation_id)
    result = await request.app.state.workflows.lookup_booking(
        conversation_id, body.model_dump(exclude={"mode"})
    )
    booking = result.get("booking", {})
    if body.mode in {"amend", "cancel"} and booking.get("status") != "confirmed":
        raise DealershipError(
            409,
            "BOOKING_NOT_ACTIVE",
            "Only a confirmed workshop booking can be changed or cancelled.",
        )
    request.app.state.conversations.update_workflow_state(
        conversation_id,
        _workshop_state("existing_workshop_booking", "verified", booking),
    )
    if body.mode in {"amend", "cancel"}:
        return await _prepare_existing_workshop_action(request, conversation_id, body.mode)
    return {
        "text": "Your workshop booking has been verified.",
        "viewType": "workshop_booking_details",
        "view": {"version": 1, **booking},
    }


@router.post("/conversations/{conversation_id}/workshop-existing-action")
async def existing_workshop_action(
    conversation_id: str, body: WorkshopExistingActionRequest, request: Request
) -> dict:
    _authorize(request, conversation_id)
    grant = request.app.state.workflow_repository.latest_grant(conversation_id)
    grant_missing_or_mismatched = (
        not grant
        or (
            body.bookingReference
            and str(grant.get("booking_reference") or "").casefold()
            != body.bookingReference.casefold()
        )
    )
    if grant_missing_or_mismatched and body.bookingReference:
        proof = request.app.state.workflow_repository.workshop_proof_for_receipt(
            conversation_id,
            body.bookingReference,
        )
        if proof:
            await request.app.state.workflows.lookup_booking(conversation_id, proof)
            grant = request.app.state.workflow_repository.latest_grant(conversation_id)
            grant_missing_or_mismatched = not grant
    if grant_missing_or_mismatched:
        result = await request.app.state.tools.execute(
            "request_workshop_booking_lookup_form",
            {"mode": body.mode},
            conversation_id,
        )
        return {
            "text": result.text,
            "viewType": result.view_type,
            "view": result.view_payload,
        }
    snapshot = json.loads(grant.get("booking_snapshot_json") or "{}")
    if snapshot.get("status") != "confirmed":
        raise HTTPException(
            status_code=409,
            detail="Only a confirmed workshop booking can be changed or cancelled.",
        )
    return await _prepare_existing_workshop_action(request, conversation_id, body.mode)
