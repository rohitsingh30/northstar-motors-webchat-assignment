from __future__ import annotations

import re

from webchat.integrations.contracts import ProviderReply
from webchat.orchestration.contracts.plan import InteractionProposal

from .base import clarify, tool
from .context import ConversationContext
from .parsers import (
    comparison_queries,
    contact_fields,
    price_limit,
    slot_id,
    vehicle_filters,
    vehicle_query,
)


class VehicleRouter:
    """Routes discovery, context follow-ups, comparison, and test-drive requests."""

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
    DEFER = object()

    def route(self, context: ConversationContext) -> ProviderReply | None:
        handlers = (
            self._selected_action,
            self._comparison,
            self._interest,
            self._availability,
            self._open_on_website,
            self._details_or_missing_context,
            self._test_drive_request,
            self._discovery,
        )
        for handler in handlers:
            reply = handler(context)
            if reply is self.DEFER:
                return None
            if reply is not None:
                return reply
        return None

    @staticmethod
    def _open_on_website(context: ConversationContext) -> ProviderReply | None:
        asks_to_open = "open" in context.words or (
            "view" in context.words and "detail" in context.words
        ) or "full details" in context.normalized
        if not asks_to_open:
            return None
        vehicle_id = _referenced_displayed_vehicle(context)
        if vehicle_id is None and context.active_vehicle and context.refers_to_active_vehicle():
            vehicle_id = context.active_vehicle
        if vehicle_id is None:
            return clarify(
                "Which vehicle would you like me to open?",
                "get_vehicle",
                fields=("id",),
            )
        return ProviderReply(
            "",
            interaction_proposal=InteractionProposal(
                kind="open_vehicle_detail",
                entityReference=f"vehicle:{vehicle_id}",
            ),
        )

    @staticmethod
    def _selected_action(context: ConversationContext) -> ProviderReply | None:
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
        return None

    @staticmethod
    def _comparison(context: ConversationContext) -> ProviderReply | None:
        if "compare" not in context.words:
            return None
        if len(context.vehicle_ids) >= 2:
            return tool(
                "local-compare",
                "compare_vehicles",
                {"vehicleIds": list(context.vehicle_ids[:4])},
            )
        if queries := comparison_queries(context.normalized):
            return tool("local-compare-models", "compare_vehicle_models", {"queries": queries})
        return clarify(
            "Please name the vehicles you want compared, or choose them from the results.",
            "compare_vehicle_models",
            fields=("queries",),
        )

    @staticmethod
    def _interest(context: ConversationContext) -> ProviderReply | None:
        if "interest" not in context.words:
            return None
        if context.active_vehicle and (
            not context.displayed_vehicle_ids or context.refers_to_active_vehicle()
        ):
            return tool(
                "local-interest",
                "prepare_vehicle_interest",
                {"vehicleId": context.active_vehicle},
            )
        return clarify(
            "Please select the reserved vehicle you want to register interest in.",
            "prepare_vehicle_interest",
            fields=("vehicleId",),
        )

    @staticmethod
    def _availability(context: ConversationContext) -> ProviderReply | object | None:
        words = context.words
        availability_words = words.intersection({"available", "availability", "reserved", "sold"})
        if availability_words and words.intersection({"mean", "means", "meaning"}):
            return ProviderReply(
                "Reserved means another customer has started a purchase, so the car is not "
                "currently available for a new purchase unless its status changes."
            )
        if availability_words and (
            words.intersection({"show", "find", "list", "browse"})
            or words.intersection({"cars", "vehicles"})
        ):
            # Availability is a search filter in discovery wording such as
            # "show available cars", not a status check for one vehicle.
            return None
        named_query = vehicle_query(context.latest)
        if (
            availability_words
            and context.displayed_vehicle_ids
            and named_query
            and not _is_deictic_vehicle_query(named_query)
        ):
            return VehicleRouter.DEFER
        if (
            availability_words
            and named_query
            and not context.displayed_vehicle_ids
            and named_query.lower() not in {"this", "it", "vehicle", "car"}
            and not _is_deictic_vehicle_query(named_query)
        ):
            return tool(
                "local-availability-search",
                "search_vehicles",
                {"q": named_query.lower(), "sort": "priceAsc"},
            )
        if context.active_vehicle and availability_words and context.refers_to_active_vehicle():
            return tool(
                "local-availability",
                "get_vehicle_availability",
                {"id": context.active_vehicle},
            )
        if availability_words and context.refers_to_active_vehicle() and not context.active_vehicle:
            query = vehicle_query(context.latest)
            if not query or query.lower() in {"this", "thi", "vehicle", "car"}:
                return clarify(
                    "Please select a vehicle card so I can check its live availability.",
                    "get_vehicle_availability",
                    fields=("id",),
                )
        return None

    @classmethod
    def _test_drive_request(cls, context: ConversationContext) -> ProviderReply | None:
        if "test" in context.words and "drive" in context.words:
            return cls._test_drive(context)
        return None

    @classmethod
    def _discovery(cls, context: ConversationContext) -> ProviderReply | None:
        if cls._needs_discovery_clarification(context):
            return ProviderReply("Tell me what matters most, or choose a starting point below.")
        if cls._is_search(context):
            return cls._search(context)
        return None

    @staticmethod
    def _needs_discovery_clarification(context: ConversationContext) -> bool:
        text = context.latest.lower()
        is_broad_request = any(
            phrase in text
            for phrase in ("help me find another car", "find another car", "help me find a car")
        )
        preference_words = {
            "budget",
            "price",
            "petrol",
            "diesel",
            "hybrid",
            "electric",
            "suv",
            "hatchback",
            "saloon",
            "estate",
            "automatic",
            "manual",
            "mileage",
            "make",
            "model",
        }
        return is_broad_request and not context.words.intersection(preference_words)

    @staticmethod
    def _details_or_missing_context(context: ConversationContext) -> ProviderReply | None:
        asks_to_show_singular = bool(
            "show" in context.words
            and context.words.intersection({"car", "vehicle"})
            and not context.words.intersection({"cars", "vehicles", "all"})
        )
        asks_for_facts = asks_to_show_singular or context.words.intersection(
            {"price", "details", "detail"}
        ) or (
            context.words.intersection({"mileage", "fuel", "gearbox", "monthly", "payment"})
            and context.words.intersection({"what", "which", "tell", "show"})
        )
        vehicle_target = context.words.intersection({"car", "cars", "vehicle", "vehicles"}) or (
            context.refers_to_active_vehicle()
        )
        if not asks_for_facts or not vehicle_target:
            return None
        if (
            context.displayed_vehicle_ids
            and not context.refers_to_active_vehicle()
            and not _is_deictic_vehicle_query(vehicle_query(context.latest) or context.latest)
        ):
            # The model receives the ordered live candidates and resolves arbitrary
            # references such as position, colour, price, or model description.
            return clarify(
                "I need to resolve which displayed vehicle you mean before showing its details.",
                "get_vehicle",
                fields=("id",),
            )
        if context.active_vehicle:
            return tool("local-vehicle", "get_vehicle", {"id": context.active_vehicle})
        return clarify(
            "Please select a vehicle card first and I can show its current details.",
            "get_vehicle",
            fields=("id",),
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
        fresh_filters = vehicle_filters(context.latest)
        if str(fresh_filters.get("q") or "").casefold() == "all":
            fresh_filters.pop("q")
        reset_ignored_fields = {"sort", "availability"}
        explicit_filters = set(fresh_filters) - reset_ignored_fields
        if (
            "all" in context.words
            and context.words.intersection({"car", "cars", "vehicle", "vehicles"})
            and not explicit_filters
        ):
            return tool("local-reset-vehicles", "reset_vehicle_search")
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
            return clarify(
                "Please identify which displayed vehicle you want to test drive.",
                "list_test_drive_slots",
                preconditions=("trusted_vehicle_id",),
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


def _referenced_displayed_vehicle(context: ConversationContext) -> str | None:
    ordinals = {"first": 0, "1": 0, "second": 1, "2": 1, "third": 2, "3": 2, "fourth": 3, "4": 3}
    reference_tokens = set(context.words) | set(re.findall(r"\b[1-4]\b", context.normalized))
    indexes = {ordinals[word] for word in reference_tokens if word in ordinals}
    if len(indexes) == 1:
        index = indexes.pop()
        if index < len(context.displayed_vehicle_ids):
            return context.displayed_vehicle_ids[index]
    query_words = set(context.words) - {"open", "view", "show", "me", "the", "full", "detail", "details", "car", "vehicle"}
    if query_words:
        matches = []
        for item in context.displayed_vehicles:
            searchable = " ".join(str(value or "") for value in item.values()).casefold()
            if all(word in searchable for word in query_words):
                matches.append(str(item.get("vehicleId") or ""))
        matches = [value for value in matches if value]
        if len(matches) == 1:
            return matches[0]
    return context.displayed_vehicle_ids[0] if len(context.displayed_vehicle_ids) == 1 else None
