from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from webchat.integrations.contracts import ProviderReply, ToolCall

from .context import ConversationContext
from .parsers import vehicle_filters


class ToolResultResponder:
    """Turns authoritative tool output into a follow-up call or concise introduction."""

    def respond(self, context: ConversationContext) -> ProviderReply:
        if followup := self._vehicle_facets_followup(context):
            return followup
        if followup := self._dealership_followup(context):
            return followup
        return ProviderReply(text=self._summary(context))

    @staticmethod
    def _vehicle_facets_followup(context: ConversationContext) -> ProviderReply | None:
        if context.latest_tool_name() != "get_vehicle_facets":
            return None
        facts = context.latest_tool_facts()
        filters = vehicle_filters(context.combined)
        matched_catalogue_value = False
        for facet, argument in (
            ("makes", "make"),
            ("models", "model"),
            ("fuelTypes", "fuelType"),
            ("transmissions", "transmission"),
            ("bodyStyles", "bodyStyle"),
        ):
            match = _matching_facet(facts.get(facet, []), context.combined)
            if match:
                filters[argument] = match
                matched_catalogue_value = True
        if matched_catalogue_value:
            filters.pop("q", None)
        return ProviderReply(
            "",
            [ToolCall("local-search-vehicles", "search_vehicles", filters)],
        )

    @staticmethod
    def _dealership_followup(context: ConversationContext) -> ProviderReply | None:
        if context.latest_tool_name() != "list_dealerships":
            return None
        dealership = next(
            (
                item
                for item in context.latest_tool_facts().get("items", [])
                if any(
                    str(value or "").lower() in context.combined
                    for value in (item.get("town"), item.get("name"))
                    if value
                )
            ),
            None,
        )
        if dealership and context.words.intersection({"hours", "opening", "open", "holiday"}):
            return ProviderReply(
                "",
                [ToolCall("local-hours", "get_opening_hours", {"id": dealership["id"]})],
            )
        return None

    @staticmethod
    def _summary(context: ConversationContext) -> str:
        facts = context.latest_tool_facts()
        if facts.get("missingFields"):
            return _missing_fields_summary(facts)
        builder = SUMMARY_BUILDERS.get(context.latest_tool_name())
        return builder(context, facts) if builder else "Here are the current Northstar details."


SummaryBuilder = Callable[[ConversationContext, dict[str, Any]], str]


def _missing_fields_summary(facts: dict[str, Any]) -> str:
    labels = {
        "dealershipId": "a preferred dealership",
        "email": "your email address",
        "firstName": "your first name",
        "lastName": "your last name",
        "phone": "your phone number",
        "registration": "the vehicle registration",
        "mileage": "the vehicle mileage",
        "condition": "the vehicle condition",
    }
    fields = ", ".join(
        labels.get(str(field), str(field)) for field in facts["missingFields"]
    )
    if facts.get("kind") == "part_exchange":
        return "I can prepare sales follow-up. Please complete the short form below."
    return f"I can help with that. Please provide {fields}."


def _vehicle_summary(context: ConversationContext, facts: dict[str, Any]) -> str:
    count = len(facts.get("items", []))
    if context.words.intersection({"available", "availability"}):
        return f"Yes — I found {count} matching vehicle{'s' if count != 1 else ''} currently available."
    return f"I found {count} matching vehicle{'s' if count != 1 else ''}. See the current details below."


def _offer_summary(context: ConversationContext, facts: dict[str, Any]) -> str:
    del context
    count = len(facts.get("items", []))
    return f"I found {count} currently published offer{'s' if count != 1 else ''}."


def _location_summary(context: ConversationContext, facts: dict[str, Any]) -> str:
    del context
    count = len(facts.get("items", []))
    return f"Here {'are' if count != 1 else 'is'} {count} Northstar location{'s' if count != 1 else ''}."


def _opening_hours_summary(context: ConversationContext, facts: dict[str, Any]) -> str:
    del context
    day = facts.get("day")
    return (
        f"Here are Northstar opening hours for {day}."
        if day
        else "Here are the current Northstar opening hours."
    )


def _department_summary(context: ConversationContext, facts: dict[str, Any]) -> str:
    del context
    departments = sorted(
        {
            str(department)
            for item in facts.get("items", [])
            for department in item.get("departments", [])
        }
    )
    return "Available departments: " + ", ".join(departments) + "."


def _service_list_summary(context: ConversationContext, facts: dict[str, Any]) -> str:
    del context
    count = len(facts.get("items", []))
    return f"Northstar currently supports {count} service type{'s' if count != 1 else ''}."


def _service_summary(context: ConversationContext, facts: dict[str, Any]) -> str:
    del context
    service = facts.get("service", {})
    name = str(service.get("name") or "This service")
    duration = service.get("durationMinutes")
    price = service.get("priceFromPence")
    price_text = f"from £{price / 100:,.0f}" if isinstance(price, int) else "priced on request"
    duration_text = (
        f" and takes about {duration} minutes" if isinstance(duration, int) else ""
    )
    description = str(service.get("description") or "").strip()
    suffix = f" {description}" if description else ""
    return f"{name} is {price_text}{duration_text}.{suffix}"


def _slot_summary(context: ConversationContext, facts: dict[str, Any]) -> str:
    del context
    count = len(facts.get("items", []))
    return f"I found {count} available time{'s' if count != 1 else ''}."


def _booking_lookup_summary(context: ConversationContext, facts: dict[str, Any]) -> str:
    del context, facts
    return "To protect your booking, I need to verify four details before showing or changing it."


SUMMARY_BUILDERS: dict[str, SummaryBuilder] = {
    "search_vehicles": _vehicle_summary,
    "compare_vehicles": _vehicle_summary,
    "compare_vehicle_models": _vehicle_summary,
    "list_offers": _offer_summary,
    "list_dealerships": _location_summary,
    "list_workshop_locations": _location_summary,
    "list_opening_hours": _opening_hours_summary,
    "list_dealership_departments": _department_summary,
    "list_service_types": _service_list_summary,
    "get_service_information": _service_summary,
    "list_test_drive_slots": _slot_summary,
    "list_workshop_slots": _slot_summary,
    "request_workshop_booking_lookup_form": _booking_lookup_summary,
}


def _matching_facet(values: list[Any], user_text: str) -> Any | None:
    return next(
        (
            value
            for value in sorted(values, key=lambda item: len(str(item)), reverse=True)
            if re.search(rf"\b{re.escape(str(value).lower())}s?\b", user_text)
        ),
        None,
    )
