from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from webchat.integrations.contracts import ToolCall
from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.form_prefill import contextual_form_prefill


@dataclass(frozen=True)
class PolicyDecision:
    calls: list[ToolCall]
    clarification: str = ""


@dataclass(frozen=True)
class GroundingContext:
    page_vehicle_ids: frozenset[str]
    trusted_vehicle_ids: frozenset[str]
    displayed_offer_ids: frozenset[str]
    displayed_dealership_ids: frozenset[str]
    displayed_dealerships: tuple[dict[str, object], ...]
    vehicle_search_state: dict[str, object] | None
    workflow_state: dict[str, Any]
    latest_customer_message: str = ""
    recent_customer_messages: tuple[str, ...] = ()

    @classmethod
    def build(
        cls,
        state: dict[str, Any],
        references,
        latest_customer_message: str = "",
        recent_customer_messages: tuple[str, ...] = (),
    ) -> GroundingContext:
        displayed_vehicle_ids = _ids(references.displayed_vehicles, "vehicleId")
        page_vehicle_ids = _ids(references.page_vehicles, "vehicleId")
        trusted_vehicle_ids = displayed_vehicle_ids | page_vehicle_ids
        entities = dict(state.get("entities") or {})
        if entities.get("vehicleId"):
            trusted_vehicle_ids.add(str(entities["vehicleId"]))
        return cls(
            frozenset(page_vehicle_ids),
            frozenset(trusted_vehicle_ids),
            frozenset(_ids(references.displayed_offers, "offerId")),
            frozenset(_ids(references.displayed_dealerships, "dealershipId")),
            tuple(references.displayed_dealerships),
            references.vehicle_search_state,
            state,
            latest_customer_message,
            recent_customer_messages,
        )


