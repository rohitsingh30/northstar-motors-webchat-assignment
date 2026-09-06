from types import SimpleNamespace

import pytest

from webchat.orchestration.tools.workflows import WorkflowToolHandler


class Drafts:
    def __init__(self):
        self.prepared = []
        self.expected_draft_ids = []
        self.clear_fields = []

    def prepare(
        self,
        conversation_id,
        kind,
        fields,
        *,
        expected_draft_id=None,
        clear_fields=(),
    ):
        assert conversation_id == "conversation-001"
        self.expected_draft_ids.append(expected_draft_id)
        self.clear_fields.append(tuple(clear_fields))
        self.prepared.append((kind, fields))
        return SimpleNamespace(
            id="draft-001",
            kind=kind,
            status="awaiting_confirmation",
            missing_fields=[],
            summary={**fields, "kind": kind, "contactProvided": True},
        )


class CollectingCallbackDrafts(Drafts):
    def prepare(self, conversation_id, kind, fields, *, expected_draft_id=None):
        assert conversation_id == "conversation-001"
        assert expected_draft_id is None
        self.prepared.append((kind, fields))
        return SimpleNamespace(
            id="draft-callback",
            kind=kind,
            status="collecting",
            missing_fields=[
                "dealershipId",
                "department",
                "reason",
                "firstName",
                "lastName",
                "email",
                "phone",
            ],
            summary=dict(fields),
        )


class CallbackDepartmentDrafts(Drafts):
    def prepare(self, conversation_id, kind, fields, *, expected_draft_id=None):
        assert conversation_id == "conversation-001"
        assert expected_draft_id is None
        self.prepared.append((kind, fields))
        return SimpleNamespace(
            id="draft-callback-department",
            kind=kind,
            status="collecting",
            missing_fields=[
                "department",
                "firstName",
                "lastName",
                "email",
                "phone",
            ],
            summary=dict(fields),
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
            "dealershipId": "northstar-stockport",
            "dealershipName": "Northstar Stockport",
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
            "dealershipId": "northstar-stockport",
            "dealershipName": "Northstar Stockport",
            "dealershipTown": None,
        }
    else:
        assert dealership.vehicle_requests == []
        assert "vehicle" not in result.view_payload


@pytest.mark.asyncio
async def test_test_drive_confirmation_enriches_vehicle_display_data() -> None:
    dealership = Dealership()

    result = await WorkflowToolHandler(dealership, Drafts()).execute(
        "prepare_test_drive",
        {
            "vehicleId": "veh-004",
            "slotId": "test-slot-001",
            "selectedStartsAt": "2026-09-12T10:00:00+01:00",
            "selectedDealershipId": "northstar-stockport",
            "selectedDealershipName": "Northstar Stockport",
        },
        "conversation-001",
    )

    assert dealership.vehicle_requests == ["veh-004"]
    assert result.view_payload["vehicle"]["id"] == "veh-004"
    assert result.view_payload["vehicle"]["make"] == "BMW"
    assert result.view_payload["vehicle"]["model"] == "i4"


@pytest.mark.asyncio
async def test_described_test_drive_replacement_clears_stale_vehicle_and_slot_fields() -> None:
    drafts = Drafts()

    result = await WorkflowToolHandler(Dealership(), drafts).execute(
        "prepare_test_drive",
        {
            "make": "MINI",
            "model": "Cooper",
            "dealershipTown": "Manchester",
            "dateFrom": "2026-09-07",
            "timeOfDay": "afternoon",
        },
        "conversation-001",
    )

    assert drafts.clear_fields == [
        (
            "vehicleId",
            "slotId",
            "selectedStartsAt",
            "selectedDealershipId",
            "selectedDealershipName",
        )
    ]
    assert drafts.prepared == [("test_drive", {})]
    assert result.view_payload["schedulingPreferences"] == {
        "dateFrom": "2026-09-07",
        "timeOfDay": "afternoon",
    }
    assert result.view_payload["vehicleSearchPreferences"] == {
        "make": "MINI",
        "model": "Cooper",
        "dealershipTown": "Manchester",
    }


@pytest.mark.asyncio
async def test_review_replacement_id_reaches_the_workflow_service() -> None:
    drafts = Drafts()

    await WorkflowToolHandler(Dealership(), drafts).execute(
        "prepare_test_drive",
        {"vehicleId": "veh-004", "slotId": "test-slot-001"},
        "conversation-001",
        replacement_draft_id="draft-under-review",
    )

    assert drafts.expected_draft_ids == ["draft-under-review"]


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


@pytest.mark.asyncio
async def test_callback_dealership_question_carries_authoritative_choices() -> None:
    result = await WorkflowToolHandler(Dealership(), CollectingCallbackDrafts()).execute(
        "prepare_callback",
        {},
        "conversation-001",
    )

    assert result.view_type == "draft"
    assert result.view_payload["missingPublicFields"] == ["dealershipId"]
    assert result.view_payload["collectionViewType"] == "choice_list"
    assert result.view_payload["choiceEntityType"] == "dealership"
    assert result.view_payload["collectionPresentation"] == {
        "schemaVersion": 1,
        "layout": "bullet_list",
        "purpose": "choice",
        "items": [
            {"label": "Northstar Bolton", "description": "Bolton"},
            {"label": "Northstar Stockport", "description": "Stockport"},
        ],
    }


@pytest.mark.asyncio
async def test_callback_department_question_uses_capability_specific_choices() -> None:
    result = await WorkflowToolHandler(Dealership(), CallbackDepartmentDrafts()).execute(
        "prepare_callback",
        {
            "dealershipId": "northstar-bolton",
            "reason": "Please call about my vehicle.",
        },
        "conversation-001",
    )

    assert result.view_payload["missingPublicFields"] == ["department"]
    assert result.view_payload["choiceField"] == "department"
    assert result.view_payload["choiceEntityType"] == "workflow_option"
    assert result.view_payload["items"] == [
        {"id": "sales", "name": "Sales"},
        {"id": "service", "name": "Service"},
        {"id": "parts", "name": "Parts"},
    ]
    labels = [
        item["label"]
        for item in result.view_payload["collectionPresentation"]["items"]
    ]
    assert labels == ["Sales", "Service", "Parts"]
    assert "General enquiries" not in labels
