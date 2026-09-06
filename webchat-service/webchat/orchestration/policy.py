from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from webchat.domain.capabilities import CapabilityRegistry, CapabilitySpec, LiveFieldResolver
from webchat.integrations.contracts import ToolCall
from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.contracts.semantics import (
    REFERENCE_FIELD_NAMESPACES,
    TurnUnderstanding,
)
from webchat.orchestration.intent_contracts import supported_intents
from webchat.orchestration.workflow_kernel import (
    merge_workshop_continuation_context,
)

_MULTI_VALUE_INPUT_ALIASES = {
    "make": "makes",
    "model": "models",
    "colour": "colours",
    "fuelType": "fuelTypes",
    "transmission": "transmissions",
    "bodyStyle": "bodyStyles",
}

_COMPATIBLE_INPUT_ALIASES = {
    "town": ("dealershipTown",),
    "dealershipTown": ("town",),
    "q": ("serviceTypeName",),
    "serviceTypeName": ("q",),
}

_APPOINTMENT_OWNED_SELECTORS = frozenset(
    {"vehicleId", "serviceTypeId", "dealershipId"}
)


@dataclass(frozen=True)
class PolicyDecision:
    calls: list[ToolCall]


class PolicyClarification(ValueError):
    """A safe, deterministic clarification that must replace an invalid tool call."""

    def __init__(self, result):
        super().__init__(result.text)
        self.result = result


@dataclass(frozen=True)
class GroundingContext:
    page_vehicle_ids: frozenset[str]
    trusted_vehicle_ids: frozenset[str]
    displayed_offer_ids: frozenset[str]
    displayed_dealership_ids: frozenset[str]
    displayed_dealerships: tuple[dict[str, object], ...]
    displayed_choices: tuple[dict[str, object], ...]
    displayed_choice_references: frozenset[str]
    vehicle_search_state: dict[str, object] | None
    workflow_state: dict[str, Any]
    candidate_vehicles: tuple[dict[str, object], ...] = ()
    turn_understanding: TurnUnderstanding | None = None

    @classmethod
    def build(
        cls,
        state: dict[str, Any],
        references,
        turn_understanding: TurnUnderstanding | None = None,
    ) -> GroundingContext:
        displayed_vehicle_ids = _ids(references.displayed_vehicles, "vehicleId")
        page_vehicle_ids = _ids(references.page_vehicles, "vehicleId")
        trusted_vehicle_ids = displayed_vehicle_ids | page_vehicle_ids
        trusted_vehicle_ids.update(
            str(item["vehicleId"])
            for item in references.displayed_choices
            if isinstance(item, dict) and item.get("vehicleId")
        )
        entities = dict(state.get("entities") or {})
        if entities.get("vehicleId"):
            trusted_vehicle_ids.add(str(entities["vehicleId"]))
        for vehicle_id in entities.get("vehicleIds") or []:
            if isinstance(vehicle_id, str):
                trusted_vehicle_ids.add(vehicle_id)
        for vehicle_id in dict(state.get("constraints") or {}).get("resolvedVehicleIds") or []:
            if isinstance(vehicle_id, str):
                trusted_vehicle_ids.add(vehicle_id)
        candidate_vehicles = tuple(
            {
                str(item.get("vehicleId")): dict(item)
                for item in (
                    *references.displayed_vehicles,
                    *references.page_vehicles,
                    *references.displayed_choices,
                )
                if item.get("vehicleId")
            }.values()
        )
        return cls(
            frozenset(page_vehicle_ids),
            frozenset(trusted_vehicle_ids),
            frozenset(_ids(references.displayed_offers, "offerId")),
            frozenset(_ids(references.displayed_dealerships, "dealershipId")),
            tuple(references.displayed_dealerships),
            tuple(references.displayed_choices),
            frozenset(
                str(item["entityReference"])
                for item in references.displayed_choices
                if item.get("entityReference")
            ),
            references.vehicle_search_state,
            state,
            candidate_vehicles,
            turn_understanding,
        )


def _selected_appointment(
    arguments: dict[str, Any], context: GroundingContext
) -> dict[str, object] | None:
    """Return the trusted appointment selected by this concrete operation."""

    slot_id = arguments.get("slotId")
    if not slot_id:
        return None
    reference = f"appointment:{slot_id}"
    return next(
        (
            item
            for item in context.displayed_choices
            if item.get("entityReference") == reference
        ),
        None,
    )


