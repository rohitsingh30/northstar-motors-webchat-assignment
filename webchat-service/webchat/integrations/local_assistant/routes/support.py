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
        words = context.words
        contact = contact_fields(context.combined)
        dealership_id = stable_dealership_id(context.combined)
        selected_department = department(words)

        if "offer" in words or "offers" in words or words.intersection({"pcp", "pch"}):
            return tool("local-list-offers", "list_offers")
        if words.intersection({"callback", "call"}):
            fields = {**contact, "reason": context.latest}
            if dealership_id:
                fields["dealershipId"] = dealership_id
            if selected_department:
                fields["department"] = selected_department
            if context.active_vehicle and context.refers_to_active_vehicle():
                fields["vehicleId"] = context.active_vehicle
            return tool("local-callback", "prepare_callback", fields)
        if "message" in words:
            fields = {
                **contact,
                "message": context.latest,
                "subject": "Website message",
                "preferredContactMethod": "email" if "email" in words else "phone",
            }
            if dealership_id:
                fields["dealershipId"] = dealership_id
            if selected_department:
                fields["department"] = selected_department
            return tool("local-message", "prepare_dealership_message", fields)
        if "department" in words or "departments" in words:
            return tool("local-departments", "list_dealership_departments")
        if self._business_information(words):
            return tool("local-business-info", "get_business_information")
        if "part" in words and words.intersection({"exchange", "valuation", "value"}):
            return self._part_exchange(context, contact, dealership_id)
        if words.intersection({"enquiry", "inquiry"}) or (
            "question" in words and words.intersection({"buying", "buy", "car", "vehicle"})
        ):
            if context.displayed_vehicle_ids and not context.refers_to_active_vehicle():
                return None
            fields = {**contact, "enquiryType": "general", "message": context.latest}
            if dealership_id:
                fields["dealershipId"] = dealership_id
            if context.active_vehicle:
                fields["vehicleId"] = context.active_vehicle
            return tool("local-sales-enquiry", "prepare_sales_enquiry", fields)
        opening_request = bool(words.intersection({"hours", "opening", "holiday"})) or (
            "open" in words and not words.intersection({"car", "cars", "vehicle", "vehicles"})
        )
        if opening_request:
            arguments = {}
            if day := opening_day(context.latest):
                arguments["day"] = day
            if town := location_query(context.latest):
                arguments["town"] = town
            if selected_department in {"sales", "service", "parts"}:
                arguments["department"] = selected_department
            return tool("local-opening-hours", "list_opening_hours", arguments)
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
            and bool(words.intersection({"calculated", "calculation", "estimate"}))
            and "give" not in words
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
