"""Adapt deterministic application routes to the canonical typed-plan boundary."""

from __future__ import annotations

from typing import Any, ClassVar

from webchat.integrations.contracts import ProviderReply, ToolCall, TurnPlan
from webchat.orchestration.planning.ontology import GoalKey, Goals
from webchat.orchestration.planning.turn_plan import parse_turn_plan

from .context import ConversationContext
from .parsers import vehicle_filters, vehicle_query
from .router import DeterministicApplicationRouter


def _plan(key: GoalKey, response: str = "", **arguments: Any) -> TurnPlan:
    payload = {
        "version": 2,
        "domain": key.domain,
        "goal": key.goal,
        **{name: value for name, value in arguments.items() if value is not None},
    }
    if response:
        payload["response"] = response
    return parse_turn_plan(payload)


def is_vehicle_status_question(context: ConversationContext) -> bool:
    if not context.words.intersection(
        {"available", "availability", "reserved", "sold"}
    ):
        return False
    if context.words.intersection(
        {"show", "find", "list", "browse", "cars", "vehicles"}
    ):
        return False
    return bool(
        context.words.intersection({"is", "are", "check", "status"})
        or context.refers_to_active_vehicle()
        or vehicle_query(context.latest)
    )


class ToolCallPlanAdapter:
    """Translate a deterministic application route into a semantic plan."""

    _PREFERENCE_DIMENSIONS: ClassVar[dict[str, str]] = {
        "maxPricePence": "budgets",
        "maxMileage": "mileages",
        "make": "makes",
        "model": "models",
        "fuelType": "fuelTypes",
        "transmission": "transmissions",
        "bodyStyle": "bodyStyles",
    }

    _SIMPLE_GOALS: ClassVar[dict[str, GoalKey]] = {
        "list_offers": Goals.OFFER_BROWSE,
        "list_dealership_departments": Goals.DEALERSHIP_VIEW_DEPARTMENTS,
        "list_opening_hours": Goals.DEALERSHIP_VIEW_OPENING_HOURS,
        "list_workshop_locations": Goals.WORKSHOP_FIND_LOCATIONS,
        "list_service_types": Goals.WORKSHOP_BROWSE_SERVICES,
        "get_service_information": Goals.WORKSHOP_CHECK_SERVICE,
        "list_workshop_slots": Goals.WORKSHOP_BOOK_SERVICE,
        "request_part_exchange_estimate_form": Goals.PART_EXCHANGE_ESTIMATE,
        "estimate_part_exchange": Goals.PART_EXCHANGE_ESTIMATE,
        "prepare_part_exchange": Goals.PART_EXCHANGE_REQUEST_FOLLOW_UP,
        "prepare_callback": Goals.SALES_REQUEST_CALLBACK,
        "prepare_sales_enquiry": Goals.SALES_ENQUIRE,
        "prepare_vehicle_interest": Goals.SALES_REGISTER_INTEREST,
        "prepare_dealership_message": Goals.DEALERSHIP_SEND_MESSAGE,
    }

    def plan(self, call: ToolCall, context: ConversationContext) -> TurnPlan:
        if call.name in {"search_vehicles", "get_vehicle_facets"}:
            return self._vehicle_search(call, context)
        if call.name in {"compare_vehicles", "compare_vehicle_models"}:
            values = (
                call.arguments.get("vehicleIds")
                or call.arguments.get("queries")
                or []
            )
            field = (
                "vehicleIds"
                if call.name == "compare_vehicles"
                else "vehicleQueries"
            )
            return _plan(Goals.VEHICLE_COMPARE, **{field: list(values)[:3]})
        if call.name in {"get_vehicle", "get_vehicle_availability"}:
            key = (
                Goals.VEHICLE_VIEW_DETAILS
                if call.name == "get_vehicle"
                else Goals.VEHICLE_CHECK_AVAILABILITY
            )
            return _plan(key, vehicleId=call.arguments.get("id"))
        if call.name == "list_test_drive_slots":
            return _plan(
                Goals.TEST_DRIVE_BOOK,
                vehicleId=call.arguments.get("vehicleId"),
            )
        if call.name == "list_dealerships":
            key = (
                Goals.DEALERSHIP_VIEW_CONTACT
                if context.words.intersection(
                    {"phone", "email", "telephone", "contact"}
                )
                else Goals.DEALERSHIP_FIND
            )
            return _plan(key, town=call.arguments.get("town"))
        if call.name == "request_workshop_booking_lookup_form":
            mode = call.arguments.get("mode")
            key = {
                "amend": Goals.WORKSHOP_CHANGE_BOOKING,
                "cancel": Goals.WORKSHOP_CANCEL_BOOKING,
            }.get(str(mode), Goals.WORKSHOP_FIND_BOOKING)
            return _plan(key)
        if call.name == "get_business_information":
            return _plan(self._business_goal(context))
        if key := self._SIMPLE_GOALS.get(call.name):
            return _plan(key, **self._canonical_arguments(call.arguments))
        return _plan(
            Goals.CONVERSATION_CLARIFY,
            response="Please use the available action in the current card to continue.",
        )

    @staticmethod
    def _business_goal(context: ConversationContext) -> GoalKey:
        if context.words.intersection({"privacy", "personal", "data"}):
            return Goals.BUSINESS_PRIVACY_INFORMATION
        if "finance" in context.words:
            return Goals.BUSINESS_FINANCE_INFORMATION
        if "part" in context.words and "exchange" in context.words:
            return Goals.BUSINESS_PART_EXCHANGE_INFORMATION
        return Goals.BUSINESS_GENERAL_INFORMATION

    @classmethod
    def _vehicle_search(
        cls, call: ToolCall, context: ConversationContext
    ) -> TurnPlan:
        source = (
            vehicle_filters(context.latest)
            if call.name == "get_vehicle_facets"
            else dict(call.arguments)
        )
        arguments = cls._canonical_arguments(source)
        if call.name == "get_vehicle_facets":
            arguments["resolveAgainstLiveFacets"] = True
        if context.words.intersection({"only", "actually", "instead", "change"}):
            arguments["refineCurrentSearch"] = True
        query = arguments.get("query") or vehicle_query(context.latest)
        if "test" in context.words and "drive" in context.words:
            return _plan(Goals.TEST_DRIVE_BOOK, query=query or context.latest[:200])
        if is_vehicle_status_question(context):
            return _plan(
                Goals.VEHICLE_CHECK_AVAILABILITY,
                query=query or context.latest[:200],
            )
        supplied_preferences = [
            (field, dimension, arguments[field])
            for field, dimension in cls._PREFERENCE_DIMENSIONS.items()
            if arguments.get(field) not in (None, "")
        ]
        if context.words.intersection({"prefer", "preference"}) and len(
            supplied_preferences
        ) == 1:
            _, dimension, value = supplied_preferences[0]
            return _plan(
                Goals.VEHICLE_APPLY_PREFERENCE,
                preferenceDimension=dimension,
                preferenceValue=value,
                **arguments,
            )
        return _plan(Goals.VEHICLE_SEARCH, **arguments)

    @staticmethod
    def _canonical_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
        aliases = {
            "q": "query",
            "dealershipTown": "town",
            "id": "dealershipId",
            "serviceTypeName": "serviceQuery",
        }
        return {aliases.get(key, key): value for key, value in arguments.items()}


class DeterministicPlanRouter:
    """Return fallback routes as typed plans so conformance always runs."""

    def __init__(
        self,
        application_router: DeterministicApplicationRouter | None = None,
        adapter: ToolCallPlanAdapter | None = None,
    ) -> None:
        self.application_router = (
            application_router or DeterministicApplicationRouter()
        )
        self.adapter = adapter or ToolCallPlanAdapter()

    def route(self, messages: list[dict[str, Any]]) -> ProviderReply | None:
        reply = self.application_router.route(messages)
        if reply is None or not reply.tool_calls:
            return reply
        if len(reply.tool_calls) != 1:
            raise ValueError("deterministic route must contain exactly one tool call")
        context = ConversationContext.from_messages(messages)
        return ProviderReply("", plan=self.adapter.plan(reply.tool_calls[0], context))
