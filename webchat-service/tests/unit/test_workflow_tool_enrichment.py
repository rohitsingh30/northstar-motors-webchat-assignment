from types import SimpleNamespace

import pytest

from webchat.orchestration.tools.workflows import WorkflowToolHandler


class Drafts:
    def __init__(self):
        self.prepared = []

    def prepare(self, conversation_id, kind, fields):
        assert conversation_id == "conversation-001"
        self.prepared.append((kind, fields))
        return SimpleNamespace(
            id="draft-001",
            kind=kind,
            status="awaiting_confirmation",
            missing_fields=[],
            summary={**fields, "kind": kind, "contactProvided": True},
        )


class Dealership:
    def __init__(self):
        self.vehicle_requests = []

    async def get_business_information(self):
        return {"privacyContact": "privacy@northstarmotors.example"}

    async def get_vehicle(self, vehicle_id):
        self.vehicle_requests.append(vehicle_id)
        return {
            "id": vehicle_id,
            "year": 2026,
            "make": "BMW",
            "model": "i4",
            "variant": "eDrive40 M Sport",
        }

    async def list_dealerships(self):
        return {
            "items": [
                {
                    "id": "northstar-bolton",
                    "name": "Northstar Bolton",
                    "town": "Bolton",
                },
                {
                    "id": "northstar-stockport",
                    "name": "Northstar Stockport",
                    "town": "Stockport",
                },
            ]
        }


@pytest.mark.asyncio
async def test_offer_enquiry_form_preview_does_not_persist_a_draft() -> None:
    drafts = Drafts()

    result = await WorkflowToolHandler(Dealership(), drafts).execute(
        "request_offer_enquiry_form",
        {
            "vehicleId": "veh-004",
            "enquiryType": "finance",
            "message": "I am interested in this offer.",
        },
        "conversation-001",
    )

    assert drafts.prepared == []
    assert result.view_type == "draft"
    assert result.view_payload["kind"] == "sales_enquiry"
    assert "draftId" not in result.view_payload
    assert result.view_payload["vehicle"]["id"] == "veh-004"


@pytest.mark.asyncio
@pytest.mark.parametrize("vehicle_id", ["veh-004", None])
async def test_sales_enquiry_reuses_vehicle_interest_enrichment(vehicle_id) -> None:
    dealership = Dealership()
    fields = {
        "dealershipId": "northstar-bolton",
        "enquiryType": "general",
        "message": "I want to discuss the BMW i4.",
    }
    if vehicle_id:
        fields["vehicleId"] = vehicle_id

    result = await WorkflowToolHandler(dealership, Drafts()).execute(
        "prepare_sales_enquiry",
        fields,
        "conversation-001",
    )

    if vehicle_id:
        assert dealership.vehicle_requests == [vehicle_id]
        assert result.view_payload["vehicle"] == {
            "id": "veh-004",
            "year": 2026,
            "make": "BMW",
            "model": "i4",
            "variant": "eDrive40 M Sport",
            "pricePence": None,
            "mileage": None,
            "fuelType": None,
            "transmission": None,
            "availability": None,
            "dealershipTown": None,
        }
    else:
        assert dealership.vehicle_requests == []
        assert "vehicle" not in result.view_payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    [
        "prepare_callback",
        "prepare_sales_enquiry",
        "prepare_dealership_message",
        "prepare_part_exchange",
    ],
)
async def test_dealership_forms_resolve_town_to_live_id(tool_name) -> None:
    drafts = Drafts()

    result = await WorkflowToolHandler(Dealership(), drafts).execute(
        tool_name,
        {"dealershipTown": "Stockport", "reason": "Please call about this vehicle"},
        "conversation-001",
    )

    assert drafts.prepared[0][1]["dealershipId"] == "northstar-stockport"
    assert "dealershipTown" not in drafts.prepared[0][1]
    assert result.view_payload["summary"]["dealershipId"] == "northstar-stockport"


@pytest.mark.asyncio
async def test_unknown_or_stale_dealership_is_left_unselected() -> None:
    drafts = Drafts()

    result = await WorkflowToolHandler(Dealership(), drafts).execute(
        "prepare_callback",
        {
            "dealershipTown": "Unknown town",
            "dealershipId": "stale-dealership",
            "reason": "Please call",
        },
        "conversation-001",
    )

    assert "dealershipId" not in drafts.prepared[0][1]
    assert "dealershipId" not in result.view_payload["summary"]
