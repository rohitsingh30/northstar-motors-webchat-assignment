"""Deterministic workflow outcomes and transitions.

The language model may identify intent and customer-authored preferences. It does not decide what
an empty business result means, which field is required next, or whether a workflow has completed.
Those decisions live here so every execution path shares the same transition contract.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

WORKSHOP_CONTINUATION_FIELDS = frozenset(
    {
        "dealershipId",
        "vehicleId",
        "serviceTypeId",
        "serviceTypeName",
        "dealershipTown",
        "dateFrom",
        "dateTo",
        "timeOfDay",
        "make",
        "productType",
        "workflowMode",
    }
)

TestDriveResolutionStatus = Literal["ready", "matched", "empty", "unavailable"]
TestDriveResolutionScope = Literal["broad", "preference_filtered"]
TestDriveContinuation = Literal[
    "request_schedule_preferences",
    "request_slot_selection",
    "offer_vehicle_alternatives",
    "request_alternative_schedule",
    "request_vehicle_selection",
    "offer_schedule_alternatives",
    "offer_location_alternatives",
    "offer_equivalent_vehicle_slots",
]
WorkshopContinuation = Literal[
    "request_schedule_preferences",
    "request_slot_selection",
    "choose_alternative_location",
    "offer_workshop_contact",
    "request_alternative_schedule",
    "offer_schedule_alternatives",
    "offer_location_alternatives",
    "offer_location_and_schedule_alternatives",
]


class TestDriveSlotResolution(TypedDict):
    kind: Literal["test_drive_slots"]
    status: TestDriveResolutionStatus
    scope: TestDriveResolutionScope
    continuation: TestDriveContinuation
    vehicleId: str
    count: int
    scheduleAlternativeCount: int
    locationAlternativeCount: int
    equivalentVehicleSlotCount: int


class WorkflowTransition(TypedDict):
    stage: str
    missingPublicFields: list[str]
    acceptedInputFields: list[str]
    continuation: TestDriveContinuation


def _appointment_empty_continuation(
    alternatives: tuple[tuple[int, str], ...],
    *,
    filtered: bool,
    search_exhausted: bool,
    terminal: str,
) -> str:
    """Choose one recovery from trusted search scopes, never from generated prose."""

    for count, continuation in alternatives:
        if count > 0:
            return continuation
    if filtered and not search_exhausted:
        return "request_alternative_schedule"
    return terminal


def merge_workshop_continuation_context(
    state: dict[str, Any], incoming: dict[str, Any]
) -> dict[str, Any]:
    """Merge one workshop continuation without losing compatible public context.

    Typed replies and trusted application actions share this function. Explicit incoming selectors
    replace only their own dimension; every other compatible selector and schedule preference is
    retained from durable workflow state.
    """

    proposed = dict(incoming)
    if state.get("activeWorkflow") != "workshop_booking":
        return proposed
    constraints = dict(state.get("constraints") or {})
    entities = dict(state.get("entities") or {})
    current = {
        key: value
        for key, value in constraints.items()
        if key in WORKSHOP_CONTINUATION_FIELDS
    }
    current.update(
        {
            key: value
            for key, value in entities.items()
            if key in {"serviceTypeId", "serviceTypeName", "dealershipId"}
            and value is not None
        }
    )
    scheduling = dict(constraints.get("schedulingPreferences") or {})
    current.update(
        {
            key: scheduling[key]
            for key in ("dateFrom", "dateTo", "timeOfDay")
            if current.get(key) is None and scheduling.get(key) is not None
        }
    )
    if proposed.get("serviceTypeId") or proposed.get("serviceTypeName"):
        current.pop("serviceTypeId", None)
        current.pop("serviceTypeName", None)
    if proposed.get("dealershipId") or proposed.get("dealershipTown"):
        current.pop("dealershipId", None)
        current.pop("dealershipTown", None)
    if str(proposed.get("schedulePreferenceMode") or "preserve") in {"replace", "clear"}:
        for field in ("dateFrom", "dateTo", "timeOfDay"):
            current.pop(field, None)
    return {**current, **proposed}


def test_drive_slot_resolution(
    arguments: dict[str, Any],
    *,
    count: int,
    vehicle_available: bool = True,
    schedule_alternative_count: int = 0,
    location_alternative_count: int = 0,
    equivalent_vehicle_slot_count: int = 0,
    schedule_search_exhausted: bool = False,
) -> TestDriveSlotResolution:
    """Classify one authoritative slot read without relying on renderer or prose."""

    filtered = any(
        arguments.get(field) is not None
        for field in ("dateFrom", "dateTo", "timeOfDay")
    )
    browse_available = arguments.get("schedulePreferenceMode") == "clear"
    scope: TestDriveResolutionScope = (
        "preference_filtered" if filtered else "broad"
    )
    if not vehicle_available:
        status: TestDriveResolutionStatus = "unavailable"
        continuation: TestDriveContinuation = "request_vehicle_selection"
    elif not filtered and not browse_available:
        status = "ready"
        continuation = "request_schedule_preferences"
    elif count > 0:
        status = "matched"
        continuation = "request_slot_selection"
    else:
        status = "empty"
        continuation = _appointment_empty_continuation(
            (
                (schedule_alternative_count, "offer_schedule_alternatives"),
                (location_alternative_count, "offer_location_alternatives"),
                (equivalent_vehicle_slot_count, "offer_equivalent_vehicle_slots"),
            ),
            filtered=filtered,
            search_exhausted=schedule_search_exhausted,
            terminal="offer_vehicle_alternatives",
        )
    return {
        "kind": "test_drive_slots",
        "status": status,
        "scope": scope,
        "continuation": continuation,
        "vehicleId": str(arguments.get("vehicleId") or ""),
        "count": max(0, int(count)),
        "scheduleAlternativeCount": max(0, int(schedule_alternative_count)),
        "locationAlternativeCount": max(0, int(location_alternative_count)),
        "equivalentVehicleSlotCount": max(0, int(equivalent_vehicle_slot_count)),
    }


def test_drive_transition(resolution: dict[str, Any]) -> WorkflowTransition:
    """Map a typed test-drive result to exactly one valid next state."""

    continuation = str(resolution.get("continuation") or "")
    transitions: dict[str, tuple[str, list[str], list[str]]] = {
        "request_schedule_preferences": (
            "choosing_schedule_preferences",
            ["preferredDayOrDate", "approximateTime"],
            ["dateFrom", "dateTo", "timeOfDay", "schedulePreferenceMode"],
        ),
        "request_slot_selection": ("choosing_slot", ["slotId"], []),
        "offer_vehicle_alternatives": ("no_availability", [], []),
        "request_alternative_schedule": (
            "choosing_alternative_schedule",
            ["preferredDayOrDate", "approximateTime"],
            ["dateFrom", "dateTo", "timeOfDay", "schedulePreferenceMode"],
        ),
        "request_vehicle_selection": ("choosing_vehicle", ["vehicleId"], []),
        "offer_schedule_alternatives": ("choosing_slot", ["slotId"], []),
        "offer_location_alternatives": ("choosing_slot", ["slotId"], []),
        "offer_equivalent_vehicle_slots": ("choosing_slot", ["slotId"], []),
    }
    if continuation not in transitions:
        raise ValueError("invalid test-drive continuation")
    stage, missing, accepted = transitions[continuation]
    return {
        "stage": stage,
        "missingPublicFields": missing,
        "acceptedInputFields": accepted,
        "continuation": continuation,  # type: ignore[typeddict-item]
    }


def workshop_slot_resolution(
    arguments: dict[str, Any],
    *,
    count: int,
    alternative_count: int = 0,
    schedule_alternative_count: int = 0,
    location_schedule_alternative_count: int = 0,
    schedule_search_exhausted: bool = False,
) -> dict[str, Any]:
    filtered = any(
        arguments.get(field) is not None
        for field in ("dateFrom", "dateTo", "timeOfDay")
    )
    browse_available = arguments.get("schedulePreferenceMode") == "clear"
    if not filtered and not browse_available:
        continuation: WorkshopContinuation = "request_schedule_preferences"
        status = "ready"
    elif count > 0:
        continuation = "request_slot_selection"
        status = "matched"
    else:
        status = "empty"
        continuation = _appointment_empty_continuation(
            (
                (schedule_alternative_count, "offer_schedule_alternatives"),
                (alternative_count, "offer_location_alternatives"),
                (
                    location_schedule_alternative_count,
                    "offer_location_and_schedule_alternatives",
                ),
            ),
            filtered=filtered,
            search_exhausted=schedule_search_exhausted,
            terminal="offer_workshop_contact",
        )
    return {
        "kind": "workshop_slots",
        "status": status,
        "scope": "preference_filtered" if filtered else "broad",
        "continuation": continuation,
        "count": max(0, int(count)),
        "alternativeCount": max(0, int(alternative_count)),
        "scheduleAlternativeCount": max(0, int(schedule_alternative_count)),
        "locationScheduleAlternativeCount": max(
            0, int(location_schedule_alternative_count)
        ),
    }


def workshop_transition(resolution: dict[str, Any]) -> dict[str, Any]:
    continuation = str(resolution.get("continuation") or "")
    transitions: dict[str, tuple[str, list[str], list[str]]] = {
        "request_schedule_preferences": (
            "choosing_schedule_preferences",
            ["preferredDayOrDate", "approximateTime"],
            ["dateFrom", "dateTo", "timeOfDay", "schedulePreferenceMode"],
        ),
        "request_slot_selection": ("choosing_slot", ["slotId"], []),
        "choose_alternative_location": ("choosing_dealership", ["dealershipId"], []),
        "offer_workshop_contact": ("no_availability", [], []),
        "request_alternative_schedule": (
            "choosing_alternative_schedule",
            ["preferredDayOrDate", "approximateTime"],
            ["dateFrom", "dateTo", "timeOfDay", "schedulePreferenceMode"],
        ),
        "offer_schedule_alternatives": ("choosing_slot", ["slotId"], []),
        "offer_location_alternatives": ("choosing_slot", ["slotId"], []),
        "offer_location_and_schedule_alternatives": (
            "choosing_slot",
            ["slotId"],
            [],
        ),
    }
    if continuation not in transitions:
        raise ValueError("invalid workshop continuation")
    stage, missing, accepted = transitions[continuation]
    return {
        "stage": stage,
        "missingPublicFields": missing,
        "acceptedInputFields": accepted,
        "continuation": continuation,
    }
