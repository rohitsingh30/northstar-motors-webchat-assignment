"""Domain-goal to tool routing used by deterministic transition planning."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from webchat.orchestration.planning.ontology import GoalKey, Goals


@dataclass(frozen=True)
class ToolTransitionContext:
    user_text: str
    vehicle_search_state: dict[str, object] | None
    page_vehicles: list[dict[str, object]]


TransitionMethod = Callable[
    [GoalKey, dict[str, Any], dict[str, Any], ToolTransitionContext],
    tuple[str, dict[str, Any]],
]
FilterBuilder = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


class ToolTransitionRouter:
    """Map validated semantic goals to deterministic application tools."""

    CONTACT_FIELDS: ClassVar[set[str]] = {
        "firstName",
        "lastName",
        "email",
        "phone",
    }

    def __init__(self, vehicle_filter_builder: FilterBuilder):
        self.vehicle_filter_builder = vehicle_filter_builder
        self.routes: dict[GoalKey, TransitionMethod] = {
            Goals.VEHICLE_SEARCH: self._vehicle_search,
            Goals.VEHICLE_APPLY_PREFERENCE: self._vehicle_search,
            Goals.VEHICLE_CONTINUE_SEARCH: self._vehicle_more,
            Goals.VEHICLE_COMPARE: self._vehicle_compare,
            Goals.VEHICLE_VIEW_DETAILS: self._vehicle_details,
            Goals.VEHICLE_CHECK_AVAILABILITY: self._vehicle_details,
            Goals.TEST_DRIVE_BOOK: self._test_drive,
            Goals.OFFER_BROWSE: self._offers,
            Goals.OFFER_VIEW_DETAILS: self._offer_details,
            Goals.OFFER_ENQUIRE: self._sales_enquiry,
            Goals.DEALERSHIP_FIND: self._dealerships,
            Goals.DEALERSHIP_VIEW_CONTACT: self._dealerships,
            Goals.DEALERSHIP_VIEW_DEPARTMENTS: self._no_argument_tool(
                "list_dealership_departments"
            ),
            Goals.DEALERSHIP_VIEW_OPENING_HOURS: self._opening_hours,
            Goals.DEALERSHIP_SEND_MESSAGE: self._dealership_message,
            Goals.WORKSHOP_FIND_LOCATIONS: self._no_argument_tool(
                "list_workshop_locations"
            ),
            Goals.WORKSHOP_BROWSE_SERVICES: self._no_argument_tool(
                "list_service_types"
            ),
            Goals.WORKSHOP_CHECK_SERVICE: self._workshop_service_information,
            Goals.WORKSHOP_BOOK_SERVICE: self._workshop_booking,
            Goals.WORKSHOP_FIND_BOOKING: self._booking_lookup,
            Goals.WORKSHOP_CHANGE_BOOKING: self._booking_change,
            Goals.WORKSHOP_CANCEL_BOOKING: self._booking_cancel,
            Goals.PART_EXCHANGE_ESTIMATE: self._part_exchange_estimate,
            Goals.PART_EXCHANGE_REQUEST_FOLLOW_UP: self._part_exchange_follow_up,
            Goals.SALES_REQUEST_CALLBACK: self._callback,
            Goals.SALES_ENQUIRE: self._sales_enquiry,
            Goals.SALES_REGISTER_INTEREST: self._vehicle_interest,
            Goals.BUSINESS_FINANCE_INFORMATION: self._business_information,
            Goals.BUSINESS_PRIVACY_INFORMATION: self._business_information,
            Goals.BUSINESS_PART_EXCHANGE_INFORMATION: self._business_information,
            Goals.BUSINESS_GENERAL_INFORMATION: self._business_information,
        }

    def resolve(
        self,
        key: GoalKey,
        arguments: dict[str, Any],
        state: dict[str, Any],
        context: ToolTransitionContext,
    ) -> tuple[str, dict[str, Any]]:
        route = self.routes.get(key)
        if route is None:
            raise ValueError(f"unsupported turn-plan goal: {key}")
        return route(key, arguments, state, context)

    def _vehicle_search(self, key, arguments, state, context):
        del key
        filters = self.vehicle_filter_builder(arguments, state)
        if arguments.get("referenceScope") == "currentPage":
            filters["vehicleIds"] = [
                str(item["vehicleId"])
                for item in context.page_vehicles
                if item.get("vehicleId")
            ]
            filters["limit"] = int(arguments.get("resultLimit") or 3)
            return "select_page_vehicles", filters
        if arguments.get("resolveAgainstLiveFacets"):
            # Resolve free-form identity terms against live catalogue facets before
            # applying typed dimensions. The follow-up re-enters this same plan
            # pipeline with canonical make/model values.
            return "get_vehicle_facets", {}
        return "search_vehicles", filters

    @staticmethod
    def _vehicle_more(key, arguments, state, context):
        del key, arguments, state
        if context.vehicle_search_state:
            filters = dict(context.vehicle_search_state.get("filters") or {})
            filters["page"] = int(context.vehicle_search_state.get("page") or 1) + 1
            return "search_vehicles", filters
        raise ValueError("vehicle_more requires an active search")

    @staticmethod
    def _vehicle_compare(key, arguments, state, context):
        del key, state, context
        vehicle_ids = list(arguments.get("vehicleIds") or [])
        if len(vehicle_ids) >= 2:
            return "compare_vehicles", {"vehicleIds": vehicle_ids[:3]}
        queries = list(arguments.get("vehicleQueries") or [])
        if len(queries) >= 2:
            return "compare_vehicle_models", {"queries": queries[:3]}
        raise ValueError("vehicle comparison requires resolved vehicles or model queries")

    @staticmethod
    def _vehicle_details(key, arguments, state, context):
        query = arguments.get("query")
        vehicle_id = arguments.get("vehicleId")
        if not vehicle_id and not query and arguments.get("reuseActiveEntity"):
            vehicle_id = _state_entity(state, "vehicleId")
        if vehicle_id:
            tool = (
                "get_vehicle"
                if key == Goals.VEHICLE_VIEW_DETAILS
                else "get_vehicle_availability"
            )
            return tool, {"id": vehicle_id}
        return "search_vehicles", {"q": query or context.user_text}

    @staticmethod
    def _test_drive(key, arguments, state, context):
        del key
        query = arguments.get("query")
        vehicle_id = arguments.get("vehicleId")
        if not vehicle_id and not query and arguments.get("reuseActiveEntity"):
            vehicle_id = _state_entity(state, "vehicleId")
        if vehicle_id:
            return "list_test_drive_slots", {"vehicleId": vehicle_id}
        return "search_vehicles", {"q": query or context.user_text}

    @staticmethod
    def _offers(key, arguments, state, context):
        del key, state, context
        return "list_offers", _only(arguments, {"make", "productType"})

    @staticmethod
    def _offer_details(key, arguments, state, context):
        del key, state, context
        offer_id = arguments.get("offerId")
        if offer_id:
            return "get_offer", {"id": offer_id}
        return "list_offers", _only(arguments, {"make", "productType"})

    @staticmethod
    def _dealerships(key, arguments, state, context):
        del key, state, context
        return "list_dealerships", _only(arguments, {"town"})

    @staticmethod
    def _opening_hours(key, arguments, state, context):
        del key, state, context
        return "list_opening_hours", _only(
            arguments, {"town", "day", "department"}
        )

    @staticmethod
    def _workshop_service_information(key, arguments, state, context):
        del key
        explicit_query = arguments.get("serviceQuery") or arguments.get("query")
        service_type_id = arguments.get("serviceTypeId")
        if arguments.get("reuseActiveEntity") and not explicit_query and not service_type_id:
            service_type_id = _state_entity(state, "serviceTypeId")
        if service_type_id:
            return "get_service_information", {"serviceTypeId": service_type_id}
        service_query = _service_query(arguments, state) or context.user_text
        return "get_service_information", {"q": service_query[:200]}

    @staticmethod
    def _workshop_booking(key, arguments, state, context):
        del key, context
        explicit_query = arguments.get("serviceQuery") or arguments.get("query")
        service_type_id = arguments.get("serviceTypeId")
        if arguments.get("reuseActiveEntity") and not explicit_query and not service_type_id:
            service_type_id = _state_entity(state, "serviceTypeId")
        service_query = _service_query(
            arguments, state if arguments.get("reuseActiveEntity") else {}
        )
        if not service_type_id and not service_query:
            return "list_service_types", {}
        filters = _only(arguments, {"dealershipId", "dateFrom", "dateTo"})
        if arguments.get("town"):
            filters["dealershipTown"] = arguments["town"]
        if service_type_id:
            filters["serviceTypeId"] = service_type_id
        else:
            filters["serviceTypeName"] = str(service_query)[:80]
        return "list_workshop_slots", filters

    @staticmethod
    def _booking_lookup(key, arguments, state, context):
        del key, arguments, state, context
        return "request_workshop_booking_lookup_form", {"mode": "lookup"}

    @staticmethod
    def _booking_change(key, arguments, state, context):
        del key, context
        if state.get("domain") == "workshop" and state.get("stage") == "verified":
            return "prepare_workshop_amendment", _only(
                arguments, {"slotId", "mileage", "notes"}
            )
        return "request_workshop_booking_lookup_form", {"mode": "amend"}

    @staticmethod
    def _booking_cancel(key, arguments, state, context):
        del key, arguments, context
        if state.get("domain") == "workshop" and state.get("stage") == "verified":
            return "prepare_workshop_cancellation", {}
        return "request_workshop_booking_lookup_form", {"mode": "cancel"}

    @staticmethod
    def _part_exchange_estimate(key, arguments, state, context):
        del key, state, context
        estimate = _only(arguments, {"registration", "mileage", "condition"})
        if set(estimate) == {"registration", "mileage", "condition"}:
            return "estimate_part_exchange", estimate
        return "request_part_exchange_estimate_form", estimate

    @classmethod
    def _part_exchange_follow_up(cls, key, arguments, state, context):
        del key, state, context
        return "prepare_part_exchange", _only(
            arguments,
            cls.CONTACT_FIELDS
            | {"dealershipId", "registration", "mileage", "condition", "vehicleId"},
        )

    @classmethod
    def _callback(cls, key, arguments, state, context):
        del key, state, context
        return "prepare_callback", _only(
            arguments,
            cls.CONTACT_FIELDS
            | {
                "dealershipId",
                "department",
                "reason",
                "vehicleId",
                "preferredTime",
            },
        )

    @classmethod
    def _sales_enquiry(cls, key, arguments, state, context):
        del key, state, context
        fields = _only(
            arguments,
            cls.CONTACT_FIELDS
            | {"dealershipId", "vehicleId", "message", "enquiryType"},
        )
        fields.setdefault("enquiryType", "general")
        return "prepare_sales_enquiry", fields

    @classmethod
    def _vehicle_interest(cls, key, arguments, state, context):
        del key, state, context
        return "prepare_vehicle_interest", _only(
            arguments, cls.CONTACT_FIELDS | {"vehicleId"}
        )

    @classmethod
    def _dealership_message(cls, key, arguments, state, context):
        del key, state, context
        return "prepare_dealership_message", _only(
            arguments,
            cls.CONTACT_FIELDS
            | {
                "dealershipId",
                "department",
                "subject",
                "message",
                "preferredContactMethod",
            },
        )

    @staticmethod
    def _business_information(key, arguments, state, context):
        del arguments, state
        topic = {
            Goals.BUSINESS_FINANCE_INFORMATION: "finance",
            Goals.BUSINESS_PRIVACY_INFORMATION: "privacy",
            Goals.BUSINESS_PART_EXCHANGE_INFORMATION: "part_exchange",
            Goals.BUSINESS_GENERAL_INFORMATION: "general",
        }[key]
        return "get_business_information", {
            "topic": topic,
            "question": context.user_text,
        }

    @staticmethod
    def _no_argument_tool(tool_name: str) -> TransitionMethod:
        def route(key, arguments, state, context):
            del key, arguments, state, context
            return tool_name, {}

        return route


def _only(arguments: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    return {
        key: value
        for key, value in arguments.items()
        if key in allowed and value is not None
    }


def _state_entity(state: dict[str, Any], name: str) -> Any | None:
    return (state.get("entities") or {}).get(name)


def _service_query(arguments: dict[str, Any], state: dict[str, Any]) -> str | None:
    value = arguments.get("serviceQuery") or arguments.get("query")
    if value:
        return str(value)
    value = (state.get("entities") or {}).get("serviceQuery")
    return str(value) if value else None
