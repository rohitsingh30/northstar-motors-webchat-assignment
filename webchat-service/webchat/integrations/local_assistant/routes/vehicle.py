from __future__ import annotations

from webchat.integrations.contracts import ProviderReply

from ..context import ConversationContext
from ..parsers import (
    comparison_queries,
    contact_fields,
    price_limit,
    slot_id,
    vehicle_filters,
    vehicle_query,
)
from .base import tool


class VehicleRouter:
    """Routes discovery, context follow-ups, comparison, and test-drive intents."""

    ACTION_WORDS = frozenset(
        {
            "test",
            "drive",
            "interest",
            "enquiry",
            "inquiry",
            "booking",
            "callback",
            "exchange",
            "finance",
            "question",
        }
    )

    def route(self, context: ConversationContext) -> ProviderReply | None:
        words = context.words
        if context.selected_test_drive_vehicle:
            return tool(
                "local-test-drive-slots",
                "list_test_drive_slots",
                {"vehicleId": context.selected_test_drive_vehicle},
            )
        if context.selected_test_drive_slot:
            return tool(
                "local-test-drive",
                "prepare_test_drive",
                {"slotId": context.selected_test_drive_slot},
            )
        if "compare" in words:
            if len(context.vehicle_ids) >= 2:
                return tool(
                    "local-compare",
                    "compare_vehicles",
                    {"vehicleIds": list(context.vehicle_ids[:4])},
                )
            if queries := comparison_queries(context.normalized):
                return tool("local-compare-models", "compare_vehicle_models", {"queries": queries})
            return ProviderReply(
                "Please name the vehicles you want compared, or choose them from the results."
            )
        if "interest" in words:
            if context.active_vehicle and (
                not context.displayed_vehicle_ids or context.refers_to_active_vehicle()
            ):
                return tool(
                    "local-interest",
                    "prepare_vehicle_interest",
                    {"vehicleId": context.active_vehicle},
                )
            return ProviderReply(
                "Please select the reserved vehicle you want to register interest in."
            )
        availability_words = words.intersection({"available", "availability", "reserved", "sold"})
        if availability_words and words.intersection({"mean", "means", "meaning"}):
            return ProviderReply(
                "Reserved means another customer has started a purchase, so the car is not "
                "currently available for a new purchase unless its status changes."
            )
        named_query = vehicle_query(context.latest)
        if (
            availability_words
            and context.displayed_vehicle_ids
            and named_query
            and not _is_deictic_vehicle_query(named_query)
        ):
            return None
        if (
            availability_words
            and named_query
            and not context.displayed_vehicle_ids
            and named_query.lower() not in {
            "this", "it", "vehicle", "car"
            }
            and not _is_deictic_vehicle_query(named_query)
        ):
            return tool(
                "local-availability-search",
                "search_vehicles",
                {"q": named_query.lower(), "sort": "priceAsc"},
            )
        if (
            context.active_vehicle
            and availability_words
            and context.refers_to_active_vehicle()
        ):
            return tool(
                "local-availability",
                "get_vehicle_availability",
                {"id": context.active_vehicle},
            )
        if reply := self._details_or_missing_context(context):
            return reply
        if availability_words and context.refers_to_active_vehicle() and not context.active_vehicle:
            query = vehicle_query(context.latest)
            if not query or query.lower() in {"this", "thi", "vehicle", "car"}:
                return ProviderReply(
                    "Please select a vehicle card so I can check its live availability."
                )
        if "test" in words and "drive" in words:
            return self._test_drive(context)
        if self._needs_discovery_clarification(context):
            return ProviderReply("Tell me what matters most, or choose a starting point below.")
        if self._is_search(context):
            return self._search(context)
        return None

    @staticmethod
    def _needs_discovery_clarification(context: ConversationContext) -> bool:
        text = context.latest.lower()
        is_broad_request = any(
            phrase in text
            for phrase in ("help me find another car", "find another car", "help me find a car")
        )
        preference_words = {
            "budget", "price", "petrol", "diesel", "hybrid", "electric", "suv", "hatchback",
            "saloon", "estate", "automatic", "manual", "mileage", "make", "model",
        }
        return is_broad_request and not context.words.intersection(preference_words)

    @staticmethod
    def _details_or_missing_context(context: ConversationContext) -> ProviderReply | None:
        asks_for_facts = context.words.intersection({"price", "details", "detail"}) or (
            context.words.intersection({"mileage", "fuel", "gearbox", "monthly", "payment"})
            and context.words.intersection({"what", "which", "tell", "show"})
        )
        vehicle_target = context.words.intersection({"car", "cars", "vehicle", "vehicles"}) or (
            context.refers_to_active_vehicle()
        )
        if not asks_for_facts or not vehicle_target:
            return None
        if context.displayed_vehicle_ids and not _is_deictic_vehicle_query(
            vehicle_query(context.latest) or context.latest
        ):
            # The model receives the ordered live candidates and resolves arbitrary
            # references such as position, colour, price, or model description.
            return ProviderReply(
                "I need to resolve which displayed vehicle you mean before showing its details."
            )
        if context.active_vehicle:
            return tool("local-vehicle", "get_vehicle", {"id": context.active_vehicle})
        return ProviderReply(
            "Please select a vehicle card first and I can show its current details."
        )

    @classmethod
    def _is_search(cls, context: ConversationContext) -> bool:
        refinement = bool(context.words.intersection({"only", "actually", "instead", "change"}))
        search = bool(
            context.words.intersection(
                {
                    "car",
                    "cars",
                    "vehicle",
                    "vehicles",
                    "suv",
                    "hatchback",
                    "saloon",
                    "estate",
                }
            )
            or vehicle_query(context.latest)
            or price_limit(context.latest) is not None
        )
        if refinement and (
            vehicle_query(context.latest) or price_limit(context.latest) is not None
        ):
            search = True
        return (
            search
            and not context.words.intersection(cls.ACTION_WORDS)
            and not context.words.intersection({"workshop", "servicing", "mot"})
        )

    @staticmethod
    def _search(context: ConversationContext) -> ProviderReply:
        refinement = bool(context.words.intersection({"only", "actually", "instead", "change"}))
        filters = vehicle_filters(context.combined if refinement else context.latest)
        if filters.get("q") and {"fuelType", "transmission", "bodyStyle"}.intersection(filters):
            return tool("local-vehicle-facets", "get_vehicle_facets")
        return tool("local-search-vehicles", "search_vehicles", filters)

    @staticmethod
    def _test_drive(context: ConversationContext) -> ProviderReply:
        contact = contact_fields(context.combined)
        if selected_slot := slot_id(context.combined):
            return tool(
                "local-test-drive",
                "prepare_test_drive",
                {**contact, "slotId": selected_slot},
            )
        named_query = vehicle_query(context.latest)
        if context.displayed_vehicle_ids and not context.refers_to_active_vehicle():
            return ProviderReply(
                "Please identify which displayed vehicle you want to test drive."
            )
        if named_query and not _is_deictic_vehicle_query(named_query):
            return tool("local-test-drive-facets", "get_vehicle_facets")
        if not context.active_vehicle:
            return tool("local-test-drive-facets", "get_vehicle_facets")
        return tool(
            "local-test-slots",
            "list_test_drive_slots",
            {"vehicleId": context.active_vehicle},
        )


def _is_deictic_vehicle_query(value: str) -> bool:
    return set(value.lower().split()).issubset(
        {"this", "that", "the", "selected", "current", "vehicle", "car", "it"}
    )
