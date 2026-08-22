from pathlib import Path
from uuid import uuid4

import pytest

from webchat.domain.workflows import WorkflowService
from webchat.persistence.database import Database
from webchat.persistence.repositories import ConversationRepository, WorkflowRepository


class FakeDealership:
    def __init__(self):
        self.calls = []

    async def create_sales_enquiry(self, body, key):
        self.calls.append((body, key))
        return {"status": "received", "reference": "SALE-123"}

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


@pytest.mark.asyncio
async def test_write_requires_complete_draft_and_confirmation(tmp_path: Path) -> None:
    conversation_id, workflows, dealership = setup(tmp_path)
    incomplete = workflows.prepare(conversation_id, "sales_enquiry", {"dealershipId": "northstar-bolton"})
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

    assert first == replay == {
        "status": "received",
        "reference": "SALE-123",
        "kind": "sales_enquiry",
    }
    assert len(dealership.calls) == 1
    assert dealership.calls[0][1]


def test_contact_is_not_echoed_in_confirmation_summary(tmp_path: Path) -> None:
    conversation_id, workflows, _ = setup(tmp_path)
    draft = workflows.prepare(
        conversation_id,
        "test_drive",
        {
            "slotId": "tds-001",
            "firstName": "Jamie",
            "lastName": "Taylor",
            "email": "jamie@example.com",
            "phone": "07700900123",
        },
    )

    assert draft.summary == {
        "slotId": "tds-001",
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
