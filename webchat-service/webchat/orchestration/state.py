from __future__ import annotations

from typing import Any, ClassVar, Literal

from webchat.domain.capabilities import CapabilityRegistry
from webchat.domain.conversation_state import validate_agent_workflow
from webchat.orchestration.contracts.semantics import TurnUnderstanding
from webchat.orchestration.workflow_kernel import (
    test_drive_transition,
    workshop_transition,
)


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
    normalized = validate_agent_workflow(state)
    constraints = dict(normalized.get("constraints") or {})
    resolution = constraints.get("resolution")
    transition = None
    if isinstance(resolution, dict):
        if resolution.get("kind") == "test_drive_slots":
            transition = test_drive_transition(resolution)
        elif resolution.get("kind") == "workshop_slots":
            transition = workshop_transition(resolution)
    if transition is not None:
        normalized["stage"] = transition["stage"]
        constraints["continuation"] = transition["continuation"]
        constraints["missingPublicFields"] = transition["missingPublicFields"]
        constraints["acceptedInputFields"] = transition["acceptedInputFields"]
        normalized["constraints"] = constraints
    if normalized.get("activeWorkflow") != "workshop_booking":
        return validate_agent_workflow(normalized)
    entities = dict(normalized.get("entities") or {})
    collected = dict(constraints.get("collectedPublicValues") or {})
    selected_dealership = collected.get("selectedDealershipId")
    if not entities.get("slotId") or not selected_dealership:
        return normalized
    entities["dealershipId"] = selected_dealership
    missing = [
        field
        for field in list(constraints.get("missingPublicFields") or [])
        if field != "dealershipId"
    ]
    constraints["missingPublicFields"] = missing
    constraints["secureInputReady"] = not missing
    normalized["entities"] = entities
    normalized["constraints"] = constraints
    return validate_agent_workflow(normalized)


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
        "resolve_vehicle_availability": "vehicle",
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

    @classmethod
    def _workflow_for_tool(cls, tool_name: str, arguments: dict[str, Any]) -> str | None:
        workflow = cls._WORKFLOWS.get(tool_name)
        if tool_name.startswith("prepare_"):
            prepared_kind = tool_name.removeprefix("prepare_")
            workflow = {
                "workshop_amendment": "workshop_amend",
                "workshop_cancellation": "workshop_cancel",
            }.get(prepared_kind, prepared_kind)
        if (
            tool_name in {"list_workshop_slots", "refine_workshop_slots"}
            and arguments.get("workflowMode") == "amendment"
        ):
            workflow = "workshop_amend"
        return workflow

    @classmethod
    def execution_transition(
        cls,
        current: dict[str, Any],
        candidate_subject_field: str | None,
        facts: dict[str, Any] | None,
        understanding: TurnUnderstanding | None,
    ) -> Literal["auto", "branch"]:
        """Classify a candidate-set read as support or a new conversational branch.

        A read that merely describes the active subject is an interruption and preserves the
        transaction. A read that opens alternatives for an already-resolved transaction subject
        pauses that transaction unless the validated turn is also continuing its owning goal.
        This keeps a hidden old question from consuming replies to the new candidate set.
        """

        state = normalize_workflow_state(current)
        active = str(state.get("activeWorkflow") or "")
        spec = CapabilityRegistry.for_active_workflow(active)
        if (
            spec is None
            or candidate_subject_field not in spec.public_fields
            or understanding is None
        ):
            return "auto"
        active_intent = CapabilityRegistry.goal_intent_for_active_workflow(active)
        if active_intent in understanding.intentKinds:
            return "auto"
        selected = {
            **dict(state.get("entities") or {}),
            **dict(dict(state.get("constraints") or {}).get("collectedPublicValues") or {}),
        }.get(candidate_subject_field)
        if selected is None:
            return "auto"
        items = facts.get("items") if isinstance(facts, dict) else None
        if isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict):
            identifier = items[0].get("id")
            if identifier is not None and str(identifier) == str(selected):
                return "auto"
        return "branch"

    @classmethod
    def turn_effect(
        cls,
        current: dict[str, Any],
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        transition: str = "auto",
    ) -> Literal[
        "none",
        "interrupt",
        "continue",
        "start",
        "switch",
        "handoff",
        "cancel",
        "resume",
    ]:
        """Classify the application effect of one validated operation.

        This is the shared owner for workflow and dialogue continuity. A model-authored
        ``dialogueAct`` cannot turn a read-only operation into a successful goal switch.
        """

        if transition not in {"auto", "handoff", "branch"}:
            raise ValueError("unsupported workflow transition")
        state = normalize_workflow_state(current)
        current_workflow = str(state.get("activeWorkflow") or "")
        if tool_name == "cancel_active_capability":
            return "cancel"
        if tool_name == "resume_paused_capability":
            return "resume"
        workflow = cls._workflow_for_tool(tool_name, arguments or {})
        if not current_workflow:
            return "start" if workflow else "none"
        if not CapabilityRegistry.is_transactional_workflow(current_workflow):
            return "start" if workflow and workflow != current_workflow else "continue"
        if workflow == current_workflow:
            return "continue"
        if workflow and CapabilityRegistry.is_transactional_workflow(workflow):
            return "handoff" if transition == "handoff" else "switch"
        if workflow and transition == "branch":
            return "switch"
        return "interrupt"

    def advance(
        self,
        current: dict[str, Any],
        tool_name: str,
        arguments: dict[str, Any],
        view_type: str | None,
        facts: dict[str, Any] | None = None,
        *,
        transition: str = "auto",
    ) -> dict[str, Any]:
        if transition not in {"auto", "handoff", "branch"}:
            raise ValueError("unsupported workflow transition")
        state = normalize_workflow_state(current)
        if tool_name == "cancel_active_capability":
            paused = state.get("pausedWorkflow")
            return normalize_workflow_state(paused) if isinstance(paused, dict) else {}
        if tool_name == "resume_paused_capability":
            paused = state.get("pausedWorkflow")
            if not isinstance(paused, dict):
                raise ValueError("there is no paused capability")
            return normalize_workflow_state(paused)
        workflow = self._workflow_for_tool(tool_name, arguments)
        if workflow is None:
            return state
        current_workflow = str(state.get("activeWorkflow") or "")
        current_is_transactional = CapabilityRegistry.is_transactional_workflow(current_workflow)
        target_is_transactional = CapabilityRegistry.is_transactional_workflow(workflow)
        if current_is_transactional and workflow != current_workflow:
            if not target_is_transactional and transition != "branch":
                interrupted = dict(state)
                interrupted["lastInterruptionTool"] = tool_name
                return interrupted
            if transition == "handoff":
                paused = state.get("pausedWorkflow")
                paused_workflow = dict(paused) if isinstance(paused, dict) else None
            else:
                paused_workflow = dict(state)
        elif current_is_transactional and workflow == current_workflow:
            paused = state.get("pausedWorkflow")
            paused_workflow = dict(paused) if isinstance(paused, dict) else None
        else:
            paused = state.get("pausedWorkflow")
            paused_workflow = dict(paused) if isinstance(paused, dict) else None
            if (
                target_is_transactional
                and isinstance(paused_workflow, dict)
                and paused_workflow.get("activeWorkflow") == workflow
            ):
                # Starting the same transaction from its alternative candidate branch replaces
                # that paused attempt; an explicit resume remains available before replacement.
                paused_workflow = None
        previous_constraints = dict(state.get("constraints") or {})
        # A read or preparation step inside the same transaction refines that workflow; it must
        # not silently erase subject relationships established by an earlier trusted result.
        # Explicit arguments and fresh facts below still replace the corresponding entity.
        entities: dict[str, Any] = (
            dict(state.get("entities") or {})
            if current_is_transactional and workflow == current_workflow
            else {}
        )
        if not arguments.get("vehicleId") and any(
            arguments.get(field)
            for field in ("make", "model", "q")
        ):
            entities.pop("vehicleId", None)
        if not arguments.get("serviceTypeId") and arguments.get("serviceTypeName"):
            entities.pop("serviceTypeId", None)
        if not arguments.get("dealershipId") and any(
            arguments.get(field) for field in ("dealershipTown", "town")
        ):
            entities.pop("dealershipId", None)
        if tool_name in {
            "list_test_drive_slots",
            "list_workshop_slots",
            "refine_workshop_slots",
        } and not arguments.get("slotId"):
            entities.pop("slotId", None)
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
            constraints = {
                key: value
                for key, value in arguments.items()
                if key != "schedulePreferenceMode"
            }
            # Availability reads refine a transactional draft; they must never erase the
            # already-collected public values or the proof of the customer's vehicle choice.
            for key in (
                "draftId",
                "secureFields",
                "secureInputReady",
                "collectedPublicValues",
                "selectionEvidence",
            ):
                if previous_constraints.get(key) is not None:
                    constraints[key] = previous_constraints[key]
            resolution = facts.get("resolution") if isinstance(facts, dict) else None
            test_drive_result = (
                test_drive_transition(resolution)
                if tool_name == "list_test_drive_slots"
                and isinstance(resolution, dict)
                and resolution.get("kind") == "test_drive_slots"
                else None
            )
            workshop_result = (
                workshop_transition(resolution)
                if tool_name in {"list_workshop_slots", "refine_workshop_slots"}
                and isinstance(resolution, dict)
                and resolution.get("kind") == "workshop_slots"
                else None
            )
            transition_result = test_drive_result or workshop_result
            if transition_result is not None:
                schedule_mode = str(
                    arguments.get("schedulePreferenceMode") or "preserve"
                )
                scheduling = (
                    {}
                    if schedule_mode in {"replace", "clear"}
                    or resolution.get("scope") == "broad"
                    else dict(previous_constraints.get("schedulingPreferences") or {})
                )
                scheduling.update(
                    {
                        key: arguments[key]
                        for key in ("dateFrom", "dateTo", "timeOfDay")
                        if arguments.get(key) is not None
                    }
                )
                if scheduling:
                    constraints["schedulingPreferences"] = scheduling
                constraints["resolution"] = dict(resolution)
                constraints["continuation"] = transition_result["continuation"]
                constraints["missingPublicFields"] = transition_result[
                    "missingPublicFields"
                ]
                constraints["acceptedInputFields"] = transition_result[
                    "acceptedInputFields"
                ]
            if arguments.get("workflowMode") == "amendment":
                constraints["verifiedBooking"] = True
            if transition_result is not None:
                pass
            elif view_type in {"slot_list", "test_drive_slot_picker"}:
                # Live slots are trusted reference context, not a browser picker. The state keeps
                # both preference dimensions available while the composer asks for one at a time;
                # a date-refined read asks the customer to identify one grounded slot.
                constraints["missingPublicFields"] = (
                    ["slotId"]
                    if arguments.get("dateFrom") or arguments.get("dateTo")
                    else ["preferredDayOrDate", "approximateTime"]
                )
            elif view_type == "workshop_location_list":
                if (
                    isinstance(facts, dict)
                    and isinstance(facts.get("resolution"), dict)
                    and facts["resolution"].get("status") == "unavailable"
                ):
                    constraints.pop("dealershipTown", None)
                constraints["missingPublicFields"] = ["dealershipId"]
            if (
                isinstance(facts, dict)
                and facts.get("selectionOnly") is True
                and facts.get("choiceField") in {
                    "vehicleId",
                    "offerId",
                    "dealershipId",
                    "serviceTypeId",
                    "slotId",
                }
            ):
                # A finite choice returned while resolving availability owns the next public
                # workflow field, regardless of the outer tool that discovered the missing input.
                constraints["missingPublicFields"] = [str(facts["choiceField"])]
                constraints["acceptedInputFields"] = []
        elif tool_name in {
            "request_workshop_booking_lookup_form",
            "request_part_exchange_estimate_form",
        }:
            capability = CapabilityRegistry.get(
                {
                    "request_workshop_booking_lookup_form": "booking_lookup",
                    "request_part_exchange_estimate_form": "part_exchange_estimate",
                }[tool_name]
            )
            constraints = {
                "missingPublicFields": [],
                "secureFields": list(capability.secure_fields),
                "secureInputReady": True,
                "collectedPublicValues": {},
            }
            if tool_name == "request_workshop_booking_lookup_form":
                constraints["mode"] = str(arguments.get("mode") or "lookup")
        elif tool_name.startswith("prepare_") and isinstance(facts, dict):
            summary = dict(facts.get("summary") or {})
            entities = {
                key: summary[key]
                for key in ("vehicleId", "serviceTypeId", "dealershipId", "slotId")
                if summary.get(key) is not None
            }
            constraints = {
                "draftId": facts.get("draftId"),
                "missingPublicFields": list(facts.get("missingPublicFields") or []),
                "secureFields": list(facts.get("secureFields") or []),
                "secureInputReady": facts.get("secureInputReady") is True,
                "collectedPublicValues": {
                    key: value
                    for key, value in summary.items()
                    if key not in {"contactProvided", "kind"}
                },
            }
            if tool_name == "prepare_test_drive" and isinstance(
                facts.get("schedulingPreferences"), dict
            ):
                constraints["schedulingPreferences"] = dict(
                    facts["schedulingPreferences"]
                )
            if tool_name == "prepare_test_drive" and isinstance(
                facts.get("selectionEvidence"), dict
            ):
                constraints["selectionEvidence"] = dict(facts["selectionEvidence"])
        elif tool_name == "show_vehicle_preferences":
            dimension = arguments.get("dimension", "startingPoint")
            if arguments.get("reuseCurrentSearch"):
                constraints = previous_constraints
                constraints.pop("preferenceDimension", None)
            constraints["preferenceDimension"] = dimension
        elif tool_name in {"compare_vehicle_models", "compare_vehicles"} and isinstance(
            facts, dict
        ):
            resolution = facts.get("resolution")
            comparison = facts.get("comparison")
            if (
                tool_name == "compare_vehicle_models"
                and isinstance(resolution, dict)
                and resolution.get("status") == "ambiguous"
            ):
                constraints = {
                    "comparisonQueries": list(resolution.get("queries") or []),
                    "pendingComparisonQuery": resolution.get("pendingQuery"),
                    "pendingComparisonIndex": resolution.get("pendingIndex"),
                    "resolvedVehicleIds": list(
                        resolution.get("resolvedVehicleIds") or []
                    ),
                    "comparisonSelectionBasis": resolution.get("selectionBasis"),
                }
            elif isinstance(comparison, dict):
                entities["vehicleIds"] = list(comparison.get("vehicleIds") or [])
                if comparison.get("selectionBasis"):
                    constraints["comparisonSelectionBasis"] = comparison["selectionBasis"]
        else:
            for key in (
                "dealershipId",
                "dealershipTown",
                "town",
                "department",
                "day",
                "serviceTypeId",
                "serviceTypeName",
            ):
                if arguments.get(key) is not None:
                    constraints[key] = arguments[key]
        if tool_name in {
            "list_dealerships",
            "list_dealership_departments",
            "find_dealership_departments",
            "get_dealership",
            "get_opening_hours",
            "list_opening_hours",
            "list_holiday_opening_hours",
        } and isinstance(facts, dict):
            result_items = facts.get("items")
            if isinstance(result_items, list) and len(result_items) == 1:
                selected_dealership = result_items[0]
                if isinstance(selected_dealership, dict) and selected_dealership.get("id"):
                    entities.setdefault("dealershipId", str(selected_dealership["id"]))
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
        if facts and isinstance(facts.get("vehicle"), dict):
            vehicle = facts["vehicle"]
            if vehicle.get("id"):
                entities.setdefault("vehicleId", vehicle["id"])
            if vehicle.get("dealershipId"):
                entities.setdefault("dealershipId", vehicle["dealershipId"])
        stage = _stage(tool_name, view_type)
        if tool_name in {
            "list_test_drive_slots",
            "list_workshop_slots",
            "refine_workshop_slots",
        }:
            resolution = constraints.get("resolution")
            if isinstance(resolution, dict):
                if resolution.get("kind") == "test_drive_slots":
                    stage = test_drive_transition(resolution)["stage"]
                elif resolution.get("kind") == "workshop_slots":
                    stage = workshop_transition(resolution)["stage"]
        next_state = workflow_state(
            workflow,
            stage,
            entities=entities,
            constraints=constraints,
        )
        next_state["lastTool"] = tool_name
        if view_type:
            next_state["lastRenderer"] = view_type
        if paused_workflow is not None:
            next_state["pausedWorkflow"] = paused_workflow
        return next_state

