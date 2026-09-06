import json
from pathlib import Path
from uuid import uuid4

import pytest

from webchat.domain.workflows import WorkflowService
from webchat.integrations.dealership import DealershipError
from webchat.persistence.database import Database
from webchat.persistence.repositories import ConversationRepository, WorkflowRepository
from webchat.persistence.repositories.workflows import StaleWorkflowDraftError


class FakeDealership:
    def __init__(self):
        self.calls = []
        self.availability = "available"
        self.test_drive_slots = [
            {
                "id": "td-slot-0001",
                "vehicleId": "veh-001",
                "startsAt": "2026-08-22T09:00:00Z",
                "dealershipId": "northstar-manchester",
                "dealershipName": "Northstar Manchester",
                "make": "BMW",
                "model": "i4",
            }
        ]
        self.workshop_slots = [
            {
                "id": "ws-slot-0001",
                "serviceTypeId": "full-service",
                "dealershipId": "northstar-manchester",
                "startsAt": "2026-08-22T10:00:00Z",
                "dealershipName": "Northstar Manchester",
                "serviceName": "Full service",
            }
        ]

    async def create_sales_enquiry(self, body, key):
        self.calls.append((body, key))
        return {"status": "received", "reference": "SALE-123"}

    async def get_vehicle_availability(self, vehicle_id):
        self.calls.append(("get_vehicle_availability", vehicle_id))
        return {"vehicleId": vehicle_id, "availability": self.availability}

    async def list_test_drive_slots(self, filters):
        self.calls.append(("list_test_drive_slots", filters))
        return {"items": self.test_drive_slots}

    async def create_test_drive(self, body, key):
        self.calls.append(("create_test_drive", body, key))
        return {
            "status": "confirmed",
            "reference": "TEST-123",
            "slotId": body["slotId"],
        }

    async def list_workshop_slots(self, filters):
        self.calls.append(("list_workshop_slots", filters))
        return {"items": self.workshop_slots}

    async def create_workshop_booking(self, body, key):
        self.calls.append(("create_workshop_booking", body, key))
        return {
            "id": "wsb-001",
            "status": "confirmed",
            "reference": "WORK-123",
            "slotId": body["slotId"],
        }

    async def lookup_workshop_booking(self, proof):
        return {
            "id": "wsb-internal-001",
            "reference": proof["reference"],
            "slotId": "ws-slot-0001",
            "startsAt": "2026-08-22T09:00:00Z",
            "dealershipId": "northstar-manchester",
            "dealershipName": "Northstar Manchester",
            "dealershipTown": "Manchester",
            "serviceTypeId": "full-service",
            "serviceTypeName": "Full service",
            "status": "confirmed",
        }


def setup(tmp_path: Path):
    database = Database(tmp_path / "webchat.sqlite3")
    database.migrate()
    conversations = ConversationRepository(database)
    conversation = conversations.create("token", {"path": "/"}, 30)
    repository = WorkflowRepository(database)
    dealership = FakeDealership()
    dealership.calls = []
    return conversation["id"], WorkflowService(repository, dealership), dealership


def test_review_replacement_consumes_the_expected_draft_once(tmp_path: Path) -> None:
    conversation_id, workflows, _ = setup(tmp_path)
    fields = {
        **contact(),
        "vehicleId": "veh-001",
        "slotId": "td-slot-0001",
    }
    reviewed = workflows.prepare(conversation_id, "test_drive", fields)

    replacement = workflows.prepare(
        conversation_id,
        "test_drive",
        {**fields, "lastName": "Morgan"},
        expected_draft_id=reviewed.id,
    )

    assert workflows.repository.statuses(conversation_id, [reviewed.id, replacement.id]) == {
        reviewed.id: "cancelled",
        replacement.id: "awaiting_confirmation",
    }
    with pytest.raises(StaleWorkflowDraftError):
        workflows.prepare(
            conversation_id,
            "test_drive",
            {**fields, "lastName": "Taylor"},
            expected_draft_id=reviewed.id,
        )
    assert workflows.repository.latest_active(conversation_id)["id"] == replacement.id


