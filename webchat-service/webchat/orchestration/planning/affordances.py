"""Application-owned legal action set for hosted capability planning.

Semantic resolution owns what the customer meant. This module does not inspect customer wording;
it uses that typed meaning plus the executable catalogue and the same policy gate used after
planning to remove actions that cannot currently pass their structural and trust contracts.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from webchat.integrations.contracts import PlanningContext, ToolCall
from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.contracts.semantics import REFERENCE_FIELD_NAMESPACES
from webchat.orchestration.intent_contracts import supported_intents
from webchat.orchestration.policy import ToolPolicyGate
from webchat.orchestration.retrieval.candidates import CandidateSet


@dataclass(frozen=True)
class PlannerAffordances:
    """Business operations and clarification authority for one resolved turn."""

    candidates: CandidateSet
    executable_tool_ids: frozenset[str]
    blocked_tool_ids: frozenset[str]
    allow_generic_clarification: bool


class PlannerAffordanceEvaluator:
    """Preflight candidate operations without executing business side effects."""

    def __init__(self, catalogue: UnifiedToolCatalog):
        self.catalogue = catalogue
        self.policy = ToolPolicyGate(catalogue)

    def evaluate(
        self,
        candidates: CandidateSet,
        context: PlanningContext,
    ) -> PlannerAffordances:
        understanding = context.turn_understanding
        if understanding is None:
            return PlannerAffordances(
                candidates,
                frozenset(),
                frozenset(),
                bool(_declared_blockers(candidates)),
            )
        if understanding.ambiguity in {"intent", "reference"}:
            # Intent and reference ambiguity have dedicated typed clarification contracts, so a
            # generic clarification must not compete with either one.
            return PlannerAffordances(
                candidates,
                frozenset(),
                frozenset(definition.id for definition in candidates.tools),
                False,
            )
        if understanding.ambiguity == "required_input":
            # Unlike intent/reference ambiguity, required input has no closed candidate set. No
            # business operation may run, but the planner must retain its generic question tool.
            return PlannerAffordances(
                candidates,
                frozenset(),
                frozenset(definition.id for definition in candidates.tools),
                True,
            )
        if not understanding.intentKinds:
            return PlannerAffordances(candidates, frozenset(), frozenset(), False)

        executable: list = []
        blocked: list = []
        declared_intents = set(understanding.intentKinds)
        executable_by_intent: dict[str, list] = {intent: [] for intent in understanding.intentKinds}
        for definition in candidates.tools:
            compatible = declared_intents & supported_intents(definition.id)
            if not compatible:
                continue
            if self._can_execute(definition.id, compatible, context):
                executable.append(definition)
                for intent in compatible:
                    executable_by_intent[intent].append(definition)
            else:
                blocked.append(definition)

        every_intent_can_progress = all(executable_by_intent.values())
        if every_intent_can_progress:
            # Once every resolved obligation has a legal operation, neither blocked siblings nor
            # generic clarification are valid planner affordances. Knowledge evidence remains
            # available for the separately governed mixed-answer path.
            unique = {definition.id: definition for definition in executable}
            narrowed = CandidateSet(tuple(unique.values()), candidates.knowledge)
            return PlannerAffordances(
                narrowed,
                frozenset(unique),
                frozenset(definition.id for definition in blocked),
                False,
            )

        return PlannerAffordances(
            candidates,
            frozenset(definition.id for definition in executable),
            frozenset(definition.id for definition in blocked),
            bool(_declared_blockers(candidates)),
        )

    def _can_execute(
        self,
        tool_name: str,
        compatible_intents: set[str],
        context: PlanningContext,
    ) -> bool:
        definition = self.catalogue.get(tool_name)
        arguments = trusted_reference_arguments(
            definition.provider_input_schema,
            definition.reference_inputs,
            context,
        )
        for intent in sorted(compatible_intents):
            try:
                decision = self.policy.validate(
                    [
                        ToolCall(
                            "affordance-preflight",
                            tool_name,
                            arguments,
                            intent_kind=intent,
                        )
                    ],
                    state=context.workflow_state,
                    references=context,
                    turn_understanding=context.turn_understanding,
                )
            except (KeyError, TypeError, ValueError) as error:
                # Provider schemas contain both application-owned identity inputs and ordinary
                # arguments that the planner is expected to author from the conversation.  A
                # partial preflight cannot manufacture the latter merely to satisfy schema
                # validation.  When there are no policy preconditions and no required unresolved
                # identity fields, keep the operation available and let the final policy pass
                # validate the planner's concrete arguments.  This is especially important after
                # a contextual reference clarification: the latest reply names the subject, while
                # the factual question lives in the preceding turn.
                provisional = ToolCall(
                    "affordance-preflight",
                    tool_name,
                    arguments,
                    intent_kind=intent,
                )
                if (
                    not _only_missing_provider_fields(error)
                    or definition.preconditions
                    or _missing_required_reference_input(definition, arguments)
                    or not _binds_answered_reference_choice(
                        [provisional],
                        self.catalogue,
                        context,
                    )
                ):
                    continue
                return True
            if not _binds_answered_reference_choice(
                decision.calls,
                self.catalogue,
                context,
            ):
                continue
            return True
        return False


def _declared_blockers(candidates: CandidateSet) -> tuple[str, ...]:
    return tuple(
        definition.id
        for definition in candidates.tools
        if definition.provider_input_schema.get("required") or definition.preconditions
    )


def trusted_reference_arguments(
    schema: dict,
    reference_inputs: tuple[tuple[str, str], ...],
    context: PlanningContext,
) -> dict:
    """Project resolved references into fields without interpreting customer language."""

    understanding = context.turn_understanding
    if understanding is None:
        return {}
    properties = set((schema.get("properties") or {}).keys())
    references: dict[str, list[str]] = {}
    for reference in understanding.resolvedReferences:
        if ":" not in reference:
            continue
        namespace, value = reference.split(":", 1)
        references.setdefault(namespace, []).append(value)

    arguments: dict = {}
    for field, namespace in REFERENCE_FIELD_NAMESPACES.items():
        values = references.get(namespace, [])
        if field in properties and len(values) == 1:
            arguments[field] = values[0]
    if "vehicleIds" in properties and references.get("vehicle"):
        arguments["vehicleIds"] = references["vehicle"]
    for field, namespace in reference_inputs:
        if field not in properties or field in arguments:
            continue
        values = references.get(namespace, [])
        if len(values) == 1:
            arguments[field] = values[0]
    return arguments


def reference_input_bindings(definition) -> tuple[tuple[str, str], ...]:
    """Return every provider-visible field that can consume a trusted reference.

    Common entity fields are declared once by the semantic contract; capability-specific aliases
    remain catalogue metadata.  Keeping this projection shared prevents affordance preflight and
    final plan validation from disagreeing about whether a selected reference can be consumed.
    """

    properties = set((definition.provider_input_schema.get("properties") or {}).keys())
    bindings = [
        (field, namespace)
        for field, namespace in REFERENCE_FIELD_NAMESPACES.items()
        if field in properties
    ]
    if "vehicleIds" in properties:
        bindings.append(("vehicleIds", "vehicle"))
    bindings.extend(
        (field, namespace)
        for field, namespace in definition.reference_inputs
        if field in properties
    )
    return tuple(dict.fromkeys(bindings))


def _missing_required_reference_input(definition, arguments: dict) -> bool:
    required = set(definition.provider_input_schema.get("required") or ())
    return any(
        field in required and not arguments.get(field)
        for field, _namespace in reference_input_bindings(definition)
    )


def _only_missing_provider_fields(error: Exception) -> bool:
    """True only for the partial probe's expected absent planner-authored arguments."""

    return (
        isinstance(error, ValidationError)
        and bool(error.errors())
        and all(item.get("type") == "missing" for item in error.errors(include_url=False))
    )


