"""Execute allow-listed structured actions emitted by application views."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from webchat.domain.workflows import offer_enquiry_fields
from webchat.orchestration.context import (
    current_vehicle_reference_context,
    current_vehicle_search_state,
)
from webchat.orchestration.tools.contracts import ToolExecutor


@dataclass(frozen=True)
class StructuredActionExecution:
    """The concrete catalogue operation executed for a trusted widget action."""

    tool_name: str
    arguments: dict[str, Any]
    result: Any


ActionHandler = Callable[[dict, str, list, dict], Awaitable[StructuredActionExecution | None]]


class StructuredActionHandler:
    """Execute only allow-listed actions emitted by application-owned UI views."""

    def __init__(self, tools: ToolExecutor):
        self.tools = tools
        self._handlers: dict[str, ActionHandler] = {
            "next_vehicle_page": self._next_vehicle_page,
            "search_vehicle_inventory": self._search_vehicle_inventory,
            "compare_displayed_vehicles": self._compare_displayed_vehicles,
            "select_test_drive_vehicle": self._select_test_drive_vehicle,
            "start_vehicle_interest": self._start_vehicle_interest,
            "start_sales_enquiry": self._start_sales_enquiry,
            "start_offer_enquiry": self._start_offer_enquiry,
            "view_offer": self._view_offer,
            "apply_vehicle_preference": self._apply_vehicle_preference,
            "choose_vehicle_filter": self._choose_vehicle_filter,
            "clear_vehicle_filter": self._clear_vehicle_filter,
            "reset_vehicle_search": self._reset_vehicle_search,
            "select_workshop_service": self._select_workshop_service,
            "start_dealership_workshop": self._start_dealership_workshop,
            "try_workshop_location": self._try_workshop_location,
            "show_workshop_services": self._show_workshop_services,
            "start_callback": self._start_callback,
            "start_dealership_message": self._start_dealership_message,
            "show_dealerships": self._show_dealerships,
            "show_opening_hours": self._show_opening_hours,
            "show_dealership_contact_options": self._show_dealership_contact_options,
        }

    async def execute(
        self,
        action: dict,
        conversation_id: str,
        messages,
        workflow_state: dict | None = None,
    ):
        handler = self._handlers.get(str(action.get("type") or ""))
        if handler is None:
            return None
        return await handler(action, conversation_id, messages, workflow_state or {})

    async def _next_vehicle_page(self, action, conversation_id, messages, workflow_state):
        state = current_vehicle_search_state(messages)
        if state is None:
            return None
        filters = dict(state["filters"])
        filters["page"] = int(state["page"]) + 1
        return await self._execute("search_vehicles", filters, conversation_id)

    async def _search_vehicle_inventory(self, action, conversation_id, messages, workflow_state):
        del action, messages
        filters = (
            dict(workflow_state.get("constraints") or {})
            if workflow_state.get("activeWorkflow") == "vehicle_search"
            else {}
        )
        filters.pop("page", None)
        return await self._execute("search_vehicles", filters, conversation_id)

    async def _compare_displayed_vehicles(self, action, conversation_id, messages, workflow_state):
        candidates = current_vehicle_reference_context(messages)
        vehicle_ids = [str(item["vehicleId"]) for item in candidates[:3]]
        if len(vehicle_ids) < 2:
            return None
        return await self._execute("compare_vehicles", {"vehicleIds": vehicle_ids}, conversation_id)

    async def _select_test_drive_vehicle(self, action, conversation_id, messages, workflow_state):
        return await self._execute(
            "list_test_drive_slots", {"vehicleId": action["vehicleId"]}, conversation_id
        )

    async def _start_vehicle_interest(self, action, conversation_id, messages, workflow_state):
        return await self._execute(
            "prepare_vehicle_interest", {"vehicleId": action["vehicleId"]}, conversation_id
        )

    async def _start_sales_enquiry(self, action, conversation_id, messages, workflow_state):
        return await self._execute(
            "prepare_sales_enquiry",
            {"vehicleId": action["vehicleId"], "enquiryType": "availability"},
            conversation_id,
        )

    async def _start_offer_enquiry(self, action, conversation_id, messages, workflow_state):
        offer_result = await self.tools.execute(
            "get_offer", {"id": action["offerId"]}, conversation_id
        )
        offer = offer_result.facts if isinstance(offer_result.facts, dict) else {}
        return await self._execute(
            "prepare_sales_enquiry",
            offer_enquiry_fields(offer),
            conversation_id,
        )

    async def _view_offer(self, action, conversation_id, messages, workflow_state):
        return await self._execute("get_offer", {"id": action["offerId"]}, conversation_id)

    async def _apply_vehicle_preference(self, action, conversation_id, messages, workflow_state):
        filters = _preference_action_filters(workflow_state, action)
        return await self._execute("search_vehicles", filters, conversation_id)

    async def _choose_vehicle_filter(self, action, conversation_id, messages, workflow_state):
        dimensions = {
            "make": "makes",
            "model": "models",
            "fuelType": "fuelTypes",
            "transmission": "transmissions",
            "bodyStyle": "bodyStyles",
            "maxPricePence": "budgets",
            "maxMileage": "mileages",
        }
        return await self._execute(
            "show_vehicle_preferences",
            {"dimension": dimensions[action["vehicleFilter"]], "reuseCurrentSearch": True},
            conversation_id,
        )

    async def _clear_vehicle_filter(self, action, conversation_id, messages, workflow_state):
        filters = _current_vehicle_filters(workflow_state)
        field = str(action["vehicleFilter"])
        filters.pop(field, None)
        opposing = {
            "make": "excludedMakes",
            "model": "excludedModels",
            "fuelType": "excludedFuelTypes",
            "transmission": "excludedTransmissions",
            "bodyStyle": "excludedBodyStyles",
        }
        if field in opposing:
            filters.pop(opposing[field], None)
        return await self._execute("search_vehicles", filters, conversation_id)

    async def _reset_vehicle_search(self, action, conversation_id, messages, workflow_state):
        del action, messages, workflow_state
        return await self._execute("reset_vehicle_search", {}, conversation_id)

    async def _select_workshop_service(self, action, conversation_id, messages, workflow_state):
        return await self._execute(
            "list_workshop_slots",
            {"serviceTypeId": action["serviceTypeId"]},
            conversation_id,
        )

    async def _start_dealership_workshop(
        self, action, conversation_id, messages, workflow_state
    ):
        return await self._execute(
            "list_workshop_slots",
            {"dealershipId": action["dealershipId"]},
            conversation_id,
        )

    async def _try_workshop_location(self, action, conversation_id, messages, workflow_state):
        return await self._execute(
            "list_workshop_slots",
            {
                "serviceTypeId": action["serviceTypeId"],
                "dealershipId": action["dealershipId"],
            },
            conversation_id,
        )

    async def _show_workshop_services(self, action, conversation_id, messages, workflow_state):
        return await self._execute("list_service_types", {}, conversation_id)

    async def _start_callback(self, action, conversation_id, messages, workflow_state):
        return await self._execute("prepare_callback", {}, conversation_id)

    async def _start_dealership_message(self, action, conversation_id, messages, workflow_state):
        return await self._execute("prepare_dealership_message", {}, conversation_id)

    async def _show_dealerships(self, action, conversation_id, messages, workflow_state):
        return await self._execute("list_dealerships", {}, conversation_id)

    async def _show_opening_hours(self, action, conversation_id, messages, workflow_state):
        return await self._execute("list_opening_hours", {}, conversation_id)

    async def _show_dealership_contact_options(
        self, action, conversation_id, messages, workflow_state
    ):
        return await self._execute("show_dealership_contact_options", {}, conversation_id)

    async def _execute(
        self, name: str, arguments: dict[str, Any], conversation_id: str
    ) -> StructuredActionExecution:
        result = await self.tools.execute(name, arguments, conversation_id)
        return StructuredActionExecution(name, arguments, result)


def _preference_action_filters(state: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    filters = _current_vehicle_filters(state)
    filters[str(action["vehicleFilter"])] = action["vehicleFilterValue"]
    return filters


def _current_vehicle_filters(state: dict[str, Any]) -> dict[str, Any]:
    filters = (
        dict(state.get("constraints") or {})
        if state.get("activeWorkflow") == "vehicle_search"
        else {}
    )
    filters.pop("preferenceDimension", None)
    filters.pop("page", None)
    return filters