def test_reselecting_a_transaction_subject_clears_only_dependent_draft_fields(
    tmp_path: Path,
) -> None:
    conversation_id, workflows, _ = setup(tmp_path)
    first = workflows.prepare(
        conversation_id,
        "test_drive",
        {
            **contact(),
            "vehicleId": "veh-042",
            "slotId": "td-slot-0001",
            "selectedStartsAt": "2026-09-07T09:00:00Z",
            "selectedDealershipId": "northstar-stockport",
            "selectedDealershipName": "Northstar Stockport",
        },
    )

    replacement = workflows.prepare(
        conversation_id,
        "test_drive",
        {},
        clear_fields=(
            "vehicleId",
            "slotId",
            "selectedStartsAt",
            "selectedDealershipId",
            "selectedDealershipName",
        ),
    )

    assert replacement.id != first.id
    assert replacement.status == "collecting"
    assert "vehicleId" not in replacement.summary
    assert "slotId" not in replacement.summary
    persisted = workflows.repository.latest_active(conversation_id, "test_drive")
    fields = json.loads(persisted["fields_json"])
    assert fields == contact()


@pytest.mark.asyncio
async def test_write_requires_complete_draft_and_confirmation(tmp_path: Path) -> None:
    conversation_id, workflows, dealership = setup(tmp_path)
    incomplete = workflows.prepare(
        conversation_id, "sales_enquiry", {"dealershipId": "northstar-bolton"}
    )
    assert incomplete.status == "collecting"
    with pytest.raises(ValueError):
        await workflows.confirm(conversation_id, incomplete.id, str(uuid4()))

    complete = workflows.prepare(
        conversation_id,
        "sales_enquiry",
        {
            "dealershipId": "northstar-bolton",
            "enquiryType": "general",
            "message": "Please contact me",
            "firstName": "Jamie",
            "lastName": "Taylor",
            "email": "jamie@example.com",
            "phone": "07700900123",
        },
    )
    action_id = str(uuid4())
    first = await workflows.confirm(conversation_id, complete.id, action_id)
    replay = await workflows.confirm(conversation_id, complete.id, action_id)

    assert (
        first
        == replay
        == {
            "status": "received",
            "reference": "SALE-123",
            "kind": "sales_enquiry",
        }
    )
    assert len(dealership.calls) == 1
    assert dealership.calls[0][1]


@pytest.mark.asyncio
async def test_confirmation_rejects_crossed_action_and_draft_kinds(tmp_path: Path) -> None:
    conversation_id, workflows, _ = setup(tmp_path)
    first_draft = workflows.prepare(
        conversation_id,
        "sales_enquiry",
        {
            "dealershipId": "northstar-bolton",
            "enquiryType": "general",
            "message": "Please contact me",
            **contact(),
        },
    )
    second_draft = workflows.prepare(
        conversation_id,
        "test_drive",
        {
            **contact(),
            "vehicleId": "veh-001",
            "slotId": "td-slot-0001",
        },
    )
    action_id = str(uuid4())
    await workflows.confirm(conversation_id, first_draft.id, action_id, "sales_enquiry")

    with pytest.raises(ValueError, match="Confirmation action does not match this draft"):
        await workflows.confirm(conversation_id, second_draft.id, action_id, "test_drive")

    fresh_draft = workflows.prepare(
        conversation_id,
        "test_drive",
        {
            **contact(),
            "vehicleId": "veh-001",
            "slotId": "td-slot-0001",
        },
    )
    with pytest.raises(ValueError, match="Draft kind does not match this confirmation"):
        await workflows.confirm(
            conversation_id,
            fresh_draft.id,
            str(uuid4()),
            "workshop_cancel",
        )


def test_contact_is_not_echoed_in_confirmation_summary(tmp_path: Path) -> None:
    conversation_id, workflows, _ = setup(tmp_path)
    draft = workflows.prepare(
        conversation_id,
        "test_drive",
        {
            "slotId": "tds-001",
            "vehicleId": "veh-001",
            "firstName": "Jamie",
            "lastName": "Taylor",
            "email": "jamie@example.com",
            "phone": "07700900123",
        },
    )

    assert draft.summary == {
        "slotId": "tds-001",
        "vehicleId": "veh-001",
        "contactProvided": True,
        "kind": "test_drive",
    }


