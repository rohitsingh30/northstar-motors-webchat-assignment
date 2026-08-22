from __future__ import annotations

import re
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
        tool_name = context.latest_tool_name()
        if facts.get("missingFields"):
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
        count = len(facts.get("items", []))
        if tool_name in {"search_vehicles", "compare_vehicles", "compare_vehicle_models"}:
            if context.words.intersection({"available", "availability"}):
                return f"Yes — I found {count} matching vehicle{'s' if count != 1 else ''} currently available."
            return f"I found {count} matching vehicle{'s' if count != 1 else ''}. See the current details below."
        if tool_name == "list_offers":
            return f"I found {count} currently published offer{'s' if count != 1 else ''}."
        if tool_name in {"list_dealerships", "list_workshop_locations"}:
            return f"Here {'are' if count != 1 else 'is'} {count} Northstar location{'s' if count != 1 else ''}."
        if tool_name == "list_opening_hours":
            day = facts.get("day")
            return (
                f"Here are Northstar opening hours for {day}."
                if day
                else "Here are the current Northstar opening hours."
            )
        if tool_name == "list_dealership_departments":
            departments = sorted(
                {
                    str(department)
                    for item in facts.get("items", [])
                    for department in item.get("departments", [])
                }
            )
            return "Available departments: " + ", ".join(departments) + "."
        if tool_name == "list_service_types":
            return f"Northstar currently supports {count} service type{'s' if count != 1 else ''}."
        if tool_name == "get_service_information":
            service = facts.get("service", {})
            name = str(service.get("name") or "This service")
            duration = service.get("durationMinutes")
            price = service.get("priceFromPence")
            price_text = (
                f"from £{price / 100:,.0f}"
                if isinstance(price, int)
                else "priced on request"
            )
            duration_text = (
                f" and takes about {duration} minutes"
                if isinstance(duration, int)
                else ""
            )
            description = str(service.get("description") or "").strip()
            suffix = f" {description}" if description else ""
            return f"{name} is {price_text}{duration_text}.{suffix}"
        if tool_name in {"list_test_drive_slots", "list_workshop_slots"}:
            return f"I found {count} available time{'s' if count != 1 else ''}."
        if tool_name == "request_workshop_booking_lookup_form":
            return "To protect your booking, I need to verify four details before showing or changing it."
        return "Here are the current Northstar details."


def _matching_facet(values: list[Any], user_text: str) -> Any | None:
    return next(
        (
            value
            for value in sorted(values, key=lambda item: len(str(item)), reverse=True)
            if re.search(rf"\b{re.escape(str(value).lower())}s?\b", user_text)
        ),
        None,
    )
