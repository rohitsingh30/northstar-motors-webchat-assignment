"""Execute allow-listed structured actions emitted by application views."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from webchat.orchestration.context.builder import (
    current_vehicle_reference_context,
    current_vehicle_search_state,
)
from webchat.orchestration.planning.transitions import TransitionController
from webchat.orchestration.tools.contracts import ToolExecutor

ActionHandler = Callable[[dict, str, list, dict], Awaitable[Any | None]]


class StructuredActionHandler:
    """Execute only allow-listed actions emitted by application-owned UI views."""

    def __init__(self, tools: ToolExecutor, transitions: TransitionController):
        self.tools = tools
        self.transitions = transitions
        self._handlers: dict[str, ActionHandler] = {
            "next_vehicle_page": self._next_vehicle_page,
            "compare_displayed_vehicles": self._compare_displayed_vehicles,
            "select_test_drive_vehicle": self._select_test_drive_vehicle,
            "start_vehicle_interest": self._start_vehicle_interest,
            "start_sales_enquiry": self._start_sales_enquiry,
            "start_offer_enquiry": self._start_offer_enquiry,
            "apply_vehicle_preference": self._apply_vehicle_preference,
            "select_workshop_service": self._select_workshop_service,
            "try_workshop_location": self._try_workshop_location,
            "show_workshop_services": self._show_workshop_services,
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
        return await self.tools.execute("search_vehicles", filters, conversation_id)

    async def _compare_displayed_vehicles(
        self, action, conversation_id, messages, workflow_state
    ):
        candidates = current_vehicle_reference_context(messages)
        vehicle_ids = [str(item["vehicleId"]) for item in candidates[:4]]
        if len(vehicle_ids) < 2:
            return None
        return await self.tools.execute(
            "compare_vehicles", {"vehicleIds": vehicle_ids}, conversation_id
        )

    async def _select_test_drive_vehicle(
        self, action, conversation_id, messages, workflow_state
    ):
        return await self.tools.execute(
            "list_test_drive_slots", {"vehicleId": action["vehicleId"]}, conversation_id
        )

    async def _start_vehicle_interest(self, action, conversation_id, messages, workflow_state):
        return await self.tools.execute(
            "prepare_vehicle_interest", {"vehicleId": action["vehicleId"]}, conversation_id
        )

    async def _start_sales_enquiry(self, action, conversation_id, messages, workflow_state):
        return await self.tools.execute(
            "prepare_sales_enquiry",
            {"vehicleId": action["vehicleId"], "enquiryType": "availability"},
            conversation_id,
        )

    async def _start_offer_enquiry(self, action, conversation_id, messages, workflow_state):
        offer_result = await self.tools.execute(
            "get_offer", {"id": action["offerId"]}, conversation_id
        )
        offer = offer_result.facts if isinstance(offer_result.facts, dict) else {}
        return await self.tools.execute(
            "prepare_sales_enquiry",
            {
                "enquiryType": "finance",
                "message": self.transitions.offer_enquiry_message(offer),
            },
            conversation_id,
        )

    async def _apply_vehicle_preference(
        self, action, conversation_id, messages, workflow_state
    ):
        filters = self.transitions.preference_action_filters(workflow_state, action)
        return await self.tools.execute("search_vehicles", filters, conversation_id)

    async def _select_workshop_service(
        self, action, conversation_id, messages, workflow_state
    ):
        return await self.tools.execute(
            "list_workshop_slots",
            {"serviceTypeId": action["serviceTypeId"]},
            conversation_id,
        )

    async def _try_workshop_location(
        self, action, conversation_id, messages, workflow_state
    ):
        return await self.tools.execute(
            "list_workshop_slots",
            {
                "serviceTypeId": action["serviceTypeId"],
                "dealershipId": action["dealershipId"],
            },
            conversation_id,
        )

    async def _show_workshop_services(
        self, action, conversation_id, messages, workflow_state
    ):
        return await self.tools.execute("list_service_types", {}, conversation_id)