@pytest.mark.asyncio
async def test_booking_proof_is_not_persisted_or_exposed(tmp_path: Path) -> None:
    conversation_id, workflows, _ = setup(tmp_path)
    proof = {
        "reference": "WORK-10001",
        "lastName": "PrivateSurname",
        "registration": "AB12 CDE",
        "phone": "07700900123",
    }

    result = await workflows.lookup_booking(conversation_id, proof)
    cancellation = workflows.prepare(conversation_id, "workshop_cancel", {})

    assert result == {
        "booking": {
            "reference": "WORK-10001",
            "slotId": "ws-slot-0001",
            "startsAt": "2026-08-22T09:00:00Z",
            "dealershipId": "northstar-manchester",
            "dealershipName": "Northstar Manchester",
            "dealershipTown": "Manchester",
            "serviceTypeId": "full-service",
            "serviceTypeName": "Full service",
            "status": "confirmed",
        }
    }
    assert cancellation.status == "awaiting_confirmation"
    assert "verifiedGrantId" not in cancellation.summary
    database_bytes = (tmp_path / "webchat.sqlite3").read_bytes()
    assert b"PrivateSurname" not in database_bytes
    assert b"AB12 CDE" not in database_bytes
    assert b"07700900123" not in database_bytes


def contact() -> dict:
    return {
        "firstName": "Jamie",
        "lastName": "Taylor",
        "email": "jamie@example.com",
        "phone": "07700900123",
    }


@pytest.mark.asyncio
async def test_new_bookings_recheck_selected_vehicle_and_slots_before_creation(
    tmp_path: Path,
) -> None:
    conversation_id, workflows, dealership = setup(tmp_path)
    test_drive = workflows.prepare(
        conversation_id,
        "test_drive",
        {
            **contact(),
            "vehicleId": "veh-001",
            "slotId": "td-slot-0001",
            "selectedStartsAt": "2026-08-22T09:00:00Z",
            "selectedDealershipId": "northstar-manchester",
            "selectedDealershipName": "Northstar Manchester",
        },
    )
    workshop = workflows.prepare(
        conversation_id,
        "workshop_booking",
        {
            **contact(),
            "slotId": "ws-slot-0001",
            "serviceTypeId": "full-service",
            "dealershipId": "northstar-manchester",
            "registration": "AB12 CDE",
            "mileage": 42000,
            "selectedStartsAt": "2026-08-22T10:00:00Z",
            "selectedDealershipId": "northstar-manchester",
            "selectedDealershipName": "Northstar Manchester",
            "selectedServiceTypeId": "full-service",
            "selectedServiceName": "Full service",
        },
    )

    test_drive_result = await workflows.confirm(conversation_id, test_drive.id, str(uuid4()))
    workshop_result = await workflows.confirm(conversation_id, workshop.id, str(uuid4()))

    assert test_drive_result["status"] == "confirmed"
    assert workshop_result["status"] == "confirmed"
    assert test_drive_result["vehicleLabel"] == "BMW i4"
    assert test_drive_result["startsAt"] == "2026-08-22T09:00:00Z"
    assert test_drive_result["dealershipName"] == "Northstar Manchester"
    assert workshop_result["serviceName"] == "Full service"
    assert workshop_result["startsAt"] == "2026-08-22T10:00:00Z"
    assert workshop_result["dealershipName"] == "Northstar Manchester"
    grant = workflows.repository.latest_grant(conversation_id)
    assert grant is not None
    assert grant["booking_record_id"] == "wsb-001"
    assert workflows.repository.workshop_proof_for_receipt(
        conversation_id, "WORK-123"
    ) == {
        "reference": "WORK-123",
        "lastName": "Taylor",
        "registration": "AB12 CDE",
        "phone": "07700900123",
    }
    assert ("get_vehicle_availability", "veh-001") in dealership.calls
    assert ("list_test_drive_slots", {"vehicleId": "veh-001"}) in dealership.calls
    assert (
        "list_workshop_slots",
        {
            "serviceTypeId": "full-service",
            "dealershipId": "northstar-manchester",
        },
    ) in dealership.calls
    test_create = next(call for call in dealership.calls if call[0] == "create_test_drive")
    workshop_create = next(
        call for call in dealership.calls if call[0] == "create_workshop_booking"
    )
    assert "vehicleId" not in test_create[1]
    assert not any(key.startswith("selected") for key in test_create[1])
    assert "serviceTypeId" not in workshop_create[1]
    assert "dealershipId" not in workshop_create[1]
    assert not any(key.startswith("selected") for key in workshop_create[1])


