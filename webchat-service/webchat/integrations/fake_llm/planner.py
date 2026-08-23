"""Fake semantic planner that emits the hosted provider's typed contract."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from webchat.integrations.contracts import ProviderReply, TurnPlan
from webchat.orchestration.planning.ontology import GoalKey, Goals
from webchat.orchestration.planning.turn_plan import parse_turn_plan
from webchat.orchestration.routing import DeterministicApplicationRouter
from webchat.orchestration.routing.context import ConversationContext
from webchat.orchestration.routing.parsers import (
    location_query,
    vehicle_query,
    workshop_filters,
)
from webchat.orchestration.routing.plan_adapter import (
    ToolCallPlanAdapter,
    is_vehicle_status_question,
)


def _plan(key: GoalKey, response: str = "", **arguments: Any) -> TurnPlan:
    """Validate local output through exactly the schema used by OpenAI."""
    payload = {
        "version": 2,
        "domain": key.domain,
        "goal": key.goal,
        **{key: value for key, value in arguments.items() if value is not None},
    }
    if response:
        payload["response"] = response
    return parse_turn_plan(payload)


class DeterministicGoalRules:
    """Handle semantic goals that should not depend on fake-only tool chaining."""

    def plan(self, context: ConversationContext) -> TurnPlan | None:
        handlers: tuple[Callable[[ConversationContext], TurnPlan | None], ...] = (
            self._workshop,
            self._offer_enquiry,
            self._offer_details,
            self._vehicle_continuation,
            self._vehicle_preferences,
        )
        return next((plan for handler in handlers if (plan := handler(context))), None)

    @staticmethod
    def _workshop(context: ConversationContext) -> TurnPlan | None:
        words = context.words
        existing = words.intersection(
            {"existing", "find", "lookup", "change", "amend", "cancel"}
        )
        if words.intersection({"booking", "appointment"}) and existing:
            key = (
                Goals.WORKSHOP_CANCEL_BOOKING
                if "cancel" in words
                else Goals.WORKSHOP_CHANGE_BOOKING
                if words.intersection({"change", "amend"})
                else Goals.WORKSHOP_FIND_BOOKING
            )
            return _plan(key)

        if (
            context.has_active_workshop_service
            and words.intersection({"location", "workshop"})
            and re.search(
                r"\b(?:for|about)\s+(?:it|that|this|the\s+service)\b",
                context.latest,
                re.IGNORECASE,
            )
        ):
            return _plan(
                Goals.WORKSHOP_BOOK_SERVICE,
                town=location_query(context.latest),
                reuseActiveEntity=True,
            )

        if "workshop" in words and words.intersection(
            {"where", "location", "locations", "address"}
        ):
            return _plan(Goals.WORKSHOP_FIND_LOCATIONS)

        if _named_service_support_request(context.latest):
            return _plan(
                Goals.WORKSHOP_CHECK_SERVICE,
                serviceQuery=context.latest[:200],
            )

        service_context = _has_named_service(context) or bool(
            words.intersection(
                {"workshop", "service", "services", "servicing", "mot"}
            )
        )
        information_request = bool(
            words.intersection(
                {"price", "cost", "duration", "include", "includes", "inclusions"}
            )
            or "how much" in context.normalized
            or "how long" in context.normalized
        )
        if service_context and information_request:
            return _plan(
                Goals.WORKSHOP_CHECK_SERVICE,
                serviceQuery=context.latest[:200],
            )

        asks_for_catalogue = bool(
            words.intersection({"service", "services"})
            and words.intersection({"type", "types"})
        ) or bool(
            "services" in words
            and words.intersection(
                {"what", "which", "list", "available", "provide", "offer", "support"}
            )
        )
        if asks_for_catalogue:
            return _plan(Goals.WORKSHOP_BROWSE_SERVICES)

        booking_request = bool(
            words.intersection({"book", "booking", "schedule", "appointment"})
            or ("mot" in words and words.intersection({"need", "want"}))
            or (
                words.intersection({"need", "want"})
                and words.intersection({"fit", "fitting"})
                and words.intersection({"tyre", "tyres"})
            )
            or (
                "workshop" in words
                and words.intersection({"availability", "find", "times", "slots"})
            )
        )
        if service_context and booking_request:
            arguments: dict[str, Any] = {}
            if _has_named_service(context):
                arguments["serviceQuery"] = context.latest[:200]
            parsed_filters = workshop_filters(context.latest)
            arguments.update(
                {
                    key: value
                    for key, value in parsed_filters.items()
                    if key in {"dateFrom", "dateTo"}
                }
            )
            town = parsed_filters.get("dealershipTown") or location_query(context.latest)
            if town:
                arguments["town"] = town
            return _plan(Goals.WORKSHOP_BOOK_SERVICE, **arguments)
        return None

    @staticmethod
    def _vehicle_continuation(context: ConversationContext) -> TurnPlan | None:
        normalized = context.normalized
        if re.search(
            r"\b(?:show(?: me)?|see|load|get|give me)\s+more\b"
            r"|\bnext\s+(?:page|results?)\b",
            normalized,
        ):
            return _plan(Goals.VEHICLE_CONTINUE_SEARCH)

        words = context.words
        status_words = words.intersection(
            {"available", "availability", "reserved", "sold"}
        )
        if status_words and words.intersection({"mean", "means", "meaning"}):
            # This is a terminology question, not a request for live stock status.
            return None
        named_query = vehicle_query(context.latest)
        if named_query and not _is_deictic_vehicle_query(named_query):
            if is_vehicle_status_question(context):
                return _plan(Goals.VEHICLE_CHECK_AVAILABILITY, query=named_query)
            if "test" in words and "drive" in words:
                return _plan(Goals.TEST_DRIVE_BOOK, query=named_query)

        if context.active_vehicle and context.refers_to_active_vehicle():
            if "interest" in words:
                return _plan(
                    Goals.SALES_REGISTER_INTEREST,
                    vehicleId=context.active_vehicle,
                )
            if status_words:
                return _plan(
                    Goals.VEHICLE_CHECK_AVAILABILITY,
                    vehicleId=context.active_vehicle,
                )
            if "test" in words and "drive" in words:
                return _plan(Goals.TEST_DRIVE_BOOK, vehicleId=context.active_vehicle)
            if words.intersection(
                {"detail", "details", "price", "mileage", "fuel", "gearbox", "payment"}
            ):
                return _plan(
                    Goals.VEHICLE_VIEW_DETAILS, vehicleId=context.active_vehicle
                )

        selected_ids = _referenced_vehicle_ids(context)
        if "compare" in words and len(selected_ids) >= 2:
            return _plan(Goals.VEHICLE_COMPARE, vehicleIds=selected_ids[:3])
        if not selected_ids:
            return None
        vehicle_id = selected_ids[0]
        if "interest" in words:
            return _plan(Goals.SALES_REGISTER_INTEREST, vehicleId=vehicle_id)
        if words.intersection({"available", "availability", "reserved", "sold"}):
            return _plan(Goals.VEHICLE_CHECK_AVAILABILITY, vehicleId=vehicle_id)
        if "test" in words and "drive" in words:
            return _plan(Goals.TEST_DRIVE_BOOK, vehicleId=vehicle_id)
        if words.intersection(
            {"detail", "details", "price", "mileage", "fuel", "gearbox"}
        ):
            return _plan(Goals.VEHICLE_VIEW_DETAILS, vehicleId=vehicle_id)
        return None

    @staticmethod
    def _vehicle_preferences(context: ConversationContext) -> TurnPlan | None:
        normalized = context.normalized
        dimension = next(
            (
                value
                for pattern, value in (
                    (r"\bbudgets?\b|\bset (?:a|my) (?:price|budget)\b", "budgets"),
                    (r"\bmileages?\b|\blow[- ]mileage choices?\b", "mileages"),
                    (r"\bmakes?\b", "makes"),
                    (r"\bmodels?\b", "models"),
                    (r"\bfuel(?: types?)?\b", "fuelTypes"),
                    (r"\btransmissions?\b", "transmissions"),
                    (r"\bbody styles?\b", "bodyStyles"),
                )
                if re.search(pattern, normalized)
                and context.words.intersection(
                    {"choose", "choices", "options", "select", "set", "help"}
                )
            ),
            None,
        )
        broad_request = any(
            phrase in normalized
            for phrase in (
                "help me find another car",
                "find another car",
                "help me find a car",
            )
        )
        if dimension or broad_request:
            return _plan(
                Goals.VEHICLE_CHOOSE_PREFERENCES,
                preferenceDimension=dimension or "startingPoint",
                response=(
                    "Tell me what matters most, or choose a starting point below."
                    if not dimension
                    else "Choose an option below."
                ),
            )
        return None

    @staticmethod
    def _offer_details(context: ConversationContext) -> TurnPlan | None:
        words = context.words
        if not words.intersection({"offer", "offers"}) or not words.intersection(
            {"detail", "details", "explain", "more"}
        ):
            return None
        offer_id = _positional_id(
            context.normalized,
            list(context.displayed_offer_ids),
        ) or context.active_offer
        return _plan(Goals.OFFER_VIEW_DETAILS, offerId=offer_id)

    @staticmethod
    def _offer_enquiry(context: ConversationContext) -> TurnPlan | None:
        if not context.active_offer or not context.words.intersection(
            {
                "buy",
                "buying",
                "enquire",
                "enquiry",
                "inquiry",
                "interest",
                "interested",
            }
        ):
            return None
        return _plan(
            Goals.OFFER_ENQUIRE,
            offerId=context.active_offer,
            message=context.latest[:1_000],
        )


class DeterministicTurnPlanner:
    """Fake provider implementation with the same typed output boundary as OpenAI."""

    def __init__(
        self,
        application_router: DeterministicApplicationRouter | None = None,
        rules: DeterministicGoalRules | None = None,
        adapter: ToolCallPlanAdapter | None = None,
    ) -> None:
        self.application_router = (
            application_router or DeterministicApplicationRouter()
        )
        self.rules = rules or DeterministicGoalRules()
        self.adapter = adapter or ToolCallPlanAdapter()

    async def generate_turn(self, messages: list[dict[str, Any]]) -> ProviderReply:
        context = ConversationContext.from_messages(messages)
        if context.has_tool_result:
            reply = self.application_router.route(messages)
            if reply is not None:
                return self._provider_plan(reply, context)

        if plan := self.rules.plan(context):
            return ProviderReply("", plan=plan)
        if reply := self.application_router.route(messages):
            return self._provider_plan(reply, context)

        greeting = bool(context.words.intersection({"hello", "hi", "hey"}))
        response = (
            "Hello — I can help you find vehicles, current offers, dealerships, or "
            "services. For example, try ‘cars below £35,000’."
            if greeting
            else "I can help with vehicles, offers, dealerships, and servicing. "
            "Try ‘cars below £35,000’ or ask about current offers."
        )
        return ProviderReply(
            "", plan=_plan(Goals.CONVERSATION_RESPOND, response=response)
        )

    def _provider_plan(
        self, reply: ProviderReply, context: ConversationContext
    ) -> ProviderReply:
        if len(reply.tool_calls) == 1:
            return ProviderReply(
                "", plan=self.adapter.plan(reply.tool_calls[0], context)
            )
        response = reply.text.strip()
        if response == "Tell me what matters most, or choose a starting point below.":
            plan = _plan(
                Goals.VEHICLE_CHOOSE_PREFERENCES,
                response=response,
                preferenceDimension="startingPoint",
            )
        elif response.startswith(("Please ", "I need to resolve", "Which ")):
            plan = _plan(Goals.CONVERSATION_CLARIFY, response=response)
        else:
            plan = _plan(Goals.CONVERSATION_RESPOND, response=response)
        return ProviderReply("", plan=plan)


def _has_named_service(context: ConversationContext) -> bool:
    service_words = context.words.intersection(
        {
            "mot",
            "interim",
            "major",
            "full",
            "brake",
            "tyre",
            "tyres",
            "diagnostic",
            "oil",
            "inspection",
            "annual",
        }
    )
    explicit_label = bool(
        re.search(r"\bbook\s+service\s*:\s*\S", context.latest, re.IGNORECASE)
    )
    return bool(service_words or explicit_label)


def _named_service_support_request(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    if re.search(
        r"\b(?:what|which|list|show)\b.*\bservice(?:s|\s+types?)\b"
        r"|\bservice\s+types?\b",
        normalized,
    ):
        return False
    return bool(
        re.search(
            r"\bdo you do\s+\S"
            r"|\bdo you (?:offer|provide|support)\s+.+?\s+as a service[?.!]*$"
            r"|\bis .+? supported(?:\s+as a service)?[?.!]*$"
            r"|\bis .+? available\s+as a service[?.!]*$",
            normalized,
        )
    )


def _is_deictic_vehicle_query(value: str) -> bool:
    return set(value.lower().split()).issubset(
        {"this", "that", "the", "selected", "current", "vehicle", "car", "it"}
    )


def _referenced_vehicle_ids(context: ConversationContext) -> list[str]:
    candidates = list(context.displayed_vehicle_ids)
    if not candidates:
        return []
    normalized = context.normalized
    if match := re.search(r"\bfirst\s+(two|three)\b", normalized):
        count = 2 if match.group(1) == "two" else 3
        return candidates[:count]
    if selected := _positional_id(normalized, candidates):
        return [selected]

    matched: list[str] = []
    for item in context.displayed_vehicles:
        searchable = " ".join(
            str(item.get(field) or "").lower()
            for field in ("make", "model", "variant", "colour")
        )
        meaningful = [
            word for word in re.findall(r"[a-z0-9]+", searchable) if len(word) > 2
        ]
        if meaningful and any(
            re.search(rf"\b{re.escape(word)}\b", normalized) for word in meaningful
        ):
            vehicle_id = str(item.get("vehicleId") or "")
            if vehicle_id in candidates:
                matched.append(vehicle_id)
    return list(dict.fromkeys(matched)) if len(set(matched)) == 1 else []


def _positional_id(text: str, candidates: list[str]) -> str | None:
    positions = {"first": 0, "second": 1, "third": 2, "fourth": 3}
    for word, index in positions.items():
        if re.search(rf"\b{word}\b", text) and index < len(candidates):
            return candidates[index]
    if "last" in text and candidates:
        return candidates[-1]
    if len(candidates) == 1 and re.search(
        r"\b(?:this|that|the)\s+(?:one|car|vehicle|offer)\b", text
    ):
        return candidates[0]
    return None
