from __future__ import annotations

import re
from typing import Any

from webchat.integrations.contracts import ProviderReply

from ..context import ConversationContext
from ..parsers import contact_fields, department, location_query, opening_day, stable_dealership_id
from .base import tool


class SupportRouter:
    """Routes offers, dealership information, notices, and contact workflows."""

    def route(self, context: ConversationContext) -> ProviderReply | None:
        handlers = (
            self._offers,
            self._callback,
            self._message,
            self._departments,
            self._business_information_request,
            self._part_exchange_request,
            self._sales_enquiry,
            self._opening_hours,
            self._locations,
        )
        for handler in handlers:
            if reply := handler(context):
                return reply
        return None

    @staticmethod
    def _offers(context: ConversationContext) -> ProviderReply | None:
        words = context.words
        explicit_offers = words.intersection(
            {"offer", "offers", "deal", "deals"}
        )
        finance_terms = words.intersection({"pcp", "pch"})
        requests_results = words.intersection(
            {
                "any",
                "available",
                "browse",
                "current",
                "find",
                "have",
                "latest",
                "list",
                "see",
                "show",
            }
        )
        if explicit_offers or (finance_terms and requests_results):
            return tool("local-list-offers", "list_offers")
        return None

    @staticmethod
    def _callback(context: ConversationContext) -> ProviderReply | None:
        words = context.words
        if not words.intersection({"callback", "call"}):
            return None
        contact = contact_fields(context.combined)
        dealership_id = stable_dealership_id(context.combined)
        selected_department = department(words)
        fields = {**contact, "reason": context.latest}
        if dealership_id:
            fields["dealershipId"] = dealership_id
        if selected_department:
            fields["department"] = selected_department
        if context.active_vehicle and context.refers_to_active_vehicle():
            fields["vehicleId"] = context.active_vehicle
        return tool("local-callback", "prepare_callback", fields)

    @staticmethod
    def _message(context: ConversationContext) -> ProviderReply | None:
        words = context.words
        if "message" not in words:
            return None
        fields = {
            **contact_fields(context.combined),
            "message": context.latest,
            "subject": "Website message",
            "preferredContactMethod": "email" if "email" in words else "phone",
        }
        if dealership_id := stable_dealership_id(context.combined):
            fields["dealershipId"] = dealership_id
        if selected_department := department(words):
            fields["department"] = selected_department
        return tool("local-message", "prepare_dealership_message", fields)

    @staticmethod
    def _departments(context: ConversationContext) -> ProviderReply | None:
        if context.words.intersection({"department", "departments"}):
            return tool("local-departments", "list_dealership_departments")
        return None

    @classmethod
    def _business_information_request(
        cls, context: ConversationContext
    ) -> ProviderReply | None:
        if cls._business_information(context.words):
            return tool("local-business-info", "get_business_information")
        return None

    @classmethod
    def _part_exchange_request(
        cls, context: ConversationContext
    ) -> ProviderReply | None:
        if "part" in context.words and context.words.intersection(
            {"exchange", "valuation", "value"}
        ):
            return cls._part_exchange(
                context,
                contact_fields(context.combined),
                stable_dealership_id(context.combined),
            )
        return None

    @staticmethod
    def _sales_enquiry(context: ConversationContext) -> ProviderReply | None:
        words = context.words
        if not (
            words.intersection({"enquiry", "inquiry"})
            or (
            "question" in words and words.intersection({"buying", "buy", "car", "vehicle"})
            )
        ):
            return None
        if context.displayed_vehicle_ids and not context.refers_to_active_vehicle():
            return None
        fields = {
            **contact_fields(context.combined),
            "enquiryType": "general",
            "message": context.latest,
        }
        if dealership_id := stable_dealership_id(context.combined):
            fields["dealershipId"] = dealership_id
        if context.active_vehicle:
            fields["vehicleId"] = context.active_vehicle
        return tool("local-sales-enquiry", "prepare_sales_enquiry", fields)

    @staticmethod
    def _opening_hours(context: ConversationContext) -> ProviderReply | None:
        words = context.words
        opening_request = bool(words.intersection({"hours", "opening", "holiday"})) or (
            "open" in words and not words.intersection({"car", "cars", "vehicle", "vehicles"})
        )
        if not opening_request:
            return None
        arguments = {}
        if day := opening_day(context.latest):
            arguments["day"] = day
        if town := location_query(context.latest):
            arguments["town"] = town
        selected_department = department(words)
        if selected_department in {"sales", "service", "parts"}:
            arguments["department"] = selected_department
        return tool("local-opening-hours", "list_opening_hours", arguments)

    @staticmethod
    def _locations(context: ConversationContext) -> ProviderReply | None:
        words = context.words
        contact_question = words.intersection(
            {"phone", "email", "telephone", "contact"}
        ) and words.intersection({"dealership", "sales", "service"})
        dealership_question = "workshop" not in words and words.intersection(
            {
                "dealership",
                "dealerships",
                "dealer",
                "dealers",
                "location",
                "locations",
                "address",
            }
        )
        active_workshop_follow_up = bool(
            context.has_active_workshop_service
            and re.search(
                r"\b(?:for|about)\s+(?:it|that|this|the\s+service)\b",
                context.latest,
                re.IGNORECASE,
            )
        )
        if active_workshop_follow_up:
            # This contextual request must pass through the semantic plan and
            # Shared transition state must resolve this follow-up; replacing a
            # general response with a dealership lookup would lose the service.
            return None
        if contact_question or dealership_question:
            arguments = {"town": location_query(context.latest)} if location_query(context.latest) else {}
            return tool("local-list-dealers", "list_dealerships", arguments)
        return None

    @staticmethod
    def _business_information(words: frozenset[str]) -> bool:
        privacy = bool(words.intersection({"privacy", "personal", "data"}))
        finance = "finance" in words and bool(words.intersection({"information", "how", "work"}))
        estimate_notice = (
            "part" in words
            and "exchange" in words
            and bool(
                words.intersection(
                    {"calculated", "calculation", "calculate", "methodology"}
                )
            )
        )
        return privacy or finance or estimate_notice

    @staticmethod
    def _part_exchange(
        context: ConversationContext,
        contact: dict[str, Any],
        dealership_id: str | None,
    ) -> ProviderReply:
        fields = dict(contact)
        if dealership_id:
            fields["dealershipId"] = dealership_id
        if context.words.intersection({"enquiry", "inquiry"}) and not context.words.intersection(
            {"estimate", "valuation", "value"}
        ):
            return tool("local-part-exchange-enquiry", "prepare_part_exchange", fields)
        user_text = " ".join(
            str(message.get("content", ""))
            for message in context.messages
            if message.get("role") == "user"
        )
        if registration := re.search(r"\b[A-Z]{2}[0-9]{2}\s?[A-Z]{3}\b", user_text, re.IGNORECASE):
            fields["registration"] = registration.group(0).upper()
        if mileage := re.search(r"([0-9,]+)\s*miles", context.combined):
            fields["mileage"] = int(mileage.group(1).replace(",", ""))
        if condition := next(
            (value for value in ("excellent", "good", "fair") if value in context.combined),
            None,
        ):
            fields["condition"] = condition
        estimate_fields = {
            key: fields[key]
            for key in ("registration", "mileage", "condition")
            if key in fields
        }
        if len(estimate_fields) == 3:
            return tool("local-part-exchange-estimate", "estimate_part_exchange", estimate_fields)
        return tool(
            "local-part-exchange-estimate-form",
            "request_part_exchange_estimate_form",
            estimate_fields,
        )
