from __future__ import annotations

import re

from webchat.integrations.contracts import ProviderReply

from ..context import ConversationContext
from ..parsers import (
    contact_fields,
    slot_id,
    stable_dealership_id,
    workshop_filters,
)
from .base import tool


class WorkshopRouter:
    """Routes service discovery, availability, booking, and verified lookup intents."""

    def route(self, context: ConversationContext) -> ProviderReply | None:
        words = context.words
        if "booking" in words and words.intersection(
            {"find", "lookup", "amend", "change", "cancel", "existing"}
        ):
            mode = (
                "cancel"
                if "cancel" in words
                else "amend"
                if words.intersection({"amend", "change"})
                else "lookup"
            )
            return tool(
                "local-booking-lookup",
                "request_workshop_booking_lookup_form",
                {"mode": mode},
            )
        if "workshop" in words and words.intersection(
            {"location", "locations", "where", "address"}
        ):
            return tool("local-workshop-locations", "list_workshop_locations")
        # Free-form service questions go to the semantic provider. Only the legacy
        # explicit action text is accepted here; current chips carry a typed action.
        if re.search(r"\bbook\s+service\s*:", context.latest, re.IGNORECASE):
            return self._booking(context)
        return None

    @staticmethod
    def _booking(context: ConversationContext) -> ProviderReply:
        contact = contact_fields(context.combined)
        selected_slot = slot_id(context.combined)
        if selected_slot:
            fields = {**contact, "slotId": selected_slot}
            if registration := re.search(
                r"\b[A-Z]{2}[0-9]{2}\s?[A-Z]{3}\b",
                context.latest,
                re.IGNORECASE,
            ):
                fields["registration"] = registration.group(0).upper()
            if mileage := re.search(r"([0-9,]+)\s*miles", context.combined):
                fields["mileage"] = int(mileage.group(1).replace(",", ""))
            return tool("local-workshop-booking", "prepare_workshop_booking", fields)
        filters = workshop_filters(context.latest)
        if dealership_id := stable_dealership_id(context.combined):
            filters["dealershipId"] = dealership_id
        if not filters:
            return tool("local-workshop-services", "list_service_types")
        return tool("local-workshop-slots", "list_workshop_slots", filters)
