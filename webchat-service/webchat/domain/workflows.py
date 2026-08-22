from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from webchat.integrations.dealership import DealershipClient, DealershipError
from webchat.persistence.repositories import WorkflowRepository

REQUIRED_FIELDS: dict[str, set[str]] = {
    "sales_enquiry": {
        "dealershipId", "enquiryType", "message", "firstName", "lastName", "email", "phone"
    },
    "test_drive": {"slotId", "firstName", "lastName", "email", "phone"},
    "vehicle_interest": {"vehicleId", "firstName", "lastName", "email", "phone"},
    "callback": {
        "dealershipId", "department", "reason", "firstName", "lastName", "email", "phone"
    },
    "workshop_booking": {
        "slotId", "registration", "mileage", "firstName", "lastName", "email", "phone"
    },
    "workshop_amend": {"verifiedGrantId"},
    "workshop_cancel": {"verifiedGrantId"},
    "dealership_message": {
        "dealershipId", "department", "subject", "message", "preferredContactMethod",
        "firstName", "lastName", "email", "phone"
    },
    "part_exchange": {
        "dealershipId", "registration", "mileage", "condition", "firstName", "lastName",
        "email", "phone"
    },
}


def material_hash(kind: str, fields: dict[str, Any]) -> str:
    canonical = json.dumps({"kind": kind, "fields": fields}, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class PreparedDraft:
    id: str
    kind: str
    status: str
    missing_fields: list[str]
    summary: dict[str, Any]


class WorkflowService:
    def __init__(self, repository: WorkflowRepository, dealership: DealershipClient):
        self.repository = repository
        self.dealership = dealership

    def prepare(self, conversation_id: str, kind: str, fields: dict[str, Any]) -> PreparedDraft:
        if kind not in REQUIRED_FIELDS:
            raise ValueError("Unsupported workflow kind")
        cleaned = {key: value for key, value in fields.items() if value not in (None, "")}
        verified_snapshot: dict[str, Any] = {}
        if kind in {"workshop_amend", "workshop_cancel"} and "verifiedGrantId" not in cleaned:
            grant = self.repository.latest_grant(conversation_id)
            if grant:
                cleaned["verifiedGrantId"] = grant["id"]
                verified_snapshot = json.loads(grant.get("booking_snapshot_json") or "{}")
        missing = sorted(REQUIRED_FIELDS[kind] - cleaned.keys())
        if kind == "workshop_amend" and not any(key in cleaned for key in ("slotId", "mileage", "notes")):
            missing.append("one of slotId, mileage, or notes")
        status = "collecting" if missing else "awaiting_confirmation"
        draft = self.repository.create_or_replace(
            conversation_id, kind, cleaned, material_hash(kind, cleaned), status
        )
        summary = self.safe_summary(kind, cleaned)
        if verified_snapshot:
            summary.update(
                {
                    "bookingReference": verified_snapshot.get("reference"),
                    "currentAppointment": verified_snapshot.get("startsAt"),
                    "service": verified_snapshot.get("serviceTypeName"),
                    "dealership": verified_snapshot.get("dealershipName")
                    or verified_snapshot.get("dealershipTown"),
                }
            )
            summary = {key: value for key, value in summary.items() if value is not None}
        return PreparedDraft(draft["id"], kind, status, missing, summary)

    @staticmethod
    def safe_summary(kind: str, fields: dict[str, Any]) -> dict[str, Any]:
        # Contact details are deliberately summarized, not echoed into a confirmation card.
        contact_fields = {"firstName", "lastName", "email", "phone"}
        hidden = contact_fields | {"verifiedGrantId"}
        summary = {key: value for key, value in fields.items() if key not in hidden}
        summary["contactProvided"] = all(fields.get(key) for key in contact_fields)
        summary["kind"] = kind
        return summary

    async def confirm(self, conversation_id: str, draft_id: str, client_action_id: str) -> dict:
        attempt = self.repository.begin_confirmation(conversation_id, draft_id, client_action_id)
        if attempt["state"] == "succeeded":
            return json.loads(attempt["result_json"])
        if attempt["state"] == "failed":
            raise DealershipError(
                409,
                attempt.get("error_code") or "OPERATION_FAILED",
                "This confirmation was already processed and failed.",
                bool(attempt.get("retryable")),
            )
        draft = attempt["draft"]
        fields = json.loads(draft["fields_json"])
        try:
            platform_result = await self._execute(
                draft["kind"], fields, attempt["idempotency_key"], conversation_id
            )
        except DealershipError as error:
            self.repository.fail_attempt(
                attempt["id"], draft_id, error.code, error.retryable
            )
            raise
        result = self.public_receipt(draft["kind"], platform_result)
        self.repository.succeed_attempt(attempt["id"], draft_id, result)
        return result

    @staticmethod
    def public_receipt(kind: str, result: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "status",
            "reference",
            "slotId",
            "vehicleId",
            "dealershipId",
            "serviceTypeId",
            "startsAt",
            "estimateLowPence",
            "estimateHighPence",
            "estimateNotice",
            "idempotentReplay",
        }
        receipt = {key: value for key, value in result.items() if key in allowed}
        receipt["kind"] = kind
        return receipt

    async def _execute(
        self, kind: str, fields: dict[str, Any], idempotency_key: str, conversation_id: str
    ) -> dict:
        if kind == "sales_enquiry":
            return await self.dealership.create_sales_enquiry(fields, idempotency_key)
        if kind == "test_drive":
            return await self.dealership.create_test_drive(fields, idempotency_key)
        if kind == "vehicle_interest":
            availability = await self.dealership.get_vehicle_availability(fields["vehicleId"])
            if availability.get("availability") != "reserved":
                raise DealershipError(409, "VEHICLE_NOT_RESERVED", "The vehicle is not reserved.")
            return await self.dealership.create_vehicle_interest(fields, idempotency_key)
        if kind == "callback":
            return await self.dealership.create_callback(fields, idempotency_key)
        if kind == "workshop_booking":
            return await self.dealership.create_workshop_booking(fields, idempotency_key)
        if kind == "dealership_message":
            return await self.dealership.create_dealership_message(fields, idempotency_key)
        if kind == "part_exchange":
            return await self.dealership.create_part_exchange(fields, idempotency_key)
        grant = self.repository.get_grant(conversation_id, fields["verifiedGrantId"])
        if not grant:
            raise DealershipError(403, "BOOKING_VERIFICATION_REQUIRED", "Booking verification has expired.")
        if kind == "workshop_amend":
            changes = {key: fields[key] for key in ("slotId", "mileage", "notes") if key in fields}
            result = await self.dealership.update_workshop_booking(
                grant["booking_record_id"], changes
            )
            self.repository.revoke_grant(grant["id"])
            return result
        if kind == "workshop_cancel":
            result = await self.dealership.cancel_workshop_booking(grant["booking_record_id"])
            self.repository.revoke_grant(grant["id"])
            return result
        raise ValueError("Unsupported workflow kind")

    async def lookup_booking(self, conversation_id: str, proof: dict[str, Any]) -> dict:
        try:
            booking = await self.dealership.lookup_workshop_booking(proof)
        except DealershipError as error:
            if error.status == 404:
                raise DealershipError(404, "BOOKING_NOT_FOUND", "No workshop booking matched those details.") from error
            raise
        safe_snapshot = {
            key: booking.get(key)
            for key in (
                "reference",
                "slotId",
                "startsAt",
                "dealershipId",
                "dealershipName",
                "dealershipTown",
                "serviceTypeId",
                "serviceTypeName",
                "status",
            )
        }
        self.repository.create_grant(
            conversation_id, booking["id"], booking["reference"], safe_snapshot
        )
        return {"booking": safe_snapshot}
