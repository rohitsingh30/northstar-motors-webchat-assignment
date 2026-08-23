from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, HTTPException, Request, Response, status

from webchat.integrations.dealership import DealershipError
from webchat.orchestration.planning.ontology import GoalKey, Goals, workflow_state

from .models import (
    BookingLookupRequest,
    CallbackDraftRequest,
    ConfirmDraftRequest,
    CreateConversationRequest,
    DealershipMessageDraftRequest,
    PartExchangeDraftRequest,
    PartExchangeEstimateRequest,
    SalesEnquiryDraftRequest,
    SendTurnRequest,
    TestDriveDraftRequest,
    TestDriveOptionsRequest,
    VehicleInterestDraftRequest,
    WorkshopAmendDraftRequest,
    WorkshopDraftRequest,
    WorkshopExistingActionRequest,
    WorkshopOptionsRequest,
)
from .restoration import message_view as _message_view
from .restoration import receipt_text as _receipt_text

router = APIRouter(prefix="/api/chat/v1")
COOKIE = "northstar_chat"


def _authorize(request: Request, conversation_id: str) -> None:
    token = request.cookies.get(COOKIE)
    if not token or not request.app.state.conversations.authorize(conversation_id, token):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")


def _workshop_state(key: GoalKey, stage: str, booking: dict | None = None) -> dict:
    booking = booking or {}
    entities = {}
    if booking.get("serviceTypeId"):
        entities["serviceTypeId"] = booking["serviceTypeId"]
    elif booking.get("serviceTypeName"):
        entities["serviceQuery"] = booking["serviceTypeName"]
    constraints = (
        {"dealershipId": booking["dealershipId"]}
        if booking.get("dealershipId")
        else {}
    )
    return workflow_state(
        key, stage, entities=entities, constraints=constraints
    )


def _persist_receipt_once(
    request: Request, conversation_id: str, text: str, receipt: dict
) -> None:
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