def _binds_answered_reference_choice(
    calls: list[ToolCall],
    catalogue: UnifiedToolCatalog,
    context: PlanningContext,
) -> bool:
    """Reject an operation plan that drops the answer to a closed finite choice.

    The check runs on policy-validated calls so it covers both typed entity references and finite
    scalar workflow values compiled from semantic input. It is deliberately capability-neutral:
    the open question and catalogue bindings declare how a choice reaches an operation.
    """

    understanding = context.turn_understanding
    question = context.pending_interaction or {}
    if (
        understanding is None
        or understanding.dialogueAct != "answer_open_question"
        or question.get("kind") != "reference_choice"
    ):
        return True
    candidates = set(question.get("candidate_references") or ())
    selected = set(understanding.resolvedReferences).intersection(candidates)
    if not selected:
        return False
    bound: set[str] = set()
    accepted_namespaces: set[str] = set()
    for call in calls:
        definition = catalogue.get(call.name)
        for field, namespace in reference_input_bindings(definition):
            accepted_namespaces.add(namespace)
            value = call.arguments.get(field)
            values = value if isinstance(value, list) else [value]
            bound.update(f"{namespace}:{item}" for item in values if isinstance(item, str) and item)

    # Application-owned enum choices use a typed reference for selection identity and a separate
    # scalar field for execution. Both must agree with the persisted question and semantic input.
    expected_fields = set(question.get("expected_fields") or ())
    required_namespaces = accepted_namespaces.union(
        namespace
        for field, namespace in REFERENCE_FIELD_NAMESPACES.items()
        if field in expected_fields
    )
    resolved_inputs = {item.field: item.value for item in understanding.resolvedInputs}
    displayed = {
        str(item.get("entityReference")): item
        for item in context.displayed_choices
        if isinstance(item, dict) and item.get("entityReference")
    }
    required_selected = {
        reference
        for reference in selected
        if reference.split(":", 1)[0] in required_namespaces
        or reference.startswith("workflow_option:")
    }
    for reference in required_selected - bound:
        choice = displayed.get(reference) or {}
        field = choice.get("inputField")
        value = choice.get("value")
        if (
            isinstance(field, str)
            and field in expected_fields
            and resolved_inputs.get(field) == value
            and any(call.arguments.get(field) == value for call in calls)
        ):
            bound.add(reference)
    return required_selected.issubset(bound)