@pytest.mark.asyncio
async def test_booking_rechecks_return_vehicle_and_fresh_slot_recovery(
    tmp_path: Path,
) -> None:
    conversation_id, workflows, dealership = setup(tmp_path)
    dealership.availability = "reserved"
    test_drive = workflows.prepare(
        conversation_id,
        "test_drive",
        {
            **contact(),
            "vehicleId": "veh-007",
            "slotId": "td-slot-0007",
        },
    )

    with pytest.raises(DealershipError) as reserved:
        await workflows.confirm(conversation_id, test_drive.id, str(uuid4()))

    assert reserved.value.code == "VEHICLE_RESERVED"
    assert reserved.value.recovery["suggestions"][0]["action"] == {
        "type": "start_vehicle_interest",
        "vehicleId": "veh-007",
    }

    dealership.availability = "available"
    dealership.workshop_slots = [
        {
            "id": "ws-slot-0002",
            "serviceTypeId": "full-service",
            "dealershipId": "northstar-manchester",
        }
    ]
    workshop = workflows.prepare(
        conversation_id,
        "workshop_booking",
        {
            **contact(),
            "slotId": "ws-slot-0001",
            "serviceTypeId": "full-service",
            "dealershipId": "northstar-manchester",
            "registration": "AB12 CDE",
            "mileage": 42000,
        },
    )

    with pytest.raises(DealershipError) as unavailable:
        await workflows.confirm(conversation_id, workshop.id, str(uuid4()))

    assert unavailable.value.code == "SLOT_UNAVAILABLE"
    assert unavailable.value.recovery["journey"] == "workshop"
    assert unavailable.value.recovery["view"]["items"][0]["id"] == "ws-slot-0002"
    assert unavailable.value.recovery["alternativeOffer"]["reasonCode"] == (
        "selected_appointment_no_longer_available"
    )
    assert unavailable.value.recovery["alternativeOffer"]["candidateReferences"] == [
        "appointment:ws-slot-0002"
    ]


@pytest.mark.asyncio
async def test_atomic_slot_conflict_refreshes_test_drive_options(
    tmp_path: Path,
) -> None:
    class RacingDealership(FakeDealership):
        def __init__(self):
            super().__init__()
            self.slot_reads = 0

        async def list_test_drive_slots(self, filters):
            self.slot_reads += 1
            items = (
                [{"id": "td-slot-0001", "vehicleId": "veh-001"}]
                if self.slot_reads == 1
                else [{"id": "td-slot-0002", "vehicleId": "veh-001"}]
            )
            return {"items": items}

        async def create_test_drive(self, body, key):
            raise DealershipError(409, "SLOT_UNAVAILABLE", "That test-drive slot is unavailable.")

    database = Database(tmp_path / "webchat.sqlite3")
    database.migrate()
    conversations = ConversationRepository(database)
    conversation_id = conversations.create("token", {"path": "/"}, 30)["id"]
    dealership = RacingDealership()
    workflows = WorkflowService(WorkflowRepository(database), dealership)
    draft = workflows.prepare(
        conversation_id,
        "test_drive",
        {
            **contact(),
            "vehicleId": "veh-001",
            "slotId": "td-slot-0001",
        },
    )

    with pytest.raises(DealershipError) as unavailable:
        await workflows.confirm(conversation_id, draft.id, str(uuid4()))

    assert unavailable.value.code == "SLOT_UNAVAILABLE"
    assert unavailable.value.recovery["view"]["items"] == [
        {"id": "td-slot-0002", "vehicleId": "veh-001"}
    ]
    assert unavailable.value.recovery["alternativeOffer"]["requiresCustomerAcceptance"] is True
    assert dealership.slot_reads == 2


@pytest.mark.asyncio
async def test_retryable_confirmation_reuses_the_same_upstream_idempotency_key(
    tmp_path: Path,
) -> None:
    class RetryDealership(FakeDealership):
        def __init__(self):
            super().__init__()
            self.keys = []

        async def create_test_drive(self, body, key):
            self.keys.append(key)
            if len(self.keys) == 1:
                raise DealershipError(503, "PLATFORM_TIMEOUT", "The platform timed out.", True)
            return {
                "status": "confirmed",
                "reference": "TEST-RETRY",
                "slotId": body["slotId"],
            }

    database = Database(tmp_path / "webchat.sqlite3")
    database.migrate()
    conversations = ConversationRepository(database)
    conversation_id = conversations.create("token", {"path": "/"}, 30)["id"]
    dealership = RetryDealership()
    workflows = WorkflowService(WorkflowRepository(database), dealership)
    draft = workflows.prepare(
        conversation_id,
        "test_drive",
        {
            **contact(),
            "vehicleId": "veh-001",
            "slotId": "td-slot-0001",
        },
    )
    action_id = str(uuid4())

    with pytest.raises(DealershipError) as first:
        await workflows.confirm(conversation_id, draft.id, action_id)
    result = await workflows.confirm(conversation_id, draft.id, str(uuid4()))

    assert first.value.retryable is True
    assert result["reference"] == "TEST-RETRY"
    assert dealership.keys[0] == dealership.keys[1]