async def _prepare_existing_workshop_action(
    request: Request, conversation_id: str, mode: str
) -> dict:
    tool_name = (
        "prepare_workshop_amendment"
        if mode == "amend"
        else "prepare_workshop_cancellation"
    )
    result = await request.app.state.tools.execute(tool_name, {}, conversation_id)
    payload = result.view_payload or {}
    summary = payload.get("summary") if isinstance(payload, dict) else {}
    summary = summary if isinstance(summary, dict) else {}
    stage = (
        "awaiting_confirmation"
        if result.view_type == "confirmation"
        else "collecting_changes"
    )
    request.app.state.conversations.update_workflow_state(
        conversation_id,
        _workshop_state(
            Goals.WORKSHOP_CHANGE_BOOKING
            if mode == "amend"
            else Goals.WORKSHOP_CANCEL_BOOKING,
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


async def _workshop_amendment_options_result(
    request: Request, conversation_id: str
):
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
    return await request.app.state.tools.execute(
        "list_workshop_slots", filters, conversation_id
    )


async def _tool_view(
    request: Request, conversation_id: str, tool_name: str, arguments: dict
) -> dict:
    """Execute a validated application tool and expose only its public view contract."""
    result = await request.app.state.tools.execute(
        tool_name, arguments, conversation_id
    )
    return {"viewType": result.view_type, "view": result.view_payload}


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
    turn_id, turn_status, messages = await request.app.state.orchestrator.run(
        conversation_id,
        str(body.clientMessageId),
        body.text,
        body.action.model_dump(exclude_none=True) if body.action else None,
    )
    return {
        "turnId": turn_id,
        "status": turn_status,
        "messages": [_message_view(message) for message in messages],
    }


@router.post("/conversations/{conversation_id}/drafts/{draft_id}/confirm")
async def confirm_draft(
    conversation_id: str, draft_id: str, body: ConfirmDraftRequest, request: Request
) -> dict:
    _authorize(request, conversation_id)
    try:
        result = await request.app.state.workflows.confirm(
            conversation_id, draft_id, str(body.clientActionId)
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    kind = str(result.get("kind") or "")
    if kind.startswith("workshop_"):
        key = {
            "workshop_booking": Goals.WORKSHOP_BOOK_SERVICE,
            "workshop_amend": Goals.WORKSHOP_CHANGE_BOOKING,
            "workshop_cancel": Goals.WORKSHOP_CANCEL_BOOKING,
        }.get(kind, Goals.WORKSHOP_BOOK_SERVICE)
        request.app.state.conversations.update_workflow_state(
            conversation_id,
            _workshop_state(key, "completed"),
        )
    receipt_text = _receipt_text(kind)
    replaced = request.app.state.messages.replace_draft_with_receipt(
        conversation_id, draft_id, receipt_text, result
    )
    if not replaced:
        _persist_receipt_once(request, conversation_id, receipt_text, result)
    return {"status": result.get("status"), "reference": result.get("reference"), "result": result}


@router.post("/conversations/{conversation_id}/test-drive-options")
async def test_drive_options(
    conversation_id: str, body: TestDriveOptionsRequest, request: Request
) -> dict:
    _authorize(request, conversation_id)
    result = await request.app.state.tools.execute(
        "list_test_drive_slots", {"vehicleId": body.vehicleId}, conversation_id
    )
    request.app.state.conversations.update_workflow_state(
        conversation_id,
        {
            **workflow_state(
                Goals.TEST_DRIVE_BOOK,
                "choosing_time",
                entities={"vehicleId": body.vehicleId},
            ),
            "lastTool": "list_test_drive_slots",
        },
    )
    return {"viewType": result.view_type, "view": result.view_payload}


@router.post("/conversations/{conversation_id}/test-drive-drafts")
async def prepare_test_drive_draft(
    conversation_id: str, body: TestDriveDraftRequest, request: Request
) -> dict:
    _authorize(request, conversation_id)
    return await _tool_view(
        request, conversation_id, "prepare_test_drive", body.model_dump()
    )


@router.post("/conversations/{conversation_id}/workshop-options")
async def workshop_options(
    conversation_id: str, body: WorkshopOptionsRequest, request: Request
) -> dict:
    _authorize(request, conversation_id)
    filters = body.model_dump(exclude_none=True)
    result = await request.app.state.tools.execute(
        "list_workshop_slots", filters, conversation_id
    )
    entities = {"serviceTypeId": body.serviceTypeId}
    constraints = {}
    if body.dealershipId:
        constraints["dealershipId"] = body.dealershipId
    request.app.state.conversations.update_workflow_state(
        conversation_id,
        {
            **workflow_state(
                Goals.WORKSHOP_BOOK_SERVICE,
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


@router.post("/conversations/{conversation_id}/part-exchange-drafts")
async def prepare_part_exchange_draft(
    conversation_id: str, body: PartExchangeDraftRequest, request: Request
) -> dict:
    _authorize(request, conversation_id)
    return await _tool_view(
        request, conversation_id, "prepare_part_exchange", body.model_dump()
    )


@router.post("/conversations/{conversation_id}/part-exchange-estimates")
async def estimate_part_exchange(
    conversation_id: str, body: PartExchangeEstimateRequest, request: Request
) -> dict:
    _authorize(request, conversation_id)
    return await _tool_view(
        request, conversation_id, "estimate_part_exchange", body.model_dump()
    )


@router.post("/conversations/{conversation_id}/callback-drafts")
async def prepare_callback_draft(
    conversation_id: str, body: CallbackDraftRequest, request: Request
) -> dict:
    _authorize(request, conversation_id)
    return await _tool_view(
        request, conversation_id, "prepare_callback", body.model_dump()
    )


@router.post("/conversations/{conversation_id}/sales-enquiry-drafts")
async def prepare_sales_enquiry_draft(
    conversation_id: str, body: SalesEnquiryDraftRequest, request: Request
) -> dict:
    _authorize(request, conversation_id)
    return await _tool_view(
        request,
        conversation_id,
        "prepare_sales_enquiry",
        body.model_dump(exclude_none=True),
    )


@router.post("/conversations/{conversation_id}/vehicle-interest-drafts")
async def prepare_vehicle_interest_draft(
    conversation_id: str, body: VehicleInterestDraftRequest, request: Request
) -> dict:
    _authorize(request, conversation_id)
    return await _tool_view(
        request,
        conversation_id,
        "prepare_vehicle_interest",
        body.model_dump(exclude_none=True),
    )


@router.post("/conversations/{conversation_id}/dealership-message-drafts")
async def prepare_dealership_message_draft(
    conversation_id: str, body: DealershipMessageDraftRequest, request: Request
) -> dict:
    _authorize(request, conversation_id)
    return await _tool_view(
        request, conversation_id, "prepare_dealership_message", body.model_dump()
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
    result = await request.app.state.tools.execute(
        "prepare_workshop_amendment",
        body.model_dump(exclude_none=True),
        conversation_id,
    )
    if selected_slot and result.view_payload:
        summary = result.view_payload.setdefault("summary", {})
        summary["newAppointment"] = selected_slot.get("startsAt")
        summary["newDealership"] = (
            selected_slot.get("dealershipName") or selected_slot.get("dealershipTown")
        )
    return {"viewType": result.view_type, "view": result.view_payload}


@router.post("/conversations/{conversation_id}/workshop-amendment-options")
async def workshop_amendment_options(conversation_id: str, request: Request) -> dict:
    _authorize(request, conversation_id)
    result = await _workshop_amendment_options_result(request, conversation_id)
    return {"viewType": result.view_type, "view": result.view_payload}


@router.post("/conversations/{conversation_id}/drafts/{draft_id}/cancel")
async def cancel_draft(conversation_id: str, draft_id: str, request: Request) -> dict:
    _authorize(request, conversation_id)
    try:
        draft = request.app.state.workflow_repository.cancel(conversation_id, draft_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    kind = (
        "workshop_change_abandoned"
        if draft.get("kind") in {"workshop_amend", "workshop_cancel"}
        else "request_cancelled"
    )
    result = {"kind": kind, "status": "cancelled"}
    receipt_text = _receipt_text(kind)
    replaced = request.app.state.messages.replace_draft_with_receipt(
        conversation_id, draft_id, receipt_text, result
    )
    if not replaced:
        _persist_receipt_once(request, conversation_id, receipt_text, result)
    return {"status": "cancelled", "result": result}


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
        _workshop_state(Goals.WORKSHOP_FIND_BOOKING, "verified", booking),
    )
    if body.mode in {"amend", "cancel"}:
        return await _prepare_existing_workshop_action(
            request, conversation_id, body.mode
        )
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
    if not grant:
        raise HTTPException(status_code=409, detail="Verify the workshop booking again.")
    snapshot = json.loads(grant.get("booking_snapshot_json") or "{}")
    if snapshot.get("status") != "confirmed":
        raise HTTPException(
            status_code=409,
            detail="Only a confirmed workshop booking can be changed or cancelled.",
        )
    return await _prepare_existing_workshop_action(request, conversation_id, body.mode)