class ToolPolicyGate:
    """Enforce catalogue-declared runtime safety on concrete tool calls."""

    def __init__(self, catalogue: UnifiedToolCatalog):
        self.catalogue = catalogue
        self._preconditions = {
            "trusted_page_vehicle_ids": self._trusted_page_vehicle_ids,
            "trusted_vehicle_ids": self._trusted_vehicle_ids,
            "trusted_vehicle_id": self._trusted_vehicle_id,
            "unresolved_vehicle_description": self._unresolved_vehicle_description,
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
            "trusted_workflow_entities": self._trusted_workflow_entities,
            "active_capability": self._active_capability,
            "paused_capability": self._paused_capability,
        }
        declared = {
            condition
            for definition in catalogue.definitions()
            for condition in definition.preconditions
        }
        unknown = declared - self._preconditions.keys()
        if unknown:
            raise ValueError(f"unknown catalogue preconditions: {sorted(unknown)}")
        self._validate_reference_input_contracts()
        self._validate_live_field_resolver_contracts()

    def _validate_reference_input_contracts(self) -> None:
        """Reject stale reference-binding metadata before it can affect planner affordances."""

        namespaces = set(REFERENCE_FIELD_NAMESPACES.values())
        for definition in self.catalogue.definitions():
            properties = set(
                (definition.provider_input_schema.get("properties") or {}).keys()
            )
            fields = [field for field, _namespace in definition.reference_inputs]
            if len(fields) != len(set(fields)):
                raise ValueError(
                    f"duplicate reference input binding for tool: {definition.id}"
                )
            invalid_fields = set(fields) - properties
            if invalid_fields:
                raise ValueError(
                    f"reference input binding is absent from provider schema: "
                    f"{definition.id}.{sorted(invalid_fields)}"
                )
            invalid_namespaces = {
                namespace
                for _field, namespace in definition.reference_inputs
                if namespace not in namespaces
            }
            if invalid_namespaces:
                raise ValueError(
                    f"unknown reference input namespace for {definition.id}: "
                    f"{sorted(invalid_namespaces)}"
                )

    def _validate_live_field_resolver_contracts(self) -> None:
        """Fail at startup when workflow dependency metadata cannot be executed safely."""

        for spec in CapabilityRegistry.SPECS.values():
            for resolution in spec.live_field_resolvers:
                if resolution.field not in spec.public_fields:
                    raise ValueError(
                        f"live resolver field is not public: {spec.kind}.{resolution.field}"
                    )
                if not set(resolution.prerequisite_fields).issubset(spec.public_fields):
                    raise ValueError(
                        f"live resolver prerequisites are not public: {spec.kind}.{resolution.field}"
                    )
                definition = self.catalogue.get(resolution.tool)
                if definition.risk != "read" or definition.result_mode != "render":
                    raise ValueError(
                        f"live resolver must be a renderable read: {resolution.tool}"
                    )
                provider_fields = set(definition.provider_input_schema.get("properties") or {})
                declared_fields = {
                    *resolution.prerequisite_fields,
                    *resolution.filter_fields,
                }
                if not declared_fields.issubset(provider_fields):
                    raise ValueError(
                        f"live resolver declares unsupported inputs: {resolution.tool}"
                    )

    def validate(
        self,
        calls: list[ToolCall],
        *,
        state: dict[str, Any],
        references,
        latest_customer_message: str = "",
        recent_customer_messages: tuple[str, ...] = (),
        turn_understanding: TurnUnderstanding | None = None,
    ) -> PolicyDecision:
        # Accepted for compatibility with injected providers; policy never interprets prose.
        del latest_customer_message, recent_customer_messages
        if not calls or len(calls) > 4:
            raise ValueError("a proposal must contain between one and four tool calls")
        context = GroundingContext.build(
            state,
            references,
            turn_understanding,
        )
        definitions = [self.catalogue.get(call.name) for call in calls]
        terminal_definitions = [
            definition for definition in definitions if definition.result_mode != "evidence"
        ]
        if len(terminal_definitions) > 4:
            raise ValueError("a proposal can contain at most four terminal reads")
        if sum(definition.result_mode == "workflow" for definition in terminal_definitions) > 1:
            raise ValueError("a proposal can start at most one workflow")
        validated: list[ToolCall] = []
        for call, definition in zip(calls, definitions, strict=True):
            if call.intent_kind is not None and call.intent_kind not in supported_intents(call.name):
                raise ValueError("tool is incompatible with its declared customer intent")
            if definition.risk == "confirmed_write":
                raise ValueError("confirmed writes are never model callable")
            arguments = dict(call.arguments)
            arguments = self._bind_resolved_inputs(arguments, definition, context)
            self._validate_live_selector_rebinding(call.name, context)
            if call.name in {"search_vehicles", "refine_vehicle_search"}:
                _raise_for_contradictory_vehicle_ranges(arguments)
            provider_arguments_validated = False
            if call.name == "refine_vehicle_search":
                arguments = self._vehicle_search_refinement(arguments, context)
            elif call.name == "refine_workshop_slots":
                arguments = self._workshop_search_refinement(arguments, context)
            elif call.name in {"prepare_workshop_booking", "prepare_test_drive"}:
                arguments = self._workflow_selector_merge(call.name, arguments, context)
                if call.name == "prepare_test_drive":
                    arguments = self._resolve_test_drive_vehicle(arguments, context)
            elif call.name == "list_test_drive_slots":
                schedule_mode = str(arguments.get("schedulePreferenceMode", "preserve"))
                if schedule_mode == "replace" and not any(
                    arguments.get(field) is not None
                    for field in ("dateFrom", "dateTo", "timeOfDay")
                ):
                    # A replacement requires a new schedule value. If semantic resolution
                    # intentionally discarded the old preference while supplying only another
                    # selector, browse live availability for that selector instead.
                    schedule_mode = "clear"
                    arguments["schedulePreferenceMode"] = "clear"
                scheduling = dict(
                    dict(context.workflow_state.get("constraints") or {}).get(
                        "schedulingPreferences"
                    )
                    or {}
                )
                if schedule_mode in {"replace", "clear"}:
                    scheduling = {}
                arguments = {**scheduling, **arguments}
                vehicle_id = dict(context.workflow_state.get("entities") or {}).get(
                    "vehicleId"
                )
                if vehicle_id:
                    arguments.setdefault("vehicleId", vehicle_id)
            elif call.name == "compare_vehicles":
                # Validate the model-authored surface before adding provenance that only the
                # server-owned comparison state is allowed to provide.
                definition.validate_provider_arguments(arguments)
                provider_arguments_validated = True
                selection_basis = dict(context.workflow_state.get("constraints") or {}).get(
                    "comparisonSelectionBasis"
                )
                if selection_basis:
                    arguments["selectionBasis"] = selection_basis
            if (
                call.name in {"list_workshop_slots", "refine_workshop_slots"}
                and arguments.get("workflowMode") == "amendment"
            ):
                self._verified_booking(arguments, context)
            if call.name.startswith("prepare_"):
                arguments = self._ground_resolved_workflow_values(
                    call.name, arguments, context
                )
            self._validate_expected_choice(call.name, arguments, context)
            if not provider_arguments_validated:
                definition.validate_provider_arguments(arguments)
            for condition in definition.preconditions:
                self._preconditions[condition](arguments, context)
            if call.name == "prepare_test_drive" and arguments.get("vehicleId"):
                arguments["selectionEvidence"] = self._test_drive_selection_evidence(
                    str(arguments["vehicleId"]), context
                )
            definition.validate_arguments(arguments)
            validated.append(
                ToolCall(call.id, call.name, arguments, call.intent_id, call.intent_kind)
            )
        validated = self._drop_redundant_test_drive_search(validated)
        compiled = self._compile_live_field_resolvers(validated, context)
        if len(compiled) > 4:
            raise ValueError("a compiled proposal can contain at most four business tool calls")
        return PolicyDecision(compiled)

    @staticmethod
    def _bind_resolved_inputs(
        arguments: dict[str, Any],
        definition,
        context: GroundingContext,
    ) -> dict[str, Any]:
        """Make validated semantic inputs authoritative for the selected capability.

        The turn resolver owns interpretation of customer language. Once it has produced a
        provenance-checked field/value pair, the application compiles that value into every
        compatible business call. The planner's restatement is discarded rather than repaired by
        another model request. The binding is driven entirely by the capability's provider schema,
        so adding a field or workflow does not require utterance-specific code.
        """

        understanding = context.turn_understanding
        if understanding is None:
            return arguments
        accepted = set((definition.provider_input_schema.get("properties") or {}).keys())
        bound = dict(arguments)
        for resolved in understanding.resolvedInputs:
            field = resolved.field
            value = resolved.value
            if isinstance(value, list) and field in _MULTI_VALUE_INPUT_ALIASES:
                plural = _MULTI_VALUE_INPUT_ALIASES[field]
                if plural in accepted:
                    bound.pop(field, None)
                    bound[plural] = value
                continue
            if field in _MULTI_VALUE_INPUT_ALIASES.values():
                singular = next(
                    name for name, plural in _MULTI_VALUE_INPUT_ALIASES.items() if plural == field
                )
                if field in accepted:
                    bound.pop(singular, None)
                    bound[field] = value
                continue
            if field not in accepted:
                alias = next(
                    (
                        candidate
                        for candidate in _COMPATIBLE_INPUT_ALIASES.get(field, ())
                        if candidate in accepted
                    ),
                    None,
                )
                if alias is not None:
                    bound[alias] = value
                continue
            bound[field] = value
        return bound

    @staticmethod
    def _drop_redundant_test_drive_search(calls: list[ToolCall]) -> list[ToolCall]:
        """Discard an empty discovery read once this turn has a trusted vehicle selection."""

        selected = any(
            call.name == "prepare_test_drive" and call.arguments.get("vehicleId")
            for call in calls
        )
        if not selected:
            return calls
        return [
            call
            for call in calls
            if not (call.name == "search_vehicles" and not call.arguments)
        ]

    def _compile_live_field_resolvers(
        self,
        calls: list[ToolCall],
        context: GroundingContext,
    ) -> list[ToolCall]:
        """Turn workflow field dependencies into executable, application-owned transitions.

        A draft tool persists intent and supplied values. It must not become a conversational
        dead end when its next required public value can only come from a live business read.
        The model may propose the draft operation; this compiler owns the required resolver.
        """

        compiled: list[ToolCall] = []
        for index, call in enumerate(calls):
            spec = CapabilityRegistry.for_agent_tool(call.name)
            resolutions = spec.live_field_resolvers if spec is not None else ()
            applicable = next(
                (
                    resolution
                    for resolution in resolutions
                    if call.arguments.get(resolution.field) is None
                    and not any(
                        later.name == resolution.tool for later in calls[index + 1 :]
                    )
                ),
                None,
            )
            if applicable is None or spec is None:
                compiled.append(call)
                continue

            resolver_call = self._live_resolver_call(call, spec, applicable, context)
            if resolver_call is None:
                compiled.append(call)
                continue

            if not self._draft_is_already_current(call, spec, applicable, context):
                compiled.append(call)
            compiled.append(resolver_call)
        return compiled

    def _live_resolver_call(
        self,
        call: ToolCall,
        spec: CapabilitySpec,
        resolution: LiveFieldResolver,
        context: GroundingContext,
    ) -> ToolCall | None:
        entities = dict(context.workflow_state.get("entities") or {})
        collected = dict(
            dict(context.workflow_state.get("constraints") or {}).get(
                "collectedPublicValues"
            )
            or {}
        )
        arguments: dict[str, Any] = {}
        for field in resolution.prerequisite_fields:
            value = call.arguments.get(field, entities.get(field, collected.get(field)))
            if value is None:
                return None
            arguments[field] = value
        for field in resolution.filter_fields:
            if call.arguments.get(field) is not None:
                arguments[field] = call.arguments[field]

        definition = self.catalogue.get(resolution.tool)
        definition.validate_provider_arguments(arguments)
        for condition in definition.preconditions:
            self._preconditions[condition](arguments, context)
        definition.validate_arguments(arguments)
        return ToolCall(
            f"{call.id}-resolve-{resolution.field}",
            resolution.tool,
            arguments,
            call.intent_id,
            call.intent_kind,
        )

    @staticmethod
    def _draft_is_already_current(
        call: ToolCall,
        spec: CapabilitySpec,
        resolution: LiveFieldResolver,
        context: GroundingContext,
    ) -> bool:
        if any(
            call.arguments.get(field) is not None
            for field in resolution.replacement_fields
        ):
            return False
        if context.workflow_state.get("activeWorkflow") != spec.kind:
            return False
        entities = dict(context.workflow_state.get("entities") or {})
        collected = dict(
            dict(context.workflow_state.get("constraints") or {}).get(
                "collectedPublicValues"
            )
            or {}
        )
        for field in spec.public_fields:
            if field == resolution.field or field in resolution.prerequisite_fields:
                continue
            if field in call.arguments and call.arguments[field] != collected.get(field):
                return False
        return all(
            call.arguments.get(field) == entities.get(field, collected.get(field))
            for field in resolution.prerequisite_fields
        )

    @staticmethod
    def _validate_expected_choice(
        tool_name: str,
        arguments: dict[str, Any],
        context: GroundingContext,
    ) -> None:
        if (
            context.workflow_state.get("activeWorkflow") != "workshop_booking"
            or context.workflow_state.get("stage") != "choosing_service"
            or tool_name not in {"list_workshop_slots", "refine_workshop_slots"}
        ):
            return
        service_id = arguments.get("serviceTypeId")
        if service_id is None:
            return
        if f"service:{service_id}" not in context.displayed_choice_references:
            raise ValueError("service selection contains an untrusted choice")

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
            ("makes", "excludedMakes"),
            ("model", "excludedModels"),
            ("models", "excludedModels"),
            ("colour", "colours"),
            ("fuelType", "excludedFuelTypes"),
            ("fuelTypes", "excludedFuelTypes"),
            ("transmission", "excludedTransmissions"),
            ("transmissions", "excludedTransmissions"),
            ("bodyStyle", "excludedBodyStyles"),
            ("bodyStyles", "excludedBodyStyles"),
            ("minPricePence", "maxPricePence"),
            ("minMileage", "maxMileage"),
            ("minYear", "maxYear"),
        )
        for positive, negative in opposing_filters:
            if positive in arguments:
                merged.pop(negative, None)
            if negative in arguments:
                merged.pop(positive, None)
        for singular, plural in _MULTI_VALUE_INPUT_ALIASES.items():
            if singular in arguments:
                merged.pop(plural, None)
            if plural in arguments:
                merged.pop(singular, None)
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
        merged = merge_workshop_continuation_context(
            context.workflow_state, arguments
        )
        incoming_service = arguments.get("serviceTypeId") or arguments.get(
            "serviceTypeName"
        )
        current_service = merged.get("serviceTypeId") or merged.get("serviceTypeName")
        if not current_service and not incoming_service:
            raise ValueError("workshop refinement requires a service candidate")
        return merged

    @staticmethod
    def _workflow_selector_merge(
        tool_name: str,
        arguments: dict[str, Any],
        context: GroundingContext,
    ) -> dict[str, Any]:
        """Carry only server-trusted selectors into the next capability operation."""

        merged = dict(arguments)
        entities = dict(context.workflow_state.get("entities") or {})
        keys = (
            ("serviceTypeId", "dealershipId")
            if tool_name == "prepare_workshop_booking"
            else ("vehicleId",)
        )
        spec = CapabilityRegistry.for_agent_tool(tool_name)
        for key in keys:
            resolution = next(
                (
                    item
                    for item in (spec.live_field_resolvers if spec is not None else ())
                    if item.field == key
                ),
                None,
            )
            replacement_requested = bool(
                resolution is not None
                and any(arguments.get(field) is not None for field in resolution.replacement_fields)
            )
            if entities.get(key) is not None and not replacement_requested:
                merged.setdefault(key, entities[key])
        if tool_name == "prepare_test_drive":
            scheduling = dict(
                dict(context.workflow_state.get("constraints") or {}).get(
                    "schedulingPreferences"
                )
                or {}
            )
            for field in ("dateFrom", "dateTo", "timeOfDay"):
                if scheduling.get(field) is not None:
                    merged.setdefault(field, scheduling[field])
        if tool_name == "prepare_workshop_booking" and not merged.get("dealershipId"):
            collected = dict(
                dict(context.workflow_state.get("constraints") or {}).get(
                    "collectedPublicValues"
                )
                or {}
            )
            if collected.get("selectedDealershipId"):
                merged["dealershipId"] = collected["selectedDealershipId"]
        return merged

    @staticmethod
    def _validate_live_selector_rebinding(
        tool_name: str,
        context: GroundingContext,
    ) -> None:
        """Reject a continuation that would discard a newly described subject selector."""

        understanding = context.turn_understanding
        active = str(context.workflow_state.get("activeWorkflow") or "")
        spec = CapabilityRegistry.for_active_workflow(active)
        if understanding is None or spec is None or tool_name == spec.agent_tool:
            return
        if spec.kind not in supported_intents(tool_name):
            return
        resolved_fields = {item.field for item in understanding.resolvedInputs}
        references = set(understanding.resolvedReferences)
        entities = dict(context.workflow_state.get("entities") or {})
        for resolution in spec.live_field_resolvers:
            namespace = REFERENCE_FIELD_NAMESPACES.get(resolution.field)
            has_resolved_reference = bool(
                namespace
                and any(reference.startswith(f"{namespace}:") for reference in references)
            )
            if (
                entities.get(resolution.field) is not None
                and resolved_fields.intersection(resolution.replacement_fields)
                and not has_resolved_reference
            ):
                raise ValueError(
                    f"{resolution.field} must be resolved again before workflow continuation"
                )

    @staticmethod
    def _resolve_test_drive_vehicle(
        arguments: dict[str, Any], context: GroundingContext
    ) -> dict[str, Any]:
        """Merge persisted identity without interpreting customer language.

        Hosted semantic resolution owns reference understanding. Policy checks provenance later;
        it never rewrites a model-selected entity from words, ordinals, or attributes.
        """

        resolved = dict(arguments)
        persisted = dict(context.workflow_state.get("constraints") or {}).get(
            "selectionEvidence"
        )
        collected = dict(
            dict(context.workflow_state.get("constraints") or {}).get(
                "collectedPublicValues"
            )
            or {}
        )
        current_vehicle = dict(context.workflow_state.get("entities") or {}).get(
            "vehicleId"
        )
        if (
            resolved.get("vehicleId")
            and current_vehicle == resolved["vehicleId"]
            and isinstance(persisted, dict)
            and persisted.get("vehicleId") == resolved["vehicleId"]
        ):
            return resolved
        if (
            resolved.get("vehicleId")
            and current_vehicle == resolved["vehicleId"]
            and collected.get("vehicleId") == resolved["vehicleId"]
        ):
            return resolved

        if resolved.get("vehicleId"):
            for field in ("make", "model", "q"):
                resolved.pop(field, None)
        return resolved

    @staticmethod
    def _test_drive_selection_evidence(
        vehicle_id: str, context: GroundingContext
    ) -> dict[str, str]:
        persisted = dict(context.workflow_state.get("constraints") or {}).get(
            "selectionEvidence"
        )
        if (
            isinstance(persisted, dict)
            and persisted.get("vehicleId") == vehicle_id
        ):
            return {
                "vehicleId": vehicle_id,
                "basis": "persisted_selection",
                "matchedIdentity": str(
                    persisted.get("matchedIdentity") or vehicle_id
                )[:160],
            }
        entities = dict(context.workflow_state.get("entities") or {})
        collected = dict(
            dict(context.workflow_state.get("constraints") or {}).get(
                "collectedPublicValues"
            )
            or {}
        )
        if (
            entities.get("vehicleId") == vehicle_id
            and collected.get("vehicleId") == vehicle_id
        ):
            return {
                "vehicleId": vehicle_id,
                "basis": "persisted_selection",
                "matchedIdentity": vehicle_id,
            }
        candidate = next(
            (
                item
                for item in context.candidate_vehicles
                if item.get("vehicleId") == vehicle_id
            ),
            None,
        )
        if candidate is None:
            raise ValueError("test-drive vehicle has no current selection evidence")
        identity = " ".join(
            str(candidate.get(field) or "").strip()
            for field in ("make", "model", "variant")
        ).strip()
        return {
            "vehicleId": vehicle_id,
            "basis": "ai_resolved_trusted_reference",
            "matchedIdentity": identity[:160] or vehicle_id,
        }

    @staticmethod
    def _ground_resolved_workflow_values(
        tool_name: str,
        arguments: dict[str, Any],
        context: GroundingContext,
    ) -> dict[str, Any]:
        """Compile workflow values exclusively from typed semantic or persisted provenance.

        A workflow preparation call may legally be incomplete because the capability collector
        owns its missing fields. Planner-authored values that semantic resolution did not extract
        are therefore discarded instead of either becoming trusted data or failing the whole
        customer turn. This applies uniformly to every registered transactional capability.
        """

        understanding = context.turn_understanding
        spec = CapabilityRegistry.for_agent_tool(tool_name)
        if understanding is None or spec is None:
            return arguments
        resolved = {item.field: item.value for item in understanding.resolvedInputs}
        resolved_references = set(understanding.resolvedReferences)
        state = context.workflow_state
        persisted = {
            **dict(state.get("entities") or {}),
            **dict(dict(state.get("constraints") or {}).get("collectedPublicValues") or {}),
        }
        grounded = dict(arguments)
        selected_appointment = _selected_appointment(grounded, context)
        for field in spec.public_fields:
            if field not in grounded or grounded[field] is None:
                continue
            value = grounded[field]
            if field in persisted and _same_resolved_value(value, persisted[field]):
                continue
            namespace = REFERENCE_FIELD_NAMESPACES.get(field)
            if namespace is not None:
                if f"{namespace}:{value}" in resolved_references:
                    continue
                if (
                    selected_appointment is not None
                    and field in _APPOINTMENT_OWNED_SELECTORS
                    and selected_appointment.get(field) is not None
                ):
                    if _same_resolved_value(value, selected_appointment[field]):
                        # Appointment identity is the provenance for the selectors it owns.
                        # Requiring separate references for the same service, dealership, or
                        # vehicle makes a valid finite-choice answer impossible to consume.
                        continue
                    raise ValueError(f"{field} is not grounded in the selected appointment")
                grounded.pop(field, None)
                continue
            if field in resolved and _same_resolved_value(value, resolved[field]):
                continue
            grounded.pop(field, None)
        return grounded

    @staticmethod
    def _current_workshop_search(arguments: dict[str, Any], context: GroundingContext) -> str:
        has_complete_starting_point = bool(
            arguments.get("serviceTypeId") or arguments.get("serviceTypeName")
        )
        if (
            context.workflow_state.get("activeWorkflow") != "workshop_booking"
            and not has_complete_starting_point
        ):
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
    def _unresolved_vehicle_description(
        arguments: dict[str, Any], context: GroundingContext
    ) -> str:
        del arguments
        understanding = context.turn_understanding
        if understanding is not None and any(
            reference.startswith("vehicle:")
            for reference in understanding.resolvedReferences
        ):
            raise ValueError("described vehicle resolver requires no resolved vehicle reference")
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
    def _trusted_workflow_entities(
        arguments: dict[str, Any], context: GroundingContext
    ) -> str:
        """Constrain AI-captured selectors to the current trusted conversation results."""

        entities = dict(context.workflow_state.get("entities") or {})
        choices = context.displayed_choice_references
        slot_id = arguments.get("slotId")
        if slot_id and f"appointment:{slot_id}" not in choices:
            raise ValueError("workflow contains an untrusted appointment ID")
        selected = _selected_appointment(arguments, context)
        if selected and selected.get("vehicleId"):
            arguments["vehicleId"] = selected["vehicleId"]
        vehicle_id = arguments.get("vehicleId")
        if vehicle_id and str(vehicle_id) not in context.trusted_vehicle_ids:
            raise ValueError("workflow contains an untrusted vehicle ID")
        if selected and arguments.get("selectedStartsAt") is None:
            trusted_fields = {
                "startsAt": "selectedStartsAt",
                "dealershipId": "selectedDealershipId",
                "dealershipName": "selectedDealershipName",
                "serviceTypeId": "selectedServiceTypeId",
                "serviceTypeName": "selectedServiceName",
            }
            for source, target in trusted_fields.items():
                if selected.get(source) is not None:
                    arguments[target] = selected[source]
        service_id = arguments.get("serviceTypeId")
        if service_id and not (
            entities.get("serviceTypeId") == service_id
            or f"service:{service_id}" in choices
            or (selected and selected.get("serviceTypeId") == service_id)
        ):
            raise ValueError("workflow contains an untrusted service ID")
        dealership_id = arguments.get("dealershipId")
        selected_dealership_id = dict(
            dict(context.workflow_state.get("constraints") or {}).get(
                "collectedPublicValues"
            )
            or {}
        ).get("selectedDealershipId")
        trusted_dealerships = {
            *context.displayed_dealership_ids,
            *(
                [str(entities["dealershipId"])]
                if entities.get("dealershipId")
                else []
            ),
            *([str(selected_dealership_id)] if selected_dealership_id else []),
            *(
                [str(selected["dealershipId"])]
                if selected and selected.get("dealershipId")
                else []
            ),
        }
        if (
            slot_id
            and arguments.get("selectedDealershipId")
            and dealership_id
            and str(dealership_id) != str(arguments["selectedDealershipId"])
        ):
            raise ValueError(
                "the selected appointment belongs to another dealership; resolve a new slot"
            )
        if dealership_id and str(dealership_id) not in trusted_dealerships:
            raise ValueError("workflow contains an untrusted dealership ID")
        return ""

    @staticmethod
    def _trusted_optional_dealership_id(
        arguments: dict[str, Any], context: GroundingContext
    ) -> str:
        dealership_id = arguments.get("dealershipId")
        if dealership_id is None:
            return ""
        workflow_dealership_id = dict(context.workflow_state.get("entities") or {}).get(
            "dealershipId"
        )
        trusted_ids = {
            *context.displayed_dealership_ids,
            *([str(workflow_dealership_id)] if workflow_dealership_id else []),
        }
        if str(dealership_id) not in trusted_ids:
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
            raise ValueError("dealership selection requires clarification")
        raise ValueError("dealership selection is ambiguous")

    @staticmethod
    def _single_dealership_or_all(arguments: dict[str, Any], context: GroundingContext) -> str:
        """Reuse one displayed location; keep a multi-location list operation plural."""
        if arguments.get("town"):
            return ""
        if arguments.get("dealershipId"):
            return ""
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
            raise ValueError("dealership selection is ambiguous")
        raise ValueError("dealership operation requires current displayed dealership context")

    @staticmethod
    def _verified_booking(arguments: dict[str, Any], context: GroundingContext) -> str:
        del arguments
        state = context.workflow_state
        booking_status = str(
            dict(state.get("constraints") or {}).get("bookingStatus") or ""
        ).casefold()
        if booking_status and booking_status != "confirmed":
            raise ValueError("only a confirmed workshop booking can be changed")
        if (
            not (
                state.get("activeWorkflow") == "existing_workshop_booking"
                and state.get("stage") == "verified"
            )
            and not (
                state.get("activeWorkflow") == "workshop_amend"
                and state.get("stage") == "choosing_time"
                and dict(state.get("constraints") or {}).get("verifiedBooking") is True
            )
        ):
            raise ValueError("workshop booking must be verified before it can be changed")
        return ""

    @staticmethod
    def _active_capability(arguments: dict[str, Any], context: GroundingContext) -> str:
        del arguments
        if not context.workflow_state.get("activeWorkflow"):
            raise ValueError("there is no active capability")
        return ""

    @staticmethod
    def _paused_capability(arguments: dict[str, Any], context: GroundingContext) -> str:
        del arguments
        if not isinstance(context.workflow_state.get("pausedWorkflow"), dict):
            raise TypeError("there is no paused capability")
        return ""