class ToolPolicyGate:
    """Enforce catalogue-declared runtime safety on concrete tool calls."""

    def __init__(self, catalogue: UnifiedToolCatalog):
        self.catalogue = catalogue
        self._preconditions = {
            "trusted_page_vehicle_ids": self._trusted_page_vehicle_ids,
            "trusted_vehicle_ids": self._trusted_vehicle_ids,
            "trusted_vehicle_id": self._trusted_vehicle_id,
            "trusted_offer_id": self._trusted_offer_id,
            "trusted_dealership_id": self._trusted_dealership_id,
            "trusted_optional_dealership_id": self._trusted_optional_dealership_id,
            "single_dealership_or_location": self._single_dealership_or_location,
            "single_dealership_or_all": self._single_dealership_or_all,
            "sole_displayed_dealership": self._sole_displayed_dealership,
            "verified_booking": self._verified_booking,
            "current_vehicle_search": self._current_vehicle_search,
            "current_workshop_search": self._current_workshop_search,
            "contextual_dealership_prefill": self._contextual_dealership_prefill,
        }
        declared = {
            condition
            for definition in catalogue.definitions()
            for condition in definition.preconditions
        }
        unknown = declared - self._preconditions.keys()
        if unknown:
            raise ValueError(f"unknown catalogue preconditions: {sorted(unknown)}")

    def validate(
        self,
        calls: list[ToolCall],
        *,
        state: dict[str, Any],
        references,
        latest_customer_message: str = "",
        recent_customer_messages: tuple[str, ...] = (),
    ) -> PolicyDecision:
        if not calls or len(calls) > 4:
            raise ValueError("a proposal must contain between one and four tool calls")
        definitions = [self.catalogue.get(call.name) for call in calls]
        if sum(definition.result_mode != "evidence" for definition in definitions) > 1:
            raise ValueError("a proposal can contain only one terminal tool")
        context = GroundingContext.build(
            state,
            references,
            latest_customer_message,
            recent_customer_messages,
        )
        validated: list[ToolCall] = []
        for call, definition in zip(calls, definitions, strict=True):
            if definition.risk == "confirmed_write":
                raise ValueError("confirmed writes are never model callable")
            arguments = dict(call.arguments)
            arguments = contextual_form_prefill(
                call.name,
                arguments,
                context.latest_customer_message,
                context.recent_customer_messages,
            )
            if call.name == "refine_vehicle_search":
                arguments = self._vehicle_search_refinement(arguments, context)
            elif call.name == "refine_workshop_slots":
                arguments = self._workshop_search_refinement(arguments, context)
            for condition in definition.preconditions:
                if clarification := self._preconditions[condition](arguments, context):
                    return PolicyDecision([], clarification)
            definition.validate_arguments(arguments)
            validated.append(ToolCall(call.id, call.name, arguments))
        return PolicyDecision(validated)

    @staticmethod
    def _vehicle_search_refinement(
        arguments: dict[str, Any], context: GroundingContext
    ) -> dict[str, Any]:
        current = context.vehicle_search_state
        if not current or not isinstance(current.get("filters"), dict):
            raise ValueError("vehicle search refinement requires current server-owned search state")
        merged = dict(current["filters"])
        opposing_filters = (
            ("make", "excludedMakes"),
            ("model", "excludedModels"),
            ("fuelType", "excludedFuelTypes"),
            ("transmission", "excludedTransmissions"),
            ("bodyStyle", "excludedBodyStyles"),
        )
        for positive, negative in opposing_filters:
            if positive in arguments:
                merged.pop(negative, None)
            if negative in arguments:
                merged.pop(positive, None)
        return {**merged, **arguments}

    @staticmethod
    def _current_vehicle_search(arguments: dict[str, Any], context: GroundingContext) -> str:
        del arguments
        if not context.vehicle_search_state:
            raise ValueError("vehicle search refinement requires current server-owned search state")
        return ""

    @staticmethod
    def _workshop_search_refinement(
        arguments: dict[str, Any], context: GroundingContext
    ) -> dict[str, Any]:
        state = context.workflow_state
        current = {
            **dict(state.get("constraints") or {}),
            **{
                key: value
                for key, value in dict(state.get("entities") or {}).items()
                if key in {"serviceTypeId", "serviceTypeName"}
            },
        }
        if not current.get("serviceTypeId") and not current.get("serviceTypeName"):
            raise ValueError("workshop refinement requires a current resolved service")
        if arguments.get("serviceTypeId") or arguments.get("serviceTypeName"):
            current.pop("serviceTypeId", None)
            current.pop("serviceTypeName", None)
        return {**current, **arguments}

    @staticmethod
    def _current_workshop_search(arguments: dict[str, Any], context: GroundingContext) -> str:
        del arguments
        if context.workflow_state.get("activeWorkflow") != "workshop_booking":
            raise ValueError("workshop refinement requires current workshop search state")
        return ""

    @staticmethod
    def _contextual_dealership_prefill(
        arguments: dict[str, Any], context: GroundingContext
    ) -> str:
        """Carry one trusted location into a form without inventing a selection."""
        if arguments.get("dealershipId") or arguments.get("dealershipTown"):
            return ""
        state = context.workflow_state
        entities = dict(state.get("entities") or {})
        constraints = dict(state.get("constraints") or {})
        dealership_id = entities.get("dealershipId") or constraints.get("dealershipId")
        town = constraints.get("dealershipTown") or constraints.get("town")
        if dealership_id:
            arguments["dealershipId"] = dealership_id
        elif town:
            arguments["dealershipTown"] = town
        return ""

    @staticmethod
    def _trusted_page_vehicle_ids(arguments: dict[str, Any], context: GroundingContext) -> str:
        if not set(arguments.get("vehicleIds") or []).issubset(context.page_vehicle_ids):
            raise ValueError("page vehicle selection contains an untrusted ID")
        return ""

    @staticmethod
    def _trusted_vehicle_ids(arguments: dict[str, Any], context: GroundingContext) -> str:
        if not set(arguments.get("vehicleIds") or []).issubset(context.trusted_vehicle_ids):
            raise ValueError("vehicle comparison contains an untrusted ID")
        return ""

    @staticmethod
    def _trusted_vehicle_id(arguments: dict[str, Any], context: GroundingContext) -> str:
        value = str(arguments.get("vehicleId") or arguments.get("id") or "")
        if value not in context.trusted_vehicle_ids:
            raise ValueError("vehicle operation contains an untrusted ID")
        return ""

    @staticmethod
    def _trusted_offer_id(arguments: dict[str, Any], context: GroundingContext) -> str:
        if str(arguments.get("id") or "") not in context.displayed_offer_ids:
            raise ValueError("offer operation contains an untrusted ID")
        return ""

    @staticmethod
    def _trusted_dealership_id(arguments: dict[str, Any], context: GroundingContext) -> str:
        if str(arguments.get("id") or "") not in context.displayed_dealership_ids:
            raise ValueError("dealership operation contains an untrusted ID")
        return ""

    @staticmethod
    def _trusted_optional_dealership_id(
        arguments: dict[str, Any], context: GroundingContext
    ) -> str:
        dealership_id = arguments.get("dealershipId")
        if dealership_id is None:
            return ""
        if str(dealership_id) not in context.displayed_dealership_ids:
            raise ValueError("dealership operation contains an untrusted ID")
        return ""

    @staticmethod
    def _single_dealership_or_location(arguments: dict[str, Any], context: GroundingContext) -> str:
        if arguments.get("town"):
            return ""
        if arguments.get("dealershipId") and len(context.displayed_dealerships) == 1:
            return ""
        if len(context.displayed_dealerships) == 1:
            selected = context.displayed_dealerships[0]
            if selected.get("dealershipId"):
                arguments["dealershipId"] = selected["dealershipId"]
            elif selected.get("town"):
                arguments["town"] = selected["town"]
            return ""
        if not context.displayed_dealerships:
            return "Which dealership do you mean?"
        return _dealership_clarification(context.displayed_dealerships)

    @staticmethod
    def _single_dealership_or_all(arguments: dict[str, Any], context: GroundingContext) -> str:
        """Reuse one displayed location; keep a multi-location list operation plural."""
        if arguments.get("town"):
            return ""
        if arguments.get("dealershipId"):
            if len(context.displayed_dealerships) == 1:
                return ""
            if len(context.displayed_dealerships) > 1:
                return _dealership_clarification(context.displayed_dealerships)
            raise ValueError("dealership operation requires current displayed dealership context")
        if len(context.displayed_dealerships) != 1:
            return ""
        selected = context.displayed_dealerships[0]
        if selected.get("dealershipId"):
            arguments["dealershipId"] = selected["dealershipId"]
        elif selected.get("town"):
            arguments["town"] = selected["town"]
        return ""

    @staticmethod
    def _sole_displayed_dealership(arguments: dict[str, Any], context: GroundingContext) -> str:
        """Prevent a singular ID operation from collapsing a displayed location set."""
        del arguments
        if len(context.displayed_dealerships) == 1:
            return ""
        if len(context.displayed_dealerships) > 1:
            return _dealership_clarification(context.displayed_dealerships)
        raise ValueError("dealership operation requires current displayed dealership context")

    @staticmethod
    def _verified_booking(arguments: dict[str, Any], context: GroundingContext) -> str:
        del arguments
        state = context.workflow_state
        if (
            state.get("activeWorkflow") != "existing_workshop_booking"
            or state.get("stage") != "verified"
        ):
            raise ValueError("workshop booking must be verified before it can be changed")
        return ""


def _ids(items: list[dict[str, object]], field: str) -> set[str]:
    return {str(item[field]) for item in items if item.get(field)}


def _dealership_clarification(dealerships: tuple[dict[str, object], ...]) -> str:
    towns = ", ".join(dict.fromkeys(str(item["town"]) for item in dealerships if item.get("town")))
    return f"Which dealership do you mean: {towns}?" if towns else "Which dealership do you mean?"
