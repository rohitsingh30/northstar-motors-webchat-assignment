"""Canonical business domains, goals, and workflow-state compatibility."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass(frozen=True, order=True)
class GoalKey:
    """A stable application-owned business capability identifier."""

    domain: str
    goal: str

    def __str__(self) -> str:
        return f"{self.domain}.{self.goal}"


class Goals:
    """Namespaced goal constants used by planning, state, and transitions."""

    VEHICLE_SEARCH: ClassVar[GoalKey] = GoalKey("vehicle", "search")
    VEHICLE_CONTINUE_SEARCH: ClassVar[GoalKey] = GoalKey("vehicle", "continue_search")
    VEHICLE_COMPARE: ClassVar[GoalKey] = GoalKey("vehicle", "compare")
    VEHICLE_VIEW_DETAILS: ClassVar[GoalKey] = GoalKey("vehicle", "view_details")
    VEHICLE_CHECK_AVAILABILITY: ClassVar[GoalKey] = GoalKey(
        "vehicle", "check_availability"
    )
    VEHICLE_CHOOSE_PREFERENCES: ClassVar[GoalKey] = GoalKey(
        "vehicle", "choose_preferences"
    )
    VEHICLE_APPLY_PREFERENCE: ClassVar[GoalKey] = GoalKey(
        "vehicle", "apply_preference"
    )

    OFFER_BROWSE: ClassVar[GoalKey] = GoalKey("offer", "browse")
    OFFER_VIEW_DETAILS: ClassVar[GoalKey] = GoalKey("offer", "view_details")
    OFFER_ENQUIRE: ClassVar[GoalKey] = GoalKey("offer", "enquire")

    TEST_DRIVE_BOOK: ClassVar[GoalKey] = GoalKey("test_drive", "book")

    SALES_ENQUIRE: ClassVar[GoalKey] = GoalKey("sales", "enquire")
    SALES_REQUEST_CALLBACK: ClassVar[GoalKey] = GoalKey("sales", "request_callback")
    SALES_REGISTER_INTEREST: ClassVar[GoalKey] = GoalKey(
        "sales", "register_vehicle_interest"
    )

    DEALERSHIP_FIND: ClassVar[GoalKey] = GoalKey("dealership", "find")
    DEALERSHIP_VIEW_CONTACT: ClassVar[GoalKey] = GoalKey(
        "dealership", "view_contact"
    )
    DEALERSHIP_VIEW_DEPARTMENTS: ClassVar[GoalKey] = GoalKey(
        "dealership", "view_departments"
    )
    DEALERSHIP_VIEW_OPENING_HOURS: ClassVar[GoalKey] = GoalKey(
        "dealership", "view_opening_hours"
    )
    DEALERSHIP_SEND_MESSAGE: ClassVar[GoalKey] = GoalKey(
        "dealership", "send_message"
    )

    WORKSHOP_FIND_LOCATIONS: ClassVar[GoalKey] = GoalKey(
        "workshop", "find_locations"
    )
    WORKSHOP_BROWSE_SERVICES: ClassVar[GoalKey] = GoalKey(
        "workshop", "browse_services"
    )
    WORKSHOP_CHECK_SERVICE: ClassVar[GoalKey] = GoalKey(
        "workshop", "check_service"
    )
    WORKSHOP_BOOK_SERVICE: ClassVar[GoalKey] = GoalKey(
        "workshop", "book_service"
    )
    WORKSHOP_FIND_BOOKING: ClassVar[GoalKey] = GoalKey(
        "workshop", "find_booking"
    )
    WORKSHOP_CHANGE_BOOKING: ClassVar[GoalKey] = GoalKey(
        "workshop", "change_booking"
    )
    WORKSHOP_CANCEL_BOOKING: ClassVar[GoalKey] = GoalKey(
        "workshop", "cancel_booking"
    )

    PART_EXCHANGE_ESTIMATE: ClassVar[GoalKey] = GoalKey(
        "part_exchange", "estimate"
    )
    PART_EXCHANGE_REQUEST_FOLLOW_UP: ClassVar[GoalKey] = GoalKey(
        "part_exchange", "request_follow_up"
    )

    BUSINESS_FINANCE_INFORMATION: ClassVar[GoalKey] = GoalKey(
        "business", "finance_information"
    )
    BUSINESS_PRIVACY_INFORMATION: ClassVar[GoalKey] = GoalKey(
        "business", "privacy_information"
    )
    BUSINESS_PART_EXCHANGE_INFORMATION: ClassVar[GoalKey] = GoalKey(
        "business", "part_exchange_information"
    )
    BUSINESS_GENERAL_INFORMATION: ClassVar[GoalKey] = GoalKey(
        "business", "general_information"
    )

    CONVERSATION_RESPOND: ClassVar[GoalKey] = GoalKey("conversation", "respond")
    CONVERSATION_CLARIFY: ClassVar[GoalKey] = GoalKey("conversation", "clarify")


ALL_GOALS = frozenset(
    value
    for name, value in vars(Goals).items()
    if name.isupper() and isinstance(value, GoalKey)
)

DEFAULT_DOMAIN_GOALS: dict[str, GoalKey] = {
    "vehicle": Goals.VEHICLE_SEARCH,
    "offer": Goals.OFFER_BROWSE,
    "test_drive": Goals.TEST_DRIVE_BOOK,
    "sales": Goals.SALES_ENQUIRE,
    "dealership": Goals.DEALERSHIP_FIND,
    "workshop": Goals.WORKSHOP_BOOK_SERVICE,
    "part_exchange": Goals.PART_EXCHANGE_ESTIMATE,
    "business": Goals.BUSINESS_GENERAL_INFORMATION,
    "conversation": Goals.CONVERSATION_RESPOND,
}


# Stored V1 states may exist in a browser's ongoing conversation. This mapping is
# deliberately confined to the persistence boundary; new plans never use these names.
LEGACY_STATE_GOALS: dict[str, GoalKey] = {
    "vehicle_search": Goals.VEHICLE_SEARCH,
    "vehicle_more": Goals.VEHICLE_CONTINUE_SEARCH,
    "vehicle_compare": Goals.VEHICLE_COMPARE,
    "vehicle_details": Goals.VEHICLE_VIEW_DETAILS,
    "vehicle_availability": Goals.VEHICLE_CHECK_AVAILABILITY,
    "vehicle_preferences": Goals.VEHICLE_CHOOSE_PREFERENCES,
    "vehicle_preference_selection": Goals.VEHICLE_APPLY_PREFERENCE,
    "test_drive": Goals.TEST_DRIVE_BOOK,
    "offers_list": Goals.OFFER_BROWSE,
    "offer_details": Goals.OFFER_VIEW_DETAILS,
    "dealership_locations": Goals.DEALERSHIP_FIND,
    "dealership_contact": Goals.DEALERSHIP_VIEW_CONTACT,
    "dealership_departments": Goals.DEALERSHIP_VIEW_DEPARTMENTS,
    "opening_hours": Goals.DEALERSHIP_VIEW_OPENING_HOURS,
    "workshop_locations": Goals.WORKSHOP_FIND_LOCATIONS,
    "workshop_services": Goals.WORKSHOP_BROWSE_SERVICES,
    "workshop_service_information": Goals.WORKSHOP_CHECK_SERVICE,
    "workshop_booking": Goals.WORKSHOP_BOOK_SERVICE,
    "workshop_booking_lookup": Goals.WORKSHOP_FIND_BOOKING,
    "workshop_booking_change": Goals.WORKSHOP_CHANGE_BOOKING,
    "workshop_booking_cancel": Goals.WORKSHOP_CANCEL_BOOKING,
    "part_exchange_estimate": Goals.PART_EXCHANGE_ESTIMATE,
    "part_exchange_follow_up": Goals.PART_EXCHANGE_REQUEST_FOLLOW_UP,
    "callback": Goals.SALES_REQUEST_CALLBACK,
    "sales_enquiry": Goals.SALES_ENQUIRE,
    "vehicle_interest": Goals.SALES_REGISTER_INTEREST,
    "dealership_message": Goals.DEALERSHIP_SEND_MESSAGE,
    "business_information": Goals.BUSINESS_GENERAL_INFORMATION,
    "general_response": Goals.CONVERSATION_RESPOND,
    "clarification": Goals.CONVERSATION_CLARIFY,
}


def normalize_workflow_state(state: dict[str, Any] | None) -> dict[str, Any]:
    """Return canonical V2 state while accepting already-persisted V1 state."""

    if not state:
        return {}
    normalized = dict(state)
    candidate = GoalKey(
        str(normalized.get("domain") or "conversation"),
        str(normalized.get("goal") or "respond"),
    )
    key = candidate if candidate in ALL_GOALS else None
    key = key or LEGACY_STATE_GOALS.get(str(normalized.get("intent") or ""))
    if key is None:
        key = DEFAULT_DOMAIN_GOALS.get(
            str(normalized.get("domain") or "conversation"),
            Goals.CONVERSATION_RESPOND,
        )
    normalized.update(version=2, domain=key.domain, goal=key.goal)
    normalized.pop("intent", None)
    normalized.setdefault("stage", "planned")
    normalized.setdefault("entities", {})
    normalized.setdefault("constraints", {})
    return normalized


def workflow_state(
    key: GoalKey,
    stage: str,
    *,
    entities: dict[str, Any] | None = None,
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct the single canonical persisted workflow-state shape."""

    return {
        "version": 2,
        "domain": key.domain,
        "goal": key.goal,
        "stage": stage,
        "entities": dict(entities or {}),
        "constraints": dict(constraints or {}),
    }