def _entity_key(tool_name: str) -> str | None:
    if tool_name in {
        "get_vehicle",
        "get_vehicle_availability",
        "resolve_vehicle_availability",
    }:
        return "vehicleId"
    if tool_name in {"get_offer"}:
        return "offerId"
    if tool_name in {"get_dealership", "get_opening_hours"}:
        return "dealershipId"
    return None


def _stage(tool_name: str, view_type: str | None) -> str:
    if tool_name == "estimate_part_exchange":
        return "estimate_ready"
    if tool_name.startswith("prepare_"):
        return "awaiting_confirmation" if view_type == "confirmation" else "collecting"
    if tool_name in {
        "list_workshop_slots",
        "refine_workshop_slots",
        "list_test_drive_slots",
    }:
        if tool_name != "list_test_drive_slots" and view_type == "service_list":
            return "choosing_service"
        if tool_name != "list_test_drive_slots" and view_type == "workshop_location_list":
            return "choosing_dealership"
        return "choosing_time"
    if tool_name == "request_workshop_booking_lookup_form":
        return "awaiting_verification"
    if tool_name == "request_part_exchange_estimate_form":
        return "awaiting_protected_input"
    if tool_name == "compare_vehicle_models" and view_type == "vehicle_list":
        return "choosing_vehicle"
    return "viewing"
