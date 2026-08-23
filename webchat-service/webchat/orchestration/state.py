from __future__ import annotations

from typing import Any, ClassVar


def workflow_state(
    active_workflow: str,
    stage: str,
    *,
    entities: dict[str, Any] | None = None,
    constraints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "version": 3,
        "activeWorkflow": active_workflow,
        "stage": stage,
        "entities": dict(entities or {}),
        "constraints": dict(constraints or {}),
    }


def normalize_workflow_state(state: dict[str, Any] | None) -> dict[str, Any]:
    """Read V3 state and upgrade persisted V2 state without preserving its ontology."""
    if not state:
        return {}
    if state.get("version") == 3:
        return {
            "version": 3,
            "activeWorkflow": str(state.get("activeWorkflow") or "conversation"),
            "stage": _normalized_stage(state.get("stage")),
            "entities": dict(state.get("entities") or {}),
            "constraints": dict(state.get("constraints") or {}),
            **({"lastTool": state["lastTool"]} if state.get("lastTool") else {}),
            **({"lastRenderer": state["lastRenderer"]} if state.get("lastRenderer") else {}),
        }
    legacy_domain = str(state.get("domain") or "conversation")
    legacy_goal = str(state.get("goal") or "")
    workflow = {
        "vehicle": "vehicle_search",
        "workshop": "workshop",
        "test_drive": "test_drive",
        "part_exchange": "part_exchange",
        "sales": "sales_contact",
        "dealership": "dealership_information",
        "offer": "offers",
    }.get(legacy_domain, legacy_goal or "conversation")
    return workflow_state(
        workflow,
        _normalized_stage(state.get("stage")),
        entities=dict(state.get("entities") or {}),
        constraints=dict(state.get("constraints") or {}),
    )


class WorkflowStateReducer:
    """Derive follow-up context from actual executions, never predicted domains or goals."""

    _WORKFLOWS: ClassVar[dict[str, str]] = {
        "search_vehicles": "vehicle_search",
        "reset_vehicle_search": "vehicle_search",
        "refine_vehicle_search": "vehicle_search",
        "select_page_vehicles": "vehicle_search",
        "show_vehicle_preferences": "vehicle_search",
        "get_vehicle": "vehicle",
        "get_vehicle_availability": "vehicle",
        "compare_vehicles": "vehicle_comparison",
        "compare_vehicle_models": "vehicle_comparison",
        "list_offers": "offers",
        "get_offer": "offers",
        "list_dealerships": "dealership_information",
        "get_dealership": "dealership_information",
        "list_opening_hours": "dealership_information",
        "list_holiday_opening_hours": "dealership_information",
        "get_opening_hours": "dealership_information",
        "list_dealership_departments": "dealership_information",
        "find_dealership_departments": "dealership_information",
        "get_service_information": "workshop",
        "list_service_types": "workshop",
        "list_workshop_locations": "workshop",
        "list_workshop_slots": "workshop_booking",
        "refine_workshop_slots": "workshop_booking",
        "list_test_drive_slots": "test_drive",
        "request_workshop_booking_lookup_form": "existing_workshop_booking",
        "request_part_exchange_estimate_form": "part_exchange",
        "estimate_part_exchange": "part_exchange",
    }

    def advance(
        self,
        current: dict[str, Any],
        tool_name: str,
        arguments: dict[str, Any],
        view_type: str | None,
        facts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = normalize_workflow_state(current)
        workflow = self._WORKFLOWS.get(tool_name)
        if tool_name.startswith("prepare_"):
            workflow = tool_name.removeprefix("prepare_")
        if workflow is None:
            return state
        previous_constraints = dict(state.get("constraints") or {})
        entities: dict[str, Any] = {}
        constraints: dict[str, Any] = {}
        if tool_name in {"search_vehicles", "reset_vehicle_search"}:
            entities = {}
            constraints = {key: value for key, value in arguments.items() if key != "page"}
        elif tool_name in {"refine_vehicle_search", "select_page_vehicles"}:
            constraints = {
                key: value for key, value in arguments.items() if key not in {"page", "vehicleIds"}
            }
        elif tool_name in {
            "list_workshop_slots",
            "refine_workshop_slots",
            "list_test_drive_slots",
        }:
            constraints = dict(arguments)
        elif tool_name == "show_vehicle_preferences":
            dimension = arguments.get("dimension", "startingPoint")
            if arguments.get("reuseCurrentSearch"):
                constraints = previous_constraints
                constraints.pop("preferenceDimension", None)
            constraints["preferenceDimension"] = dimension
        else:
            for key in (
                "dealershipId",
                "dealershipTown",
                "town",
                "serviceTypeId",
                "serviceTypeName",
            ):
                if arguments.get(key) is not None:
                    constraints[key] = arguments[key]
        for key in ("id", "vehicleId", "offerId", "serviceTypeId", "dealershipId"):
            if arguments.get(key) is not None:
                entity_key = {"id": _entity_key(tool_name)}.get(key, key)
                if entity_key:
                    entities[entity_key] = arguments[key]
        if facts and isinstance(facts.get("service"), dict):
            service = facts["service"]
            if service.get("id"):
                entities["serviceTypeId"] = service["id"]
            if service.get("name"):
                entities["serviceTypeName"] = service["name"]
        next_state = workflow_state(
            workflow,
            _stage(tool_name, view_type),
            entities=entities,
            constraints=constraints,
        )
        next_state["lastTool"] = tool_name
        if view_type:
            next_state["lastRenderer"] = view_type
        return next_state

def _entity_key(tool_name: str) -> str | None:
    if tool_name in {"get_vehicle", "get_vehicle_availability"}:
        return "vehicleId"
    if tool_name in {"get_offer"}:
        return "offerId"
    if tool_name in {"get_dealership", "get_opening_hours"}:
        return "dealershipId"
    return None


def _stage(tool_name: str, view_type: str | None) -> str:
    if tool_name.startswith("prepare_"):
        return "awaiting_confirmation" if view_type == "confirmation" else "collecting"
    if tool_name in {
        "list_workshop_slots",
        "refine_workshop_slots",
        "list_test_drive_slots",
    }:
        return "choosing_time"
    if tool_name == "request_workshop_booking_lookup_form":
        return "awaiting_verification"
    return "viewing"


def _normalized_stage(value: Any) -> str:
    stage = str(value or "active")
    if stage == "answered" or stage.startswith("viewing_"):
        return "viewing"
    if stage == "planned":
        return "active"
    return stage
