"""Test-drive, offer, part-exchange, callback, and enquiry preparation routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from webchat.domain.workflows import offer_enquiry_fields
from webchat.orchestration.state import workflow_state

from .dependencies import _authorize, _tool_view
from .models import (
    CallbackDraftRequest,
    DealershipMessageDraftRequest,
    OfferEnquiryOptionsRequest,
    PartExchangeDraftRequest,
    PartExchangeEstimateRequest,
    SalesEnquiryDraftRequest,
    TestDriveDraftRequest,
    TestDriveOptionsRequest,
    VehicleInterestDraftRequest,
)

router = APIRouter()

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
                "test_drive",
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
    return await _tool_view(request, conversation_id, "prepare_test_drive", body.model_dump())


@router.post("/conversations/{conversation_id}/offer-enquiry-options")
async def offer_enquiry_options(
    conversation_id: str, body: OfferEnquiryOptionsRequest, request: Request
) -> dict:
    _authorize(request, conversation_id)
    offer_result = await request.app.state.tools.execute(
        "get_offer", {"id": body.offerId}, conversation_id
    )
    offer = offer_result.facts if isinstance(offer_result.facts, dict) else {}
    return await _tool_view(
        request,
        conversation_id,
        "request_offer_enquiry_form",
        offer_enquiry_fields(offer),
    )

@router.post("/conversations/{conversation_id}/part-exchange-drafts")
async def prepare_part_exchange_draft(
    conversation_id: str, body: PartExchangeDraftRequest, request: Request
) -> dict:
    _authorize(request, conversation_id)
    return await _tool_view(request, conversation_id, "prepare_part_exchange", body.model_dump())


@router.post("/conversations/{conversation_id}/part-exchange-estimates")
async def estimate_part_exchange(
    conversation_id: str, body: PartExchangeEstimateRequest, request: Request
) -> dict:
    _authorize(request, conversation_id)
    return await _tool_view(request, conversation_id, "estimate_part_exchange", body.model_dump())


@router.post("/conversations/{conversation_id}/callback-drafts")
async def prepare_callback_draft(
    conversation_id: str, body: CallbackDraftRequest, request: Request
) -> dict:
    _authorize(request, conversation_id)
    return await _tool_view(request, conversation_id, "prepare_callback", body.model_dump())


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
