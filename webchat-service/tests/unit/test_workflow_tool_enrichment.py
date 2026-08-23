from types import SimpleNamespace

import pytest

from webchat.orchestration.tools.workflows import WorkflowToolHandler


class Drafts:
    def prepare(self, conversation_id, kind, fields):
        assert conversation_id == "conversation-001"
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
        return {"items": [{"id": "northstar-bolton", "name": "Northstar Bolton"}]}


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