def _ids(items: list[dict[str, object]], field: str) -> set[str]:
    return {str(item[field]) for item in items if item.get(field)}


def _same_resolved_value(left: Any, right: Any) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return " ".join(left.casefold().split()) == " ".join(right.casefold().split())
    return left == right


def _raise_for_contradictory_vehicle_ranges(arguments: dict[str, Any]) -> None:
    from webchat.orchestration.tools.result import ToolResult

    ranges = (
        ("minPricePence", "maxPricePence", "price", lambda value: f"£{value / 100:,.0f}"),
        ("minMileage", "maxMileage", "mileage", lambda value: f"{value:,} miles"),
        ("minYear", "maxYear", "year", str),
    )
    for lower_field, upper_field, label, display in ranges:
        lower = arguments.get(lower_field)
        upper = arguments.get(upper_field)
        if not isinstance(lower, int) or not isinstance(upper, int) or lower <= upper:
            continue
        text = (
            f"Those {label} limits conflict: the maximum is {display(upper)} and the minimum "
            f"is {display(lower)}. Which limit should I keep?"
        )
        raise PolicyClarification(
            ToolResult(
                text,
                None,
                None,
                {
                    "outcome": "ambiguous",
                    "constraint": label,
                    "minimum": lower,
                    "maximum": upper,
                },
            )
        )
