from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from webchat.domain.capabilities import CapabilityRegistry
from webchat.integrations.dealership import DealershipClient, DealershipError
from webchat.persistence.repositories import WorkflowRepository

REQUIRED_FIELDS: dict[str, set[str]] = {
    kind: set(spec.required_fields)
    for kind, spec in CapabilityRegistry.SPECS.items()
    if kind not in {"booking_lookup", "part_exchange_estimate"}
} | {
    "workshop_amend": {"verifiedGrantId"},
    "workshop_cancel": {"verifiedGrantId"},
}


def offer_enquiry_fields(offer: dict[str, Any]) -> dict[str, Any]:
    """Build a sales-enquiry prefill from an authoritative selected offer."""
    label = " ".join(
        str(offer.get(field) or "").strip() for field in ("make", "model", "productType")
    ).strip()
    fields: dict[str, Any] = {
        "enquiryType": "finance",
        "message": (
            f"I am interested in the currently published {label} offer."
            if label
            else "I am interested in this currently published offer."
        ),
    }
    vehicle_id = offer.get("vehicleId")
    if (
        isinstance(vehicle_id, str)
        and len(vehicle_id) == 7
        and vehicle_id.startswith("veh-")
        and vehicle_id[4:].isdigit()
    ):
        fields["vehicleId"] = vehicle_id
    return fields


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

    def prepare(
        self,
        conversation_id: str,
        kind: str,
        fields: dict[str, Any],
        *,
        expected_draft_id: str | None = None,
        clear_fields: tuple[str, ...] = (),
    ) -> PreparedDraft:
        if kind not in REQUIRED_FIELDS:
            raise ValueError("Unsupported workflow kind")
        supplied = {key: value for key, value in fields.items() if value not in (None, "")}
        previous = self.repository.latest_active(conversation_id, kind)
        previous_fields = (
            json.loads(previous.get("fields_json") or "{}") if previous is not None else {}
        )
        allowed_clear_fields = {
            "vehicleId",
            "slotId",
            "selectedStartsAt",
            "selectedDealershipId",
            "selectedDealershipName",
            "selectedServiceTypeId",
            "selectedServiceName",
            "serviceTypeId",
            "dealershipId",
        }
        if not set(clear_fields).issubset(allowed_clear_fields):
            raise ValueError("Unsupported workflow field reset")
        retained = dict(previous_fields) if isinstance(previous_fields, dict) else {}
        for field in clear_fields:
            retained.pop(field, None)
        cleaned = {
            **retained,
            **supplied,
        }
        # A trusted appointment slot owns its dealership. Requiring a second dealership
        # selection after the slot was chosen creates contradictory workflow state.
        if kind == "workshop_booking" and cleaned.get("selectedDealershipId"):
            if cleaned.get("dealershipId") not in {None, cleaned["selectedDealershipId"]}:
                raise ValueError("the selected appointment belongs to another dealership")
            cleaned["dealershipId"] = cleaned["selectedDealershipId"]
        verified_snapshot: dict[str, Any] = {}
        if kind in {"workshop_amend", "workshop_cancel"} and "verifiedGrantId" not in cleaned:
            grant = self.repository.latest_grant(conversation_id)
            if grant:
                cleaned["verifiedGrantId"] = grant["id"]
                verified_snapshot = json.loads(grant.get("booking_snapshot_json") or "{}")
        missing = sorted(REQUIRED_FIELDS[kind] - cleaned.keys())
        if kind == "workshop_amend" and not any(
            key in cleaned for key in ("slotId", "mileage", "notes")
        ):
            missing.append("one of slotId, mileage, or notes")
        status = "collecting" if missing else "awaiting_confirmation"
        draft = self.repository.create_or_replace(
            conversation_id,
            kind,
            cleaned,
            material_hash(kind, cleaned),
            status,
            expected_draft_id=expected_draft_id,
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

    def cancel_active(self, conversation_id: str) -> dict[str, Any]:
        draft = self.repository.latest_active(conversation_id)
        if draft is None:
            return {"cancelled": False}
        cancelled = self.repository.cancel(conversation_id, str(draft["id"]))
        return {"cancelled": True, "kind": cancelled["kind"]}

    @staticmethod
    def safe_summary(kind: str, fields: dict[str, Any]) -> dict[str, Any]:
        # Persist public conversational values and trusted operational selectors only. Contact,
        # registration, mileage, condition, and booking proof stay in the secure channel.
        contact_fields = {"firstName", "lastName", "email", "phone"}
        allowed_by_kind = {
            "sales_enquiry": {"vehicleId", "dealershipId", "enquiryType", "message"},
            "test_drive": {
                "vehicleId",
                "slotId",
                "selectedStartsAt",
                "selectedDealershipId",
                "selectedDealershipName",
            },
            "vehicle_interest": {"vehicleId", "notes"},
            "callback": {"dealershipId", "department", "reason", "preferredTime", "vehicleId"},
            "workshop_booking": {
                "slotId",
                "serviceTypeId",
                "dealershipId",
                "notes",
                "selectedStartsAt",
                "selectedDealershipId",
                "selectedDealershipName",
                "selectedServiceTypeId",
                "selectedServiceName",
            },
            "workshop_amend": {
                "slotId",
                "selectedStartsAt",
                "selectedDealershipId",
                "selectedDealershipName",
                "selectedServiceTypeId",
                "selectedServiceName",
            },
            "workshop_cancel": set(),
            "dealership_message": {
                "dealershipId",
                "department",
                "subject",
                "message",
                "preferredContactMethod",
            },
            "part_exchange": {"dealershipId"},
        }
        summary = {
            key: value
            for key, value in fields.items()
            if key in allowed_by_kind.get(kind, set())
        }
        summary["contactProvided"] = all(fields.get(key) for key in contact_fields)
        summary["kind"] = kind
        return summary

    async def confirm(
        self,
        conversation_id: str,
        draft_id: str,
        client_action_id: str,
        expected_kind: str | None = None,
    ) -> dict:
        attempt = self.repository.begin_confirmation(
            conversation_id,
            draft_id,
            client_action_id,
            expected_kind,
        )
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
            self.repository.fail_attempt(attempt["id"], draft_id, error.code, error.retryable)
            raise
        if (
            draft["kind"] == "workshop_booking"
            and platform_result.get("id")
            and platform_result.get("reference")
        ):
            snapshot = {
                "reference": platform_result.get("reference"),
                "slotId": platform_result.get("slotId"),
                "startsAt": platform_result.get("startsAt"),
                "dealershipId": platform_result.get("dealershipId"),
                "dealershipName": platform_result.get("dealershipName"),
                "serviceTypeId": platform_result.get("serviceTypeId"),
                "serviceTypeName": platform_result.get("serviceName"),
                "status": platform_result.get("status"),
            }
            self.repository.create_grant(
                conversation_id,
                str(platform_result["id"]),
                str(platform_result["reference"]),
                {key: value for key, value in snapshot.items() if value is not None},
            )
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
            "dealershipName",
            "vehicleLabel",
            "serviceName",
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

    async def _create_test_drive(self, fields: dict[str, Any], idempotency_key: str) -> dict:
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
        selected_slot = _find_slot(slots, slot_id)
        if selected_slot is None:
            raise self._slot_unavailable_error("test_drive", slots, vehicle_id=vehicle_id)
        presentation_only = {
            "vehicleId",
            "selectedStartsAt",
            "selectedDealershipId",
            "selectedDealershipName",
            "selectedServiceTypeId",
            "selectedServiceName",
        }
        payload = {key: value for key, value in fields.items() if key not in presentation_only}
        try:
            result = dict(await self.dealership.create_test_drive(payload, idempotency_key))
            result.setdefault("vehicleId", vehicle_id)
            for key in ("startsAt", "dealershipId", "dealershipName"):
                if selected_slot.get(key):
                    result.setdefault(key, selected_slot[key])
            vehicle_label = " ".join(
                str(selected_slot.get(key) or "").strip() for key in ("make", "model")
            ).strip()
            if vehicle_label:
                result.setdefault("vehicleLabel", vehicle_label)
            return result
        except DealershipError as error:
            if error.code == "SLOT_UNAVAILABLE":
                fresh = await self.dealership.list_test_drive_slots({"vehicleId": vehicle_id})
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

    async def _create_workshop_booking(self, fields: dict[str, Any], idempotency_key: str) -> dict:
        slot_id = str(fields["slotId"])
        filters = {
            "serviceTypeId": fields["serviceTypeId"],
            "dealershipId": fields["dealershipId"],
        }
        slots = await self.dealership.list_workshop_slots(filters)
        selected_slot = _find_slot(slots, slot_id)
        if selected_slot is None:
            raise self._slot_unavailable_error("workshop", slots)
        presentation_only = {
            "serviceTypeId",
            "dealershipId",
            "selectedStartsAt",
            "selectedDealershipId",
            "selectedDealershipName",
            "selectedServiceTypeId",
            "selectedServiceName",
        }
        payload = {
            key: value
            for key, value in fields.items()
            if key not in presentation_only
        }
        try:
            result = dict(await self.dealership.create_workshop_booking(payload, idempotency_key))
            result.setdefault("dealershipId", fields["dealershipId"])
            result.setdefault("serviceTypeId", fields["serviceTypeId"])
            for key in ("startsAt", "dealershipName", "serviceName"):
                if selected_slot.get(key):
                    result.setdefault(key, selected_slot[key])
            return result
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
        candidate_references = [
            f"appointment:{item['id']}"
            for item in view["items"]
            if isinstance(item, dict) and item.get("id")
        ]
        return DealershipError(
            409,
            "SLOT_UNAVAILABLE",
            "That appointment is no longer available. Please choose another current time.",
            recovery={
                "journey": journey,
                "viewType": view_type,
                "view": view,
                "alternativeOffer": {
                    "reasonCode": "selected_appointment_no_longer_available",
                    "requestedOutcome": "The previously selected appointment",
                    "failureReason": "That appointment is no longer available.",
                    "offeredOutcome": "Choose another current appointment from the refreshed options.",
                    "changes": [
                        {
                            "dimension": "Appointment",
                            "requested": "the previously selected appointment",
                            "offered": "a new current appointment selected by the customer",
                        }
                    ],
                    "preserved": [journey.replace("_", " ").title()],
                    "candidateReferences": candidate_references,
                    "requiresCustomerAcceptance": True,
                },
            },
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

    async def _create_vehicle_interest(self, fields: dict[str, Any], idempotency_key: str) -> dict:
        availability = await self.dealership.get_vehicle_availability(fields["vehicleId"])
        if availability.get("availability") != "reserved":
            raise DealershipError(409, "VEHICLE_NOT_RESERVED", "The vehicle is not reserved.")
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
        changes = {key: fields[key] for key in ("slotId", "mileage", "notes") if key in fields}
        snapshot = json.loads(grant.get("booking_snapshot_json") or "{}")
        try:
            if kind == "workshop_amend":
                result = await self.dealership.update_workshop_booking(
                    grant["booking_record_id"], changes
                )
            else:
                result = await self.dealership.cancel_workshop_booking(grant["booking_record_id"])
        except DealershipError as error:
            if not error.retryable:
                raise
            reconciled = await self._reconcile_workshop_mutation(
                kind, grant["booking_record_id"], changes
            )
            if reconciled is None:
                raise
            result = reconciled
        if kind == "workshop_amend":
            result = dict(result)
            result.setdefault(
                "slotId",
                result.get("slot_id") or fields.get("slotId") or snapshot.get("slotId"),
            )
            result.setdefault(
                "startsAt",
                fields.get("selectedStartsAt") or snapshot.get("startsAt"),
            )
            result.setdefault(
                "dealershipId",
                fields.get("selectedDealershipId")
                or result.get("dealership_id")
                or snapshot.get("dealershipId"),
            )
            result.setdefault(
                "dealershipName",
                fields.get("selectedDealershipName")
                or snapshot.get("dealershipName")
                or snapshot.get("dealershipTown"),
            )
            result.setdefault(
                "serviceTypeId",
                fields.get("selectedServiceTypeId")
                or result.get("service_type_id")
                or snapshot.get("serviceTypeId"),
            )
            result.setdefault(
                "serviceName",
                fields.get("selectedServiceName") or snapshot.get("serviceTypeName"),
            )
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
                raise DealershipError(
                    404, "BOOKING_NOT_FOUND", "No workshop booking matched those details."
                ) from error
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


def _find_slot(data: dict[str, Any], slot_id: str) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in data.get("items", [])
            if isinstance(item, dict) and str(item.get("id")) == slot_id
        ),
        None,
    )