@pytest.mark.asyncio
async def test_ambiguous_workshop_amendment_is_reconciled_before_retry(
    tmp_path: Path,
) -> None:
    class AmbiguousDealership(FakeDealership):
        def __init__(self):
            super().__init__()
            self.record = {
                "status": "confirmed",
                "reference": "WORK-10001",
                "slotId": "ws-slot-0001",
                "mileage": 42000,
            }
            self.update_calls = 0

        async def update_workshop_booking(self, record_id, changes):
            self.update_calls += 1
            self.record.update(changes)
            raise DealershipError(503, "PLATFORM_TIMEOUT", "The platform timed out.", True)

        async def get_workshop_booking(self, record_id):
            return dict(self.record)

    database = Database(tmp_path / "webchat.sqlite3")
    database.migrate()
    conversations = ConversationRepository(database)
    conversation_id = conversations.create("token", {"path": "/"}, 30)["id"]
    dealership = AmbiguousDealership()
    workflows = WorkflowService(WorkflowRepository(database), dealership)
    await workflows.lookup_booking(
        conversation_id,
        {
            "reference": "WORK-10001",
            "lastName": "Taylor",
            "registration": "AB12 CDE",
            "phone": "07700900123",
        },
    )
    draft = workflows.prepare(
        conversation_id,
        "workshop_amend",
        {
            "slotId": "ws-slot-0002",
            "mileage": 43000,
            "selectedStartsAt": "2026-08-29T09:00:00Z",
            "selectedDealershipId": "northstar-stockport",
            "selectedDealershipName": "Northstar Stockport",
            "selectedServiceTypeId": "full-service",
            "selectedServiceName": "Full service",
        },
    )

    result = await workflows.confirm(conversation_id, draft.id, str(uuid4()))

    assert result["status"] == "confirmed"
    assert result["reference"] == "WORK-10001"
    assert result["kind"] == "workshop_amend"
    assert result["slotId"] == "ws-slot-0002"
    assert result["startsAt"] == "2026-08-29T09:00:00Z"
    assert result["dealershipName"] == "Northstar Stockport"
    assert result["serviceName"] == "Full service"
    assert dealership.update_calls == 1


@pytest.mark.asyncio
async def test_ambiguous_workshop_cancellation_is_reconciled_before_retry(
    tmp_path: Path,
) -> None:
    class AmbiguousDealership(FakeDealership):
        def __init__(self):
            super().__init__()
            self.record = {
                "status": "confirmed",
                "reference": "WORK-10001",
                "slotId": "ws-slot-0001",
            }
            self.cancel_calls = 0

        async def cancel_workshop_booking(self, record_id):
            self.cancel_calls += 1
            self.record["status"] = "cancelled"
            raise DealershipError(503, "PLATFORM_TIMEOUT", "The platform timed out.", True)

        async def get_workshop_booking(self, record_id):
            return dict(self.record)

    database = Database(tmp_path / "webchat.sqlite3")
    database.migrate()
    conversations = ConversationRepository(database)
    conversation_id = conversations.create("token", {"path": "/"}, 30)["id"]
    dealership = AmbiguousDealership()
    workflows = WorkflowService(WorkflowRepository(database), dealership)
    await workflows.lookup_booking(
        conversation_id,
        {
            "reference": "WORK-10001",
            "lastName": "Taylor",
            "registration": "AB12 CDE",
            "phone": "07700900123",
        },
    )
    draft = workflows.prepare(conversation_id, "workshop_cancel", {})

    result = await workflows.confirm(conversation_id, draft.id, str(uuid4()))

    assert result["status"] == "cancelled"
    assert result["reference"] == "WORK-10001"
    assert result["kind"] == "workshop_cancel"
    assert dealership.cancel_calls == 1
