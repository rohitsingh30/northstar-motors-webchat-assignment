"""Execute allow-listed structured actions emitted by application views."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from webchat.domain.interactions import ActionHandoff, PendingInteraction, preceding_interaction
from webchat.domain.workflows import offer_enquiry_fields
from webchat.orchestration.context import (
    current_vehicle_reference_context,
    current_vehicle_search_state,
)
from webchat.orchestration.state import WorkflowStateReducer
from webchat.orchestration.tools.contracts import ToolExecutor
from webchat.orchestration.workflow_kernel import merge_workshop_continuation_context


@dataclass(frozen=True)
class StructuredActionExecution:
    """The concrete catalogue operation executed for a trusted widget action."""

    tool_name: str
    arguments: dict[str, Any]
    result: Any
    workflow_state: dict[str, Any] | None = None
    transition: Literal["auto", "handoff"] = "auto"


@dataclass(frozen=True)
class WorkflowActionContext:
    """Declare how one trusted action continues an existing public workflow."""

    workflow: str
    explicit_fields: tuple[str, ...]
    preserved_fields: tuple[str, ...] = ()
    preserve_scheduling: bool = False


@dataclass(frozen=True)
class PendingActionToolBinding:
    """Trusted context recovered when typed language selects a visible action."""

    arguments: dict[str, Any]
    transition: Literal["auto", "handoff"] = "auto"


# This registry describes the business-operation targets of every structured action handler.  It
# lets the provider path recognize the same action without inspecting customer wording.  Actions
# with conditional or multi-step handlers list every possible terminal operation.
_ACTION_TOOL_TARGETS: dict[str, frozenset[str]] = {
    "next_vehicle_page": frozenset({"search_vehicles"}),
    "search_vehicle_inventory": frozenset({"search_vehicles"}),
    "compare_displayed_vehicles": frozenset({"compare_vehicles"}),
    "select_test_drive_vehicle": frozenset({"list_test_drive_slots"}),
    "start_vehicle_interest": frozenset({"prepare_vehicle_interest"}),
    "start_sales_enquiry": frozenset({"prepare_sales_enquiry"}),
    "start_offer_enquiry": frozenset({"prepare_sales_enquiry"}),
    "view_offer": frozenset({"get_offer"}),
    "apply_vehicle_preference": frozenset({"search_vehicles"}),
    "choose_vehicle_filter": frozenset({"show_vehicle_preferences"}),
    "clear_vehicle_filter": frozenset({"search_vehicles"}),
    "reset_vehicle_search": frozenset({"reset_vehicle_search"}),
    "select_workshop_service": frozenset({"list_workshop_slots"}),
    "start_dealership_workshop": frozenset({"list_workshop_slots"}),
    "try_workshop_location": frozenset({"list_workshop_slots"}),
    "show_workshop_services": frozenset({"list_service_types", "list_workshop_slots"}),
    "start_callback": frozenset({"prepare_callback"}),
    "start_dealership_message": frozenset({"prepare_dealership_message"}),
    "show_dealerships": frozenset({"list_dealerships"}),
    "show_opening_hours": frozenset({"list_opening_hours"}),
    "show_dealership_contact_options": frozenset({"show_dealership_contact_options"}),
}


_WORKFLOW_ACTION_CONTEXTS: dict[str, WorkflowActionContext] = {
    "select_test_drive_vehicle": WorkflowActionContext(
        workflow="test_drive",
        explicit_fields=("vehicleId",),
        preserve_scheduling=True,
    ),
    "select_workshop_service": WorkflowActionContext(
        workflow="workshop_booking",
        explicit_fields=("serviceTypeId",),
        preserved_fields=("dealershipId",),
        preserve_scheduling=True,
    ),
    "start_dealership_workshop": WorkflowActionContext(
        workflow="workshop_booking",
        explicit_fields=("dealershipId",),
        preserved_fields=("serviceTypeId",),
        preserve_scheduling=True,
    ),
    "try_workshop_location": WorkflowActionContext(
        workflow="workshop_booking",
        explicit_fields=("serviceTypeId", "dealershipId"),
        preserve_scheduling=True,
    ),
}


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
        if self._handlers.keys() != _ACTION_TOOL_TARGETS.keys():
            raise ValueError("structured action handlers and tool targets disagree")

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
        state = workflow_state or {}
        contextual_arguments = _workflow_action_arguments(action, state)
        if contextual_arguments is not None:
            action = {**action, "_workflowArguments": contextual_arguments}
        execution = await handler(action, conversation_id, messages, state)
        if (
            execution is not None
            and contextual_arguments is not None
            and execution.arguments != contextual_arguments
        ):
            raise ValueError("workflow action bypassed its preserved continuation context")
        return execution

    async def _next_vehicle_page(self, action, conversation_id, messages, workflow_state):
        state = current_vehicle_search_state(messages)
        if state is None:
            return None
        filters = dict(state["filters"])
        filters["page"] = int(state["page"]) + 1
        return await self._execute("search_vehicles", filters, conversation_id)

    async def _search_vehicle_inventory(self, action, conversation_id, messages, workflow_state):
        del action, workflow_state
        # A chip continues the persisted result that offered it. Never reinterpret unrelated
        # workflow constraints as a VehicleSearch payload.
        search_state = current_vehicle_search_state(messages)
        filters = dict(search_state.get("filters") or {}) if search_state else {}
        filters.pop("page", None)
        return await self._execute("search_vehicles", filters, conversation_id)

    async def _compare_displayed_vehicles(self, action, conversation_id, messages, workflow_state):
        candidates = current_vehicle_reference_context(messages)
        vehicle_ids = [str(item["vehicleId"]) for item in candidates[:3]]
        if len(vehicle_ids) < 2:
            return None
        return await self._execute("compare_vehicles", {"vehicleIds": vehicle_ids}, conversation_id)

    async def _select_test_drive_vehicle(self, action, conversation_id, messages, workflow_state):
        slot_arguments = _compiled_workflow_arguments(action)
        vehicle_id = slot_arguments["vehicleId"]
        draft_arguments = {
            **slot_arguments,
            "selectionEvidence": {
                "vehicleId": vehicle_id,
                "basis": "trusted_action",
                "matchedIdentity": vehicle_id,
            },
        }
        draft = await self.tools.execute(
            "prepare_test_drive", draft_arguments, conversation_id
        )
        reducer = WorkflowStateReducer()
        next_state = reducer.advance(
            workflow_state,
            "prepare_test_drive",
            draft_arguments,
            draft.view_type,
            draft.facts,
        )
        slots = await self.tools.execute(
            "list_test_drive_slots", slot_arguments, conversation_id
        )
        next_state = reducer.advance(
            next_state,
            "list_test_drive_slots",
            slot_arguments,
            slots.view_type,
            slots.facts,
        )
        return StructuredActionExecution(
            "list_test_drive_slots", slot_arguments, slots, next_state
        )

    async def _start_vehicle_interest(self, action, conversation_id, messages, workflow_state):
        return await self._execute(
            "prepare_vehicle_interest",
            {"vehicleId": action["vehicleId"]},
            conversation_id,
            transition=_transactional_handoff(workflow_state, "vehicle_interest"),
        )

    async def _start_sales_enquiry(self, action, conversation_id, messages, workflow_state):
        vehicle_id = action["vehicleId"]
        vehicle_result = await self.tools.execute("get_vehicle", {"id": vehicle_id}, conversation_id)
        vehicle = vehicle_result.facts if isinstance(vehicle_result.facts, dict) else {}
        identity = " ".join(
            str(vehicle.get(field) or "").strip() for field in ("make", "model")
        ).strip()
        arguments = {
            "vehicleId": vehicle_id,
            "enquiryType": "availability",
            "message": (
                f"Please contact me about the availability of the {identity}."
                if identity
                else "Please contact me about the availability of this vehicle."
            ),
        }
        if vehicle.get("dealershipId"):
            arguments["dealershipId"] = str(vehicle["dealershipId"])
        return await self._execute(
            "prepare_sales_enquiry",
            arguments,
            conversation_id,
            transition=_transactional_handoff(workflow_state, "sales_enquiry"),
        )

    async def _start_offer_enquiry(self, action, conversation_id, messages, workflow_state):
        offer_result = await self.tools.execute(
            "get_offer", {"id": action["offerId"]}, conversation_id
        )
        offer = offer_result.facts if isinstance(offer_result.facts, dict) else {}
        arguments = offer_enquiry_fields(offer)
        return await self._execute(
            "prepare_sales_enquiry",
            arguments,
            conversation_id,
            transition=_transactional_handoff(workflow_state, "sales_enquiry"),
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
            _compiled_workflow_arguments(action),
            conversation_id,
        )

    async def _start_dealership_workshop(
        self, action, conversation_id, messages, workflow_state
    ):
        return await self._execute(
            "list_workshop_slots",
            _compiled_workflow_arguments(action),
            conversation_id,
        )

    async def _try_workshop_location(self, action, conversation_id, messages, workflow_state):
        return await self._execute(
            "list_workshop_slots",
            _compiled_workflow_arguments(action),
            conversation_id,
        )

    async def _show_workshop_services(self, action, conversation_id, messages, workflow_state):
        active = _active_public_values(workflow_state)
        if workflow_state.get("activeWorkflow") == "workshop_booking" and active.get(
            "dealershipId"
        ):
            return await self._execute(
                "list_workshop_slots",
                {"dealershipId": active["dealershipId"]},
                conversation_id,
            )
        return await self._execute("list_service_types", {}, conversation_id)

    async def _start_callback(self, action, conversation_id, messages, workflow_state):
        handoff = _preceding_handoff(messages)
        arguments = _contact_workflow_arguments(workflow_state, handoff, "callback")
        return await self._execute(
            "prepare_callback",
            arguments,
            conversation_id,
            transition=_contact_transition(handoff),
        )

    async def _start_dealership_message(self, action, conversation_id, messages, workflow_state):
        handoff = _preceding_handoff(messages)
        arguments = _contact_workflow_arguments(
            workflow_state, handoff, "dealership_message"
        )
        return await self._execute(
            "prepare_dealership_message",
            arguments,
            conversation_id,
            transition=_contact_transition(handoff),
        )

    async def _show_dealerships(self, action, conversation_id, messages, workflow_state):
        active = _active_public_values(workflow_state)
        if active.get("dealershipId"):
            return await self._execute(
                "get_dealership", {"id": active["dealershipId"]}, conversation_id
            )
        arguments = {"town": active["dealershipTown"]} if active.get("dealershipTown") else {}
        return await self._execute("list_dealerships", arguments, conversation_id)

    async def _show_opening_hours(self, action, conversation_id, messages, workflow_state):
        active = _active_public_values(workflow_state)
        arguments = {
            key: active[key]
            for key in ("dealershipId", "dealershipTown", "department", "day")
            if active.get(key) is not None
        }
        if "dealershipTown" in arguments:
            arguments["town"] = arguments.pop("dealershipTown")
        return await self._execute("list_opening_hours", arguments, conversation_id)

    async def _show_dealership_contact_options(
        self, action, conversation_id, messages, workflow_state
    ):
        handoff = _preceding_handoff(messages)
        if handoff is not None:
            active_workflow = str(workflow_state.get("activeWorkflow") or "") or None
            matching_part_exchange = (
                active_workflow == "part_exchange" and handoff.topic == "part_exchange"
            )
            handoff = handoff.model_copy(
                update={
                    "sourceWorkflow": handoff.sourceWorkflow or active_workflow,
                    "transition": "handoff" if matching_part_exchange else "interrupt",
                }
            )
        arguments = (
            {"actionHandoff": handoff.model_dump(exclude_none=True)} if handoff else {}
        )
        return await self._execute(
            "show_dealership_contact_options", arguments, conversation_id
        )

    async def _execute(
        self,
        name: str,
        arguments: dict[str, Any],
        conversation_id: str,
        *,
        transition: Literal["auto", "handoff"] = "auto",
    ) -> StructuredActionExecution:
        result = await self.tools.execute(name, arguments, conversation_id)
        return StructuredActionExecution(name, arguments, result, transition=transition)


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


def _preceding_handoff(messages) -> ActionHandoff | None:
    pending = preceding_interaction(messages)
    return pending[0].handoff if pending is not None else None


def bind_pending_action_handoff(
    tool_name: str,
    arguments: dict[str, Any],
    pending_interaction: dict[str, Any] | None,
    workflow_state: dict[str, Any],
) -> PendingActionToolBinding | None:
    """Apply a server-owned handoff when typed language selects its visible action.

    Clicks already enter through :class:`StructuredActionHandler`. A typed equivalent is planned
    as a business tool call, so this shared admission hook restores only the handoff attached to
    the uniquely matching pending action. It never classifies prose or trusts model-authored
    context. Values grounded from the latest turn remain authoritative over older defaults.
    """

    if not pending_interaction:
        return None
    try:
        interaction = PendingInteraction.model_validate(pending_interaction)
    except (TypeError, ValueError):
        return None
    if interaction.kind not in {"single_action", "choice"} or interaction.handoff is None:
        return None
    matching_actions = [
        action
        for action in interaction.actions
        if tool_name in _ACTION_TOOL_TARGETS.get(str(action.get("type") or ""), frozenset())
    ]
    if len(matching_actions) != 1:
        return None

    action_type = str(matching_actions[0].get("type") or "")
    if action_type == "show_dealership_contact_options":
        return PendingActionToolBinding(
            {
                "actionHandoff": interaction.handoff.model_dump(exclude_none=True),
                **arguments,
            }
        )
    target = {
        "start_callback": "callback",
        "start_dealership_message": "dealership_message",
    }.get(action_type)
    if target is None:
        return None
    contextual = _contact_workflow_arguments(
        workflow_state,
        interaction.handoff,
        target,
    )
    return PendingActionToolBinding(
        {**contextual, **arguments},
        _contact_transition(interaction.handoff),
    )


def _active_public_values(workflow_state: dict[str, Any]) -> dict[str, Any]:
    """Project only validated public selectors and constraints from durable state."""

    constraints = dict(workflow_state.get("constraints") or {})
    values = {
        **dict(constraints.get("collectedPublicValues") or {}),
        **dict(workflow_state.get("entities") or {}),
    }
    for key in (
        "dealershipId",
        "dealershipTown",
        "town",
        "department",
        "day",
        "vehicleId",
        "serviceTypeId",
    ):
        if constraints.get(key) is not None:
            values.setdefault(key, constraints[key])
    if values.get("town") and not values.get("dealershipTown"):
        values["dealershipTown"] = values["town"]
    return values


def _active_scheduling_preferences(
    workflow_state: dict[str, Any], expected_workflow: str
) -> dict[str, Any]:
    if workflow_state.get("activeWorkflow") != expected_workflow:
        return {}
    constraints = dict(workflow_state.get("constraints") or {})
    scheduling = dict(constraints.get("schedulingPreferences") or {})
    return {
        key: scheduling[key]
        for key in ("dateFrom", "dateTo", "timeOfDay")
        if scheduling.get(key) is not None
    }


def _workflow_action_arguments(
    action: dict[str, Any], workflow_state: dict[str, Any]
) -> dict[str, Any] | None:
    """Compile explicit action values with compatible durable workflow context.

    This is the single action-continuation merge boundary. Individual handlers receive the
    compiled arguments and are checked after execution so they cannot silently drop preserved
    selectors or scheduling preferences.
    """

    contract = _WORKFLOW_ACTION_CONTEXTS.get(str(action.get("type") or ""))
    if contract is None:
        return None
    explicit = {
        field: action[field]
        for field in contract.explicit_fields
        if action.get(field) is not None
    }
    if contract.workflow == "workshop_booking":
        return merge_workshop_continuation_context(workflow_state, explicit)
    arguments: dict[str, Any] = {}
    if workflow_state.get("activeWorkflow") == contract.workflow:
        active = _active_public_values(workflow_state)
        arguments.update(
            {
                field: active[field]
                for field in contract.preserved_fields
                if active.get(field) is not None
            }
        )
        if contract.preserve_scheduling:
            arguments.update(
                _active_scheduling_preferences(workflow_state, contract.workflow)
            )
    arguments.update(explicit)
    return arguments


def _compiled_workflow_arguments(action: dict[str, Any]) -> dict[str, Any]:
    arguments = action.get("_workflowArguments")
    if not isinstance(arguments, dict):
        raise TypeError("workflow action has no compiled continuation context")
    return dict(arguments)


def _transactional_handoff(
    workflow_state: dict[str, Any], target_workflow: str
) -> Literal["auto", "handoff"]:
    source = str(workflow_state.get("activeWorkflow") or "")
    if source == "test_drive" and target_workflow in {
        "sales_enquiry",
        "vehicle_interest",
    }:
        return "handoff"
    return "auto"


def _contact_transition(handoff: ActionHandoff | None) -> Literal["auto", "handoff"]:
    return "handoff" if handoff is not None and handoff.transition == "handoff" else "auto"


def _contact_workflow_arguments(
    workflow_state: dict[str, Any],
    handoff: ActionHandoff | None,
    target: Literal["callback", "dealership_message"],
) -> dict[str, Any]:
    """Materialize a contact workflow from server-owned context and exact customer evidence."""

    active_workflow = str(workflow_state.get("activeWorkflow") or "")
    can_reuse_active = (
        handoff is None
        or handoff.transition == "handoff"
        or active_workflow in {"vehicle", "dealership_information"}
    )
    active = _active_public_values(workflow_state) if can_reuse_active else {}
    arguments = {
        key: active[key]
        for key in ("dealershipId", "dealershipTown", "vehicleId")
        if active.get(key) is not None
    }
    if handoff is None:
        if active.get("department"):
            arguments["department"] = active["department"]
        return arguments

    department_by_topic = {
        "finance": "sales",
        "part_exchange": "sales",
        "privacy": "general",
    }
    subject_by_topic = {
        "finance": "Finance enquiry",
        "part_exchange": "Part-exchange enquiry",
        "privacy": "Privacy enquiry",
        "general": "General enquiry",
    }
    department = department_by_topic.get(handoff.topic)
    reason = handoff.customerReason
    if target == "callback":
        if department in {"sales", "service", "parts"}:
            arguments["department"] = department
        if reason:
            arguments["reason"] = reason
        return arguments

    if department:
        arguments["department"] = department
    if handoff.topic:
        arguments["subject"] = subject_by_topic[handoff.topic]
    if reason:
        arguments["message"] = reason
    return arguments
