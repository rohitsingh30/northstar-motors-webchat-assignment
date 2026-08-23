from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from webchat.integrations.dealership import DealershipClient, DealershipError
from webchat.persistence.repositories import WorkflowRepository

REQUIRED_FIELDS: dict[str, set[str]] = {
    "sales_enquiry": {
        "dealershipId", "enquiryType", "message", "firstName", "lastName", "email", "phone"
    },
    "test_drive": {
        "slotId", "vehicleId", "firstName", "lastName", "email", "phone"
    },
    "vehicle_interest": {"vehicleId", "firstName", "lastName", "email", "phone"},
    "callback": {
        "dealershipId", "department", "reason", "firstName", "lastName", "email", "phone"
    },
    "workshop_booking": {
        "slotId", "serviceTypeId", "dealershipId", "registration", "mileage",
        "firstName", "lastName", "email", "phone"
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
        self._creation_routes: dict[str, str] = {
            "sales_enquiry": "create_sales_enquiry",
            "test_drive": "create_test_drive",
            "callback": "create_callback",
            "workshop_booking": "create_workshop_booking",
            "dealership_message": "create_dealership_message",
            "part_exchange": "create_part_exchange",
        }

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
        if kind == "test_drive":
            return await self._create_test_drive(fields, idempotency_key)
        if kind == "workshop_booking":
            return await self._create_workshop_booking(fields, idempotency_key)
        creation_method = self._creation_routes.get(kind)
        if creation_method is not None:
            creation: Callable[[dict[str, Any], str], Awaitable[dict]] = getattr(
                self.dealership, creation_method
            )
            return await creation(fields, idempotency_key)
        if kind == "vehicle_interest":
            return await self._create_vehicle_interest(fields, idempotency_key)
        if kind not in {"workshop_amend", "workshop_cancel"}:
            raise ValueError("Unsupported workflow kind")
        return await self._mutate_verified_booking(kind, fields, conversation_id)

    async def _create_test_drive(
        self, fields: dict[str, Any], idempotency_key: str
    ) -> dict:
        vehicle_id = str(fields["vehicleId"])
        slot_id = str(fields["slotId"])
        availability = await self.dealership.get_vehicle_availability(vehicle_id)
        current_state = str(availability.get("availability") or "")
        if current_state != "available":
            code = "VEHICLE_RESERVED" if current_state == "reserved" else "VEHICLE_UNAVAILABLE"
            message = (
                "The vehicle is reserved and cannot be booked for a test drive."
                if current_state == "reserved"
                else "The vehicle is not available for a test drive."
            )
            raise DealershipError(
                409,
                code,
                message,
                recovery=self._vehicle_recovery(vehicle_id, current_state),
            )
        slots = await self.dealership.list_test_drive_slots({"vehicleId": vehicle_id})
        if not _contains_slot(slots, slot_id):
            raise self._slot_unavailable_error(
                "test_drive", slots, vehicle_id=vehicle_id
            )
        payload = {key: value for key, value in fields.items() if key != "vehicleId"}
        try:
            return await self.dealership.create_test_drive(payload, idempotency_key)
        except DealershipError as error:
            if error.code == "SLOT_UNAVAILABLE":
                fresh = await self.dealership.list_test_drive_slots(
                    {"vehicleId": vehicle_id}
                )
                raise self._slot_unavailable_error(
                    "test_drive", fresh, vehicle_id=vehicle_id
                ) from error
            if error.code in {"VEHICLE_RESERVED", "VEHICLE_UNAVAILABLE"}:
                state = "reserved" if error.code == "VEHICLE_RESERVED" else "unavailable"
                raise DealershipError(
                    error.status,
                    error.code,
                    str(error),
                    error.retryable,
                    error.field_errors,
                    self._vehicle_recovery(vehicle_id, state),
                ) from error
            raise

    async def _create_workshop_booking(
        self, fields: dict[str, Any], idempotency_key: str
    ) -> dict:
        slot_id = str(fields["slotId"])
        filters = {
            "serviceTypeId": fields["serviceTypeId"],
            "dealershipId": fields["dealershipId"],
        }
        slots = await self.dealership.list_workshop_slots(filters)
        if not _contains_slot(slots, slot_id):
            raise self._slot_unavailable_error("workshop", slots)
        payload = {
            key: value
            for key, value in fields.items()
            if key not in {"serviceTypeId", "dealershipId"}
        }
        try:
            return await self.dealership.create_workshop_booking(
                payload, idempotency_key
            )
        except DealershipError as error:
            if error.code == "SLOT_UNAVAILABLE":
                fresh = await self.dealership.list_workshop_slots(filters)
                raise self._slot_unavailable_error("workshop", fresh) from error
            raise

    @staticmethod
    def _slot_unavailable_error(
        journey: str,
        slots: dict[str, Any],
        *,
        vehicle_id: str | None = None,
    ) -> DealershipError:
        view_type = "test_drive_slot_picker" if journey == "test_drive" else "slot_list"
        view = {"version": 1, "items": list(slots.get("items", []))}
        if vehicle_id:
            view["vehicleId"] = vehicle_id
        return DealershipError(
            409,
            "SLOT_UNAVAILABLE",
            "That appointment is no longer available. Please choose another current time.",
            recovery={"journey": journey, "viewType": view_type, "view": view},
        )

    @staticmethod
    def _vehicle_recovery(vehicle_id: str, availability: str) -> dict[str, Any]:
        suggestions = [
            {
                "label": "Find another vehicle",
                "text": "Show me available vehicles",
            }
        ]
        if availability == "reserved":
            suggestions.insert(
                0,
                {
                    "label": "Register interest",
                    "text": "Register interest in this vehicle",
                    "action": {
                        "type": "start_vehicle_interest",
                        "vehicleId": vehicle_id,
                    },
                },
            )
        return {
            "journey": "test_drive",
            "vehicleId": vehicle_id,
            "availability": availability,
            "suggestions": suggestions,
        }

    async def _create_vehicle_interest(
        self, fields: dict[str, Any], idempotency_key: str
    ) -> dict:
        availability = await self.dealership.get_vehicle_availability(fields["vehicleId"])
        if availability.get("availability") != "reserved":
            raise DealershipError(
                409, "VEHICLE_NOT_RESERVED", "The vehicle is not reserved."
            )
        return await self.dealership.create_vehicle_interest(fields, idempotency_key)

    async def _mutate_verified_booking(
        self, kind: str, fields: dict[str, Any], conversation_id: str
    ) -> dict:
        grant = self.repository.get_grant(conversation_id, fields["verifiedGrantId"])
        if not grant:
            raise DealershipError(
                403,
                "BOOKING_VERIFICATION_REQUIRED",
                "Booking verification has expired.",
            )
        changes = {
            key: fields[key]
            for key in ("slotId", "mileage", "notes")
            if key in fields
        }
        try:
            if kind == "workshop_amend":
                result = await self.dealership.update_workshop_booking(
                    grant["booking_record_id"], changes
                )
            else:
                result = await self.dealership.cancel_workshop_booking(
                    grant["booking_record_id"]
                )
        except DealershipError as error:
            if not error.retryable:
                raise
            reconciled = await self._reconcile_workshop_mutation(
                kind, grant["booking_record_id"], changes
            )
            if reconciled is None:
                raise
            result = reconciled
        self.repository.revoke_grant(grant["id"])
        return result

    async def _reconcile_workshop_mutation(
        self, kind: str, booking_record_id: str, changes: dict[str, Any]
    ) -> dict[str, Any] | None:
        try:
            current = await self.dealership.get_workshop_booking(booking_record_id)
        except DealershipError:
            return None
        if kind == "workshop_cancel":
            return current if current.get("status") == "cancelled" else None
        if current.get("status") != "confirmed":
            return None
        return current if all(current.get(key) == value for key, value in changes.items()) else None

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


def _contains_slot(data: dict[str, Any], slot_id: str) -> bool:
    return any(str(item.get("id")) == slot_id for item in data.get("items", []))
