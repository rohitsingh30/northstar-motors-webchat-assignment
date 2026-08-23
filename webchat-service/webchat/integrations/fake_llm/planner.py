"""Deterministic offline provider that emits the same concrete calls as hosted mode."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from webchat.integrations.contracts import ProviderReply, ToolCall
from webchat.integrations.fake_llm.routing import DeterministicApplicationRouter
from webchat.integrations.fake_llm.routing.base import clarify
from webchat.integrations.fake_llm.routing.context import ConversationContext
from webchat.integrations.fake_llm.routing.parsers import location_query, workshop_filters


def _tool(name: str, **arguments: Any) -> ProviderReply:
    return ProviderReply("", [ToolCall(f"local-{name}", name, arguments)])


class DeterministicToolRules:
    """Offline-only language matching; production semantic decisions remain model-owned."""

    def route(self, context: ConversationContext) -> ProviderReply | None:
        handlers: tuple[Callable[[ConversationContext], ProviderReply | None], ...] = (
            self._knowledge,
            self._unsupported_business_policy,
            self._workshop,
            self._offer_enquiry,
            self._offer_details,
            self._vehicle_continuation,
            self._vehicle_preferences,
        )
        return next((reply for handler in handlers if (reply := handler(context))), None)

    @staticmethod
    def _knowledge(context: ConversationContext) -> ProviderReply | None:
        words = context.words
        if "pch" in words and words.intersection({"what", "mean", "means", "explain"}):
            return ProviderReply(
                "PCH means Personal Contract Hire. It is a lease: you pay rentals for an agreed "
                "term and mileage, then return the vehicle.",
                response_mode="answer",
                citation_ids=("customer.pch",),
            )
        if "pcp" in words and words.intersection({"what", "mean", "means", "explain"}):
            return ProviderReply(
                "PCP means Personal Contract Purchase. It uses monthly payments with an optional "
                "final payment if you want to keep the vehicle.",
                response_mode="answer",
                citation_ids=("customer.pcp",),
            )
        return None

    @staticmethod
    def _unsupported_business_policy(context: ConversationContext) -> ProviderReply | None:
        if not context.words.intersection({"pickup", "pick", "collect", "collection"}):
            return None
        if not context.words.intersection({"car", "vehicle", "it"}):
            return None
        return _tool("get_business_information", topic="general", question=context.latest[:1_000])

    @staticmethod
    def _workshop(context: ConversationContext) -> ProviderReply | None:
        words = context.words
        existing = words.intersection({"existing", "find", "lookup", "change", "amend", "cancel"})
        if words.intersection({"booking", "appointment"}) and existing:
            mode = (
                "cancel"
                if "cancel" in words
                else "amend"
                if words.intersection({"change", "amend"})
                else "lookup"
            )
            return _tool("request_workshop_booking_lookup_form", mode=mode)

        if (
            (context.active_workshop_service or context.active_workshop_service_id)
            and words.intersection({"location", "workshop"})
            and re.search(
                r"\b(?:for|about)\s+(?:it|that|this|the\s+service)\b",
                context.latest,
                re.IGNORECASE,
            )
        ):
            arguments = _active_service_arguments(context)
            if town := location_query(context.latest):
                arguments["dealershipTown"] = town
            return _tool("list_workshop_slots", **arguments)

        if "workshop" in words and words.intersection(
            {"where", "location", "locations", "address"}
        ):
            return _tool("list_workshop_locations")
        if _named_service_support_request(context.latest):
            return _tool("get_service_information", q=context.latest[:200])

        has_named_service = _has_named_service(context)
        service_context = has_named_service or bool(
            words.intersection({"workshop", "service", "services", "servicing", "mot"})
        )
        information_request = bool(
            words.intersection({"price", "cost", "duration", "include", "includes", "inclusions"})
            or "how much" in context.normalized
            or "how long" in context.normalized
        )
        if service_context and information_request:
            return _tool("get_service_information", q=context.latest[:200])

        asks_for_catalogue = bool(
            words.intersection({"service", "services"}) and words.intersection({"type", "types"})
        ) or bool(
            "services" in words
            and words.intersection(
                {"what", "which", "list", "available", "provide", "offer", "support"}
            )
        )
        if asks_for_catalogue:
            return _tool("list_service_types")

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
            if not has_named_service:
                return _tool("list_service_types")
            arguments = {
                key: value
                for key, value in workshop_filters(context.latest).items()
                if key in {"dateFrom", "dateTo", "dealershipTown"}
            }
            arguments["serviceTypeName"] = context.latest[:200]
            return _tool("list_workshop_slots", **arguments)
        return None

    @staticmethod
    def _vehicle_continuation(context: ConversationContext) -> ProviderReply | None:
        if re.search(
            r"\b(?:show(?: me)?|see|load|get|give me)\s+more\b|\bnext\s+(?:page|results?)\b",
            context.normalized,
        ):
            return _tool("search_vehicles", page=2)
        words = context.words
        status_words = words.intersection({"available", "availability", "reserved", "sold"})
        if context.active_vehicle and context.refers_to_active_vehicle():
            if "interest" in words:
                return _tool("prepare_vehicle_interest", vehicleId=context.active_vehicle)
            if status_words:
                return _tool("get_vehicle_availability", id=context.active_vehicle)
            if "test" in words and "drive" in words:
                return _tool("list_test_drive_slots", vehicleId=context.active_vehicle)
            if words.intersection(
                {"detail", "details", "price", "mileage", "fuel", "gearbox", "payment"}
            ):
                return _tool("get_vehicle", id=context.active_vehicle)
        selected_ids = _referenced_vehicle_ids(context)
        if "compare" in words and len(selected_ids) >= 2:
            return _tool("compare_vehicles", vehicleIds=selected_ids[:3])
        return None

    @staticmethod
    def _vehicle_preferences(context: ConversationContext) -> ProviderReply | None:
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
                if re.search(pattern, context.normalized)
                and context.words.intersection(
                    {"choose", "choices", "options", "select", "set", "help"}
                )
            ),
            None,
        )
        broad = any(
            phrase in context.normalized
            for phrase in (
                "help me find another car",
                "find another car",
                "help me find a car",
            )
        )
        if dimension or broad:
            return _tool("show_vehicle_preferences", dimension=dimension or "startingPoint")
        return None

    @staticmethod
    def _offer_details(context: ConversationContext) -> ProviderReply | None:
        if not context.words.intersection({"offer", "offers"}) or not context.words.intersection(
            {"detail", "details", "explain", "more"}
        ):
            return None
        offer_id = (
            _positional_id(context.normalized, list(context.displayed_offer_ids))
            or context.active_offer
        )
        if offer_id:
            return _tool("get_offer", id=offer_id)
        return clarify("Which offer do you mean?", "get_offer", fields=("id",))

    @staticmethod
    def _offer_enquiry(context: ConversationContext) -> ProviderReply | None:
        if not context.active_offer or not context.words.intersection(
            {"buy", "buying", "enquire", "enquiry", "inquiry", "interest", "interested"}
        ):
            return None
        return _tool(
            "prepare_sales_enquiry",
            message=context.latest[:1_000],
            enquiryType="finance",
        )


class DeterministicToolPlanner:
    """Offline provider implementation with the hosted direct-tool boundary."""

    def __init__(
        self,
        application_router: DeterministicApplicationRouter | None = None,
        rules: DeterministicToolRules | None = None,
    ) -> None:
        self.application_router = application_router or DeterministicApplicationRouter()
        self.rules = rules or DeterministicToolRules()

    async def generate_turn(self, messages: list[dict[str, Any]]) -> ProviderReply:
        pending = getattr(getattr(messages, "reviewer_context", None), "pending_interaction", None)
        latest = next(
            (
                str(message.get("content") or "")
                for message in reversed(messages)
                if message.get("role") == "user"
            ),
            "",
        )
        canonical_reply = latest.strip().casefold().rstrip(".!?")
        if pending and pending.get("kind") != "input":
            if canonical_reply == "yes":
                return ProviderReply("", interaction_decision="accept")
            if canonical_reply == "no":
                return ProviderReply("", interaction_decision="decline")
        context = ConversationContext.from_messages(messages)
        if context.has_tool_result and (reply := self.application_router.route(messages)):
            return reply
        if reply := self.rules.route(context):
            return reply
        if reply := self.application_router.route(messages):
            if reply.text == "Tell me what matters most, or choose a starting point below.":
                return _tool("show_vehicle_preferences", dimension="startingPoint")
            return reply
        greeting = bool(context.words.intersection({"hello", "hi", "hey"}))
        text = (
            "Hello — I can help you find vehicles, current offers, dealerships, or services. "
            "For example, try ‘cars below £35,000’."
            if greeting
            else "I can help with vehicles, offers, dealerships, and servicing. "
            "Try ‘cars below £35,000’ or ask about current offers."
        )
        return ProviderReply(text, response_mode="conversation")


def _active_service_arguments(context: ConversationContext) -> dict[str, Any]:
    if context.active_workshop_service_id:
        return {"serviceTypeId": context.active_workshop_service_id}
    if context.active_workshop_service:
        return {"serviceTypeName": context.active_workshop_service}
    return {}


def _has_named_service(context: ConversationContext) -> bool:
    return bool(
        context.words.intersection(
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
            }
        )
        or re.search(r"\bannual\s+service\b", context.latest, re.IGNORECASE)
        or re.search(r"\bbook\s+service\s*:\s*\S", context.latest, re.IGNORECASE)
    )


def _named_service_support_request(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    if re.search(
        r"\b(?:what|which|list|show)\b.*\bservice(?:s|\s+types?)\b|\bservice\s+types?\b",
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


def _referenced_vehicle_ids(context: ConversationContext) -> list[str]:
    if context.vehicle_ids:
        return list(dict.fromkeys(context.vehicle_ids))
    positions = {"first": 0, "second": 1, "third": 2}
    return [
        context.displayed_vehicle_ids[index]
        for word, index in positions.items()
        if word in context.words and index < len(context.displayed_vehicle_ids)
    ]


def _positional_id(text: str, ids: list[str]) -> str | None:
    for word, index in (("first", 0), ("second", 1), ("third", 2)):
        if word in text and index < len(ids):
            return ids[index]
    return ids[0] if len(ids) == 1 else None
