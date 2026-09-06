"""Hosted Responses transport for tool planning and grounded response composition."""

from __future__ import annotations

import asyncio
import json
import logging
from copy import deepcopy
from dataclasses import replace
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import ValidationError

from webchat.domain.capabilities import CapabilityRegistry
from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.contracts.response import GroundedResponseDraft
from webchat.orchestration.contracts.semantics import TurnUnderstanding
from webchat.orchestration.intent_contracts import supported_intents
from webchat.orchestration.planning.affordances import (
    PlannerAffordanceEvaluator,
    _binds_answered_reference_choice,
    reference_input_bindings,
    trusted_reference_arguments,
)
from webchat.orchestration.planning.prompt import COMPOSER_SYSTEM_POLICY, planner_instructions
from webchat.orchestration.retrieval import FullCatalogRetriever
from webchat.orchestration.retrieval.candidates import CandidateRetriever, CandidateSet

from ..contracts import (
    PlanningContext,
    PlanningValidationError,
    ProviderReply,
    ProviderUnavailableError,
    SemanticMessages,
    ToolCall,
    TurnProposal,
)
from .candidates import _retrieve_candidates
from .protocol import (
    _clarification_tool_definition,
    _customer_knowledge,
    _function_arguments,
    _intent_clarification_tool_definition,
    _interaction_decision_tool_definitions,
    _knowledge_answer_tool_definition,
    _knowledge_response,
    _parse_proposal,
    _provider_reply,
    _reference_clarification_tool_definition,
    _semantic_intent_candidates,
    _social_response_tool_definition,
    _vehicle_navigation_tool_definition,
    response_input,
)
from .turn_resolution import (
    RESOLVE_TURN_TOOL,
    input_resolution_catalogue,
    input_resolution_contracts,
    intent_resolution_catalogue,
    requires_explicit_outcome_disambiguation,
    resolution_request,
    validate_turn_understanding,
)

logger = logging.getLogger(__name__)

# One initial proposal plus one contract-guided repair. Keep this separate from transport retries:
# repeating an invalid semantic plan more than once adds latency without adding a new signal.
PLANNER_ATTEMPTS = 2
PROVIDER_TRANSPORT_ATTEMPTS = 3
COMPOSE_ATTEMPTS = 2
RESOLUTION_ATTEMPTS = 3
COMPOSE_RESPONSE_TOOL = "compose_grounded_response"


def _planning_context(messages: SemanticMessages) -> PlanningContext:
    context = getattr(messages, "planning_context", None)
    if isinstance(context, PlanningContext):
        return context
    latest = next(
        (
            str(message.get("content") or "")
            for message in reversed(messages)
            if message.get("role") == "user"
        ),
        "",
    )
    return PlanningContext(latest_customer_message=latest)


def _outcome_disambiguation_confirms_selection(
    initial: TurnUnderstanding,
    disambiguated: TurnUnderstanding,
    goal: str,
) -> bool:
    return (
        disambiguated.ambiguity == "none"
        and disambiguated.intentStructure == "single_outcome"
        and disambiguated.intentKinds == [goal]
        and disambiguated.resolvedReferences == initial.resolvedReferences
    )


def _continued_open_question_reply(
    context: PlanningContext,
    understanding,
) -> ProviderReply | None:
    """Re-present an active question when the customer only reaffirms its owning goal."""

    question = context.pending_interaction or {}
    goal = str(question.get("goal_intent") or "")
    prompt = str(question.get("prompt") or "").strip()
    if (
        understanding is None
        or understanding.dialogueAct != "continue_goal"
        or understanding.goalRelation != "active"
        or understanding.ambiguity != "none"
        or understanding.resolvedInputs
        or understanding.resolvedReferences
        or understanding.referenceCandidates
        or not question.get("question_id")
        or not prompt
        or not goal
        or goal not in understanding.intentKinds
    ):
        return None
    return ProviderReply(
        text=prompt,
        response_mode="clarify",
        turn_understanding=understanding,
    )


def _protected_edit_field(context: PlanningContext, understanding) -> str | None:
    """Return a semantically resolved secure-field edit for the active review."""

    pending = context.pending_interaction or {}
    if pending.get("kind") != "protected_confirmation" or understanding is None:
        return None
    workflow_kind = str(pending.get("workflow_kind") or "")
    if understanding.dialogueAct != "modify_goal":
        return None
    return CapabilityRegistry.secure_edit_field(
        workflow_kind,
        [item.field for item in understanding.requestedInputChanges],
    )


def _compiled_conversation_control(
    context: PlanningContext,
    understanding,
) -> tuple[ToolCall, Any | None] | None:
    """Compile an unambiguous goal-control act before business planning.

    Resume and cancel change application-owned workflow state; they are not alternative business
    operations for the planner to rediscover.  The semantic resolver still owns interpretation of
    the customer's language.  Once it has validated one of these acts against the active/paused
    goal, this boundary emits the corresponding control operation and, for a compound turn,
    returns a projected understanding containing only the outcomes that remain to be planned.
    """

    if understanding is None or understanding.ambiguity != "none":
        return None
    state = context.workflow_state
    control_name: str | None = None
    owned_intent = ""
    if understanding.dialogueAct == "resume_goal" and understanding.goalRelation == "paused":
        paused = state.get("pausedWorkflow")
        if not isinstance(paused, dict):
            return None
        owned_intent = CapabilityRegistry.goal_intent_for_active_workflow(
            str(paused.get("activeWorkflow") or "")
        )
        control_name = "resume_paused_capability"
    elif understanding.dialogueAct == "cancel_goal" and understanding.goalRelation == "active":
        active = str(state.get("activeWorkflow") or "")
        if not CapabilityRegistry.is_transactional_workflow(active):
            return None
        owned_intent = CapabilityRegistry.goal_intent_for_active_workflow(active)
        control_name = "cancel_active_capability"
    if control_name is None:
        return None

    declared = list(understanding.intentKinds)
    consumed_intent = owned_intent if owned_intent in declared else "capability"
    if consumed_intent not in declared:
        return None
    remaining = [intent for intent in declared if intent != consumed_intent]
    projected = None
    if remaining:
        projected = understanding.model_copy(
            update={
                "intentKinds": remaining,
                "intentStructure": (
                    "compound_outcomes" if len(remaining) > 1 else "single_outcome"
                ),
            }
        )
    return (
        ToolCall(
            id="semantic-control-1",
            name=control_name,
            arguments={},
            intent_kind="capability",
        ),
        projected,
    )


class HostedLlmProvider:
    """Provider-neutral Responses adapter with schema and policy enforcement."""

    def __init__(
        self,
        provider_url: str,
        api_key: str,
        model: str,
        catalogue: UnifiedToolCatalog,
        retriever: CandidateRetriever | None = None,
        client: httpx.AsyncClient | None = None,
        request_timeout_seconds: float = 44.0,
        semantic_resolution: bool = True,
    ):
        self.model = _required_value("model", model)
        self._api_key = _required_value("API key", api_key)
        self.catalogue = catalogue
        self.affordance_evaluator = PlannerAffordanceEvaluator(catalogue)
        self.retriever = retriever or FullCatalogRetriever(catalogue)
        self.semantic_resolution = semantic_resolution
        if request_timeout_seconds <= 0:
            raise ValueError("request timeout must be positive")
        self._client = client or httpx.AsyncClient(
            base_url=_provider_base_url(provider_url),
            timeout=httpx.Timeout(request_timeout_seconds, connect=min(5, request_timeout_seconds)),
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def generate_turn(self, messages: SemanticMessages) -> ProviderReply:
        turn_started = perf_counter()
        context = _planning_context(messages)
        if context.trusted_tool_facts:
            draft = await self._compose_with_recovery(messages, context)
            return ProviderReply(
                text="",
                response_draft=draft,
            )
        understanding = context.turn_understanding
        resolved_in_this_call = False
        if self.semantic_resolution and understanding is None:
            understanding = await self._resolve_with_recovery(context)
            resolved_in_this_call = True
            context = replace(context, turn_understanding=understanding)
            messages.planning_context = context
            logger.info(
                "hosted turn semantics resolved",
                extra={
                    "context": {
                        "dialogue_act": understanding.dialogueAct,
                        "goal_relation": understanding.goalRelation,
                        "intent_kinds": list(understanding.intentKinds),
                        "ambiguity": understanding.ambiguity,
                        "resolved_references": list(understanding.resolvedReferences),
                        "resolved_input_fields": [
                            item.field for item in understanding.resolvedInputs
                        ],
                        "open_question_goal": str(
                            (context.pending_interaction or {}).get("goal_intent") or ""
                        ),
                    }
                },
            )
        if _protected_edit_field(context, understanding) is not None:
            return ProviderReply(text="", turn_understanding=understanding)
        original_understanding = understanding
        compiled_control = _compiled_conversation_control(context, understanding)
        control_call: ToolCall | None = None
        if compiled_control is not None:
            control_call, remaining_understanding = compiled_control
            if remaining_understanding is None:
                return ProviderReply(
                    text="",
                    tool_calls=[control_call],
                    turn_understanding=original_understanding,
                )
            understanding = remaining_understanding
            context = replace(context, turn_understanding=understanding)
            messages.planning_context = context
        if resolved_in_this_call:
            messages.append(
                {
                    "role": "developer",
                    "content": (
                        "Structurally validated conversational understanding for this turn: "
                        + understanding.model_dump_json(exclude_none=True)
                        + (
                            ". The application has already compiled the explicit workflow "
                            "control transition; plan only these remaining outcomes"
                            if control_call is not None
                            else ". Plan the business operation that fulfils this meaning"
                        )
                        + ". Do not reinterpret the raw message into a different goal or entity."
                    ),
                }
            )
        continued_question = _continued_open_question_reply(context, understanding)
        if continued_question is not None:
            return continued_question
        retrieval_started = perf_counter()
        candidates = _workflow_candidates(
            _retrieve_candidates(self.retriever, context),
            context,
            self.catalogue,
        )
        candidates = _semantic_candidates(candidates, context, self.catalogue)
        resolved_knowledge = _resolved_knowledge_reply(candidates, understanding, context)
        if resolved_knowledge is not None:
            return replace(resolved_knowledge, turn_understanding=original_understanding)
        affordances = self.affordance_evaluator.evaluate(candidates, context)
        candidates = affordances.candidates
        logger.info(
            "planner affordances evaluated",
            extra={
                "context": {
                    "executable_tools": sorted(affordances.executable_tool_ids),
                    "blocked_tools": sorted(affordances.blocked_tool_ids),
                    "generic_clarification": affordances.allow_generic_clarification,
                }
            },
        )
        retrieval_ms = _elapsed_ms(retrieval_started)
        planner_started = perf_counter()
        try:
            candidate = await self._plan_with_recovery(
                messages,
                candidates,
                context,
                allow_generic_clarification=affordances.allow_generic_clarification,
            )
        except PlanningValidationError:
            compiled = _unique_affordance_fallback(affordances, context)
            if compiled is None:
                raise
            logger.warning(
                "hosted planner exhausted repair; executing unique semantic affordance",
                extra={
                    "context": {
                        "intent_kinds": list(understanding.intentKinds),
                        "tools": [call.name for call in compiled.tool_calls],
                    }
                },
            )
            reply = replace(compiled, turn_understanding=original_understanding)
            if control_call is not None:
                reply = replace(reply, tool_calls=[control_call, *reply.tool_calls])
                messages.planning_context = replace(
                    messages.planning_context,
                    turn_understanding=original_understanding,
                )
            return reply
        planner_ms = _elapsed_ms(planner_started)
        logger.info(
            "hosted turn proposal validated",
            extra={
                "context": {
                    "retrieval_ms": retrieval_ms,
                    "planner_ms": planner_ms,
                    "total_ms": _elapsed_ms(turn_started),
                }
            },
        )
        reply = _provider_reply(
            candidate,
            candidates,
            customer_reason=context.latest_customer_message,
        )
        if control_call is not None:
            if not reply.tool_calls:
                raise PlanningValidationError(
                    "compound control turn omitted a resolved remaining outcome"
                )
            reply = replace(reply, tool_calls=[control_call, *reply.tool_calls])
            messages.planning_context = replace(
                messages.planning_context,
                turn_understanding=original_understanding,
            )
        return replace(reply, turn_understanding=original_understanding)

    async def _resolve_with_recovery(
        self,
        context: PlanningContext,
        *,
        outcome_disambiguation: bool = False,
    ):
        """Resolve conversational meaning before retrieving or selecting business tools."""

        last_error: Exception | None = None
        feedback: str | None = None
        input_fields = input_resolution_contracts(self.catalogue)
        input_catalogue = input_resolution_catalogue(self.catalogue)
        input_fields.setdefault("fullName", {"type": "string", "minLength": 1, "maxLength": 200})
        input_catalogue.append(
            {
                "field": "fullName",
                "descriptions": ["The customer's complete first and last name."],
                "allowedValues": [],
                "valueSchema": input_fields["fullName"],
            }
        )
        intent_catalogue = intent_resolution_catalogue(self.catalogue)
        knowledge_catalogue = [
            {"id": f"knowledge:{item.id}", "title": item.title, "text": item.text}
            for item in self.retriever.retrieve(
                context.latest_customer_message,
                tool_limit=0,
                knowledge_limit=5,
            ).knowledge
            if item.audience == "customer"
        ]
        trusted_knowledge_context_ids = {item["id"] for item in knowledge_catalogue}
        for attempt in range(1, RESOLUTION_ATTEMPTS + 1):
            try:
                response = await self._post_planner_request(
                    resolution_request(
                        model=self.model,
                        context=context,
                        input_fields=input_fields,
                        input_catalogue=input_catalogue,
                        intent_catalogue=intent_catalogue,
                        knowledge_catalogue=knowledge_catalogue,
                        feedback=feedback,
                        outcome_disambiguation=outcome_disambiguation,
                    )
                )
                calls = _function_arguments(response.json(), expected_names={RESOLVE_TURN_TOOL})
                if len(calls) != 1:
                    raise ValueError("semantic resolution must return exactly one result")
                understanding = validate_turn_understanding(
                    calls[0][2],
                    context,
                    input_fields,
                    trusted_supporting_context_ids=trusted_knowledge_context_ids,
                )
                if outcome_disambiguation or not requires_explicit_outcome_disambiguation(
                    understanding,
                    context,
                ):
                    return understanding
                disambiguated = await self._resolve_with_recovery(
                    context,
                    outcome_disambiguation=True,
                )
                goal = str((context.pending_interaction or {}).get("goal_intent") or "")
                confirmed = _outcome_disambiguation_confirms_selection(
                    understanding,
                    disambiguated,
                    goal,
                )
                logger.info(
                    "hosted finite choice outcome independently disambiguated",
                    extra={
                        "context": {
                            "confirmed_answer": confirmed,
                            "initial_intents": list(understanding.intentKinds),
                            "disambiguated_intents": list(disambiguated.intentKinds),
                            "disambiguated_dialogue_act": disambiguated.dialogueAct,
                            "disambiguated_ambiguity": disambiguated.ambiguity,
                        }
                    },
                )
                return understanding if confirmed else disambiguated
            except httpx.TimeoutException as error:
                raise TimeoutError("hosted semantic resolution timed out") from error
            except (TypeError, ValueError, KeyError) as error:
                last_error = error
                feedback = _safe_semantic_failure(error)
                logger.warning(
                    "hosted semantic resolution failed validation",
                    extra={
                        "context": {
                            "attempt": attempt,
                            "retrying": attempt < RESOLUTION_ATTEMPTS,
                            "error_type": type(error).__name__,
                            "reason": feedback,
                        }
                    },
                )
        assert last_error is not None
        raise PlanningValidationError(feedback or "semantic_resolution_invalid") from last_error

    async def _compose_with_recovery(
        self,
        messages: SemanticMessages,
        context: PlanningContext,
    ) -> GroundedResponseDraft:
        last_error: Exception | None = None
        feedback: str | None = None
        for attempt in range(1, COMPOSE_ATTEMPTS + 1):
            try:
                response = await self._client.post(
                    "/responses",
                    headers=self._headers(),
                    json=self._composition_request_payload(messages, context, feedback),
                )
                response.raise_for_status()
                calls = _function_arguments(response.json(), expected_names={COMPOSE_RESPONSE_TOOL})
                if len(calls) != 1:
                    raise ValueError("composition must return exactly one grounded response")
                draft = GroundedResponseDraft.model_validate(calls[0][2])
                return draft
            except httpx.TimeoutException as error:
                raise TimeoutError("hosted composition request timed out") from error
            except (httpx.HTTPError, TypeError, ValueError, KeyError) as error:
                last_error = error
                feedback = _safe_composition_failure(error)
                logger.warning(
                    "hosted grounded composition failed validation",
                    extra={"context": {"attempt": attempt, "error_type": feedback}},
                )
        assert last_error is not None
        raise last_error

    def _composition_request_payload(
        self,
        messages: SemanticMessages,
        context: PlanningContext,
        feedback: str | None,
    ) -> dict[str, Any]:
        trusted = context.trusted_tool_facts or {"results": []}
        envelope = {
            "latestCustomerMessage": context.latest_customer_message,
            "turnUnderstanding": (
                context.turn_understanding.model_dump(mode="json")
                if context.turn_understanding is not None
                else None
            ),
            "openQuestion": context.pending_interaction,
            "applicationContinuation": trusted.get("applicationContinuation"),
            "trustedToolResults": trusted.get("results", []),
            "activeWorkflow": context.workflow_state,
            "secureInputActivation": bool(trusted.get("secureInputActivation")),
            "selectionPromptActivation": bool(trusted.get("selectionPromptActivation")),
            "collectionPresentationActivation": bool(
                trusted.get("collectionPresentationActivation")
            ),
            "navigationConfirmationActivation": bool(
                trusted.get("navigationConfirmationActivation")
            ),
            "groundingValidationFailure": trusted.get("groundingValidationFailure"),
            "groundingValidationFailures": trusted.get("groundingValidationFailures", []),
        }
        if feedback:
            envelope["previousValidationFailure"] = feedback
        instructions = COMPOSER_SYSTEM_POLICY
        return {
            "model": self.model,
            "instructions": instructions,
            "input": [
                {
                    "role": "user",
                    "content": json.dumps(envelope, separators=(",", ":")),
                }
            ],
            "tools": [
                {
                    "type": "function",
                    "name": COMPOSE_RESPONSE_TOOL,
                    "description": "Return the grounded conversational response.",
                    "parameters": _strict_function_schema(
                        GroundedResponseDraft.model_json_schema()
                    ),
                    "strict": True,
                }
            ],
            "tool_choice": "required",
            "parallel_tool_calls": False,
            "max_output_tokens": 1_800,
            "store": False,
        }

    async def _plan_with_recovery(
        self,
        messages: SemanticMessages,
        candidates: CandidateSet,
        context: PlanningContext,
        *,
        allow_generic_clarification: bool = True,
    ) -> TurnProposal:
        last_error: Exception | None = None
        feedback: str | None = None
        compound_seed: TurnProposal | None = None
        for attempt in range(1, PLANNER_ATTEMPTS + 1):
            request_started = perf_counter()
            try:
                request_candidates = (
                    _remaining_compound_candidates(candidates, compound_seed, context)
                    if compound_seed is not None
                    else candidates
                )
                response = await self._post_planner_request(
                    self._planner_request_payload(
                        messages,
                        request_candidates,
                        context,
                        feedback=feedback,
                        allow_generic_clarification=allow_generic_clarification,
                    )
                )
                candidate = _parse_proposal(
                    response.json(),
                    request_candidates,
                    self.catalogue,
                    context.pending_interaction,
                    _trusted_vehicle_ids(context),
                    _trusted_entity_references(context),
                    _authoritative_intent_candidates(context),
                    _authoritative_reference_candidates(context),
                    _authoritative_reference_intents(context),
                    allow_generic_clarification=allow_generic_clarification,
                )
                candidate = _bind_resolved_reference_arguments(
                    candidate,
                    context,
                    self.catalogue,
                )
                _validate_plan_against_understanding(candidate, context, self.catalogue)
                if compound_seed is not None:
                    _validate_compound_repair(compound_seed, candidate)
                    return _merge_compound_proposals(compound_seed, candidate)
                if attempt < PLANNER_ATTEMPTS and _needs_compound_plan_recheck(context, candidate):
                    planned = ",".join(call.name for call in candidate.tool_calls) or "none"
                    compound_seed = candidate
                    feedback = (
                        "compound_request_incomplete; the prior proposal planned only "
                        f"[{planned}], which the application has retained. Plan only the other "
                        "coordinated customer clause or clauses that remain unfulfilled; do not "
                        "repeat the retained operation."
                    )
                    logger.info(
                        "hosted planner compound proposal requires one completeness recheck",
                        extra={"context": {"attempt": attempt}},
                    )
                    continue
                return candidate
            except httpx.TimeoutException as error:
                logger.warning(
                    "hosted planner request timed out",
                    extra={
                        "context": {
                            "stage": "planner",
                            "attempt": attempt,
                            "duration_ms": _elapsed_ms(request_started),
                            "candidate_tools": [definition.id for definition in candidates.tools],
                            "candidate_knowledge_count": len(candidates.knowledge),
                        }
                    },
                )
                raise TimeoutError("hosted planner request timed out") from error
            except (TypeError, ValueError, KeyError) as error:
                last_error = error
                retryable = _retryable_planner_error(error)
                feedback = _safe_planner_failure(error)
                logger.warning(
                    "hosted planner response failed validation",
                    extra={
                        "context": {
                            "attempt": attempt,
                            "retrying": retryable and attempt < PLANNER_ATTEMPTS,
                            "error_type": type(error).__name__,
                            "reason": feedback,
                        }
                    },
                )
                if not retryable or attempt == PLANNER_ATTEMPTS:
                    raise PlanningValidationError(feedback) from error
        assert last_error is not None
        raise last_error

    async def _post_planner_request(self, payload: dict[str, Any]) -> httpx.Response:
        """Retry provider transport independently from semantic plan repair.

        HTTP and connection failures never consume a schema-repair attempt and can never be
        reclassified as an invalid customer intent or planning error.
        """

        for attempt in range(1, PROVIDER_TRANSPORT_ATTEMPTS + 1):
            try:
                response = await self._client.post(
                    "/responses", headers=self._headers(), json=payload
                )
                response.raise_for_status()
                return response
            except httpx.TimeoutException:
                raise
            except httpx.HTTPError as error:
                retryable = _retryable_transport_error(error)
                logger.warning(
                    "hosted planner transport failed",
                    extra={
                        "context": {
                            "attempt": attempt,
                            "retrying": retryable and attempt < PROVIDER_TRANSPORT_ATTEMPTS,
                            "error_type": type(error).__name__,
                            "status_code": (
                                error.response.status_code
                                if isinstance(error, httpx.HTTPStatusError)
                                else None
                            ),
                        }
                    },
                )
                if not retryable or attempt == PROVIDER_TRANSPORT_ATTEMPTS:
                    raise ProviderUnavailableError(
                        "hosted planner transport unavailable"
                    ) from error
                await asyncio.sleep(0.1 * attempt)
        raise ProviderUnavailableError("hosted planner transport unavailable")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _planner_request_payload(
        self,
        messages: list[dict[str, Any]],
        candidates: CandidateSet,
        context: PlanningContext,
        feedback: str | None = None,
        allow_generic_clarification: bool | None = None,
    ) -> dict[str, Any]:
        customer_knowledge = _customer_knowledge(candidates)
        vehicle_navigation = _vehicle_navigation_tool_definition(_trusted_vehicle_ids(context))
        planner_input = response_input(messages)
        if feedback:
            planner_input.append(
                {
                    "role": "developer",
                    "content": (
                        "The previous proposal violated a deterministic conversation contract. "
                        f"Re-plan the same customer request. Failure: {feedback}."
                    ),
                }
            )
        clarification = (
            _clarification_tool_definition(candidates)
            if allow_generic_clarification is not False
            else None
        )
        authoritative_intents = _authoritative_intent_candidates(context)
        authoritative_references = _authoritative_reference_candidates(context)
        authoritative_reference_intents = _authoritative_reference_intents(context)
        intent_clarification = _intent_clarification_tool_definition(candidates)
        if authoritative_intents:
            intent_clarification = _intent_clarification_tool_definition(
                candidates,
                authoritative_intents,
            )
        reference_clarification = _reference_clarification_tool_definition(
            _trusted_entity_references(context),
            set(_semantic_intent_candidates(candidates)),
            authoritative_references,
            authoritative_reference_intents,
        )
        if authoritative_intents:
            assert intent_clarification is not None
            tools = [intent_clarification]
            tool_choice: str | dict[str, str] = {
                "type": "function",
                "name": intent_clarification["name"],
            }
        elif authoritative_references:
            assert reference_clarification is not None
            tools = [reference_clarification]
            tool_choice = {
                "type": "function",
                "name": reference_clarification["name"],
            }
        else:
            tools = [
                *(_intent_declared_tool(definition) for definition in candidates.tools),
                *(
                    [_knowledge_answer_tool_definition(customer_knowledge)]
                    if customer_knowledge
                    else []
                ),
                _social_response_tool_definition(),
                *([clarification] if clarification else []),
                *([intent_clarification] if intent_clarification else []),
                *([reference_clarification] if reference_clarification else []),
                *([vehicle_navigation] if vehicle_navigation else []),
                *(_interaction_decision_tool_definitions(context.pending_interaction)),
            ]
            tool_choice = "required"
        return {
            "model": self.model,
            "instructions": planner_instructions(candidates),
            "input": planner_input,
            "tools": tools,
            "tool_choice": tool_choice,
            "parallel_tool_calls": True,
            "max_output_tokens": 1_200,
            "store": False,
        }


def _required_value(label: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized


def _authoritative_intent_candidates(context: PlanningContext) -> tuple[str, ...]:
    """Return ambiguity alternatives already validated by semantic resolution.

    Planning may phrase the question, but it must not reproduce or alter the alternatives. The
    application compiles them into the persisted interaction just as it compiles resolved inputs
    into business-tool calls.
    """

    understanding = context.turn_understanding
    if understanding is None or understanding.ambiguity != "intent":
        return ()
    return tuple(understanding.intentKinds)


def _authoritative_reference_candidates(context: PlanningContext) -> tuple[str, ...]:
    """Return the complete entity ambiguity already validated by turn resolution."""

    understanding = context.turn_understanding
    if (
        understanding is None
        or understanding.ambiguity != "reference"
        or len(understanding.intentKinds) != 1
    ):
        return ()
    return tuple(understanding.referenceCandidates)


def _authoritative_reference_intents(context: PlanningContext) -> tuple[str, ...]:
    """Return the single goal that owns an authoritative reference clarification."""

    return (
        tuple(context.turn_understanding.intentKinds)
        if _authoritative_reference_candidates(context) and context.turn_understanding is not None
        else ()
    )


def _needs_compound_plan_recheck(context: PlanningContext, proposal: TurnProposal) -> bool:
    """Ask for one completeness repair when the typed semantic agenda is under-planned."""

    understanding = context.turn_understanding
    if understanding is None or understanding.intentStructure != "compound_outcomes":
        return False
    fulfilments = len(_fulfilled_intents(proposal)) + bool(proposal.response)
    return fulfilments < len(understanding.intentKinds)


def _remaining_compound_candidates(
    candidates: CandidateSet,
    seed: TurnProposal,
    context: PlanningContext,
) -> CandidateSet:
    understanding = context.turn_understanding
    required_intents = set(understanding.intentKinds if understanding is not None else ())
    remaining_intents = required_intents - _fulfilled_intents(seed)
    remaining = tuple(
        tool for tool in candidates.tools if supported_intents(tool.id) & remaining_intents
    )
    return CandidateSet(remaining, candidates.knowledge)


def _fulfilled_intents(proposal: TurnProposal) -> set[str]:
    """Return semantic obligations fulfilled by executable calls in a proposal."""

    return {call.intent_kind for call in proposal.tool_calls if call.intent_kind}


def _validate_compound_repair(seed: TurnProposal, repair: TurnProposal) -> None:
    """A completeness repair may fill only obligations absent from the retained proposal."""

    if _fulfilled_intents(seed) & _fulfilled_intents(repair):
        raise ValueError("compound repair repeated a retained intent")


def _merge_compound_proposals(first: TurnProposal, second: TurnProposal) -> TurnProposal:
    calls: list[ToolCall] = []
    seen: set[tuple[str, str]] = set()
    for call in (*first.tool_calls, *second.tool_calls):
        identity = (call.name, json.dumps(call.arguments, sort_keys=True, separators=(",", ":")))
        if identity in seen:
            continue
        seen.add(identity)
        calls.append(
            ToolCall(
                id=f"compound-call-{len(calls) + 1}",
                name=call.name,
                arguments=call.arguments,
                intent_id=f"intent-{len(calls) + 1}",
                intent_kind=call.intent_kind or min(supported_intents(call.name)),
            )
        )
    if len(calls) > 4:
        raise ValueError("compound plan exceeds the four-intent execution boundary")
    response = first.response or second.response
    return TurnProposal(calls, response)


def _intent_declared_tool(definition) -> dict[str, Any]:
    """Require intent only when one tool can represent several customer intents.

    A single-intent tool already declares its meaning through the application catalogue. Asking
    the model to repeat that same value creates a redundant schema-failure point without adding a
    safety boundary, so the parser derives it application-side.
    """

    tool = deepcopy(definition.provider_tool())
    compatible_intents = supported_intents(definition.id)
    if len(compatible_intents) == 1:
        return tool
    parameters = tool["parameters"]
    properties = parameters.setdefault("properties", {})
    properties["intentKind"] = {
        "type": "string",
        "enum": sorted(compatible_intents),
        "description": "The customer intent this tool call is intended to fulfil.",
    }
    required = list(parameters.get("required") or [])
    if "intentKind" not in required:
        required.append("intentKind")
    parameters["required"] = required
    return tool


def _elapsed_ms(started: float) -> int:
    return round((perf_counter() - started) * 1_000)


def _retryable_planner_error(error: Exception) -> bool:
    return isinstance(error, (TypeError, ValueError, KeyError))


def _safe_semantic_failure(error: Exception) -> str:
    """Return bounded repair feedback without echoing customer or provider content."""

    if isinstance(error, ValidationError):
        details = []
        for item in error.errors(include_input=False, include_url=False)[:4]:
            location = ".".join(str(part) for part in item.get("loc") or ()) or "resolution"
            error_type = str(item.get("type") or "invalid")
            message = str(item.get("msg") or "invalid value")
            details.append(f"{location} [{error_type}]: {message}")
        return "semantic_resolution_schema_invalid: " + "; ".join(details)[:600]
    message = str(error)
    known = {
        "semantic resolution must return exactly one result",
        "answer_open_question requires exactly one answeredQuestionId",
        "an ambiguous interpretation cannot have high confidence",
        "intentKinds must be unique",
        "single intent structure permits at most one intent",
        "compound intent structure requires multiple requested outcomes",
        "intent alternatives require multiple ambiguous interpretations",
        "intent ambiguity must be represented as alternatives",
        "intent alternatives require intent ambiguity",
        "resolvedReferences must be unique",
        "referenceCandidates must be unique",
        "resolved references and ambiguous candidates must be disjoint",
        "reference ambiguity requires multiple candidates",
        "reference candidates require reference ambiguity",
        "resolvedInputs fields must be unique",
        "requested input changes must be unique",
        "supportingContextIds must be unique",
        "turn resolution contains an untrusted entity reference",
        "turn resolution cites unknown context evidence",
        "knowledge evidence can only resolve one factual information outcome",
        "a factual answer can cite at most three knowledge records",
        "turn resolution contains an unknown input field",
        "turn resolution answered a question that is not open",
        "turn resolution answered a question when none is open",
        "turn resolution used an entity outside the open question",
        "an unambiguous choice answer must resolve exactly one candidate",
        "an open input answer must resolve an expected field",
        "turn resolution does not continue the open question's goal",
        "turn resolution input lacks customer-authored evidence",
        "turn resolution contains an unknown requested input change",
        "requested input change lacks customer-authored evidence",
        "a supplied replacement cannot also be only a requested change",
        "resolved entity contradicts a customer-supplied descriptor",
        "entity identifiers must be resolved as trusted references",
    }
    return message if message in known else "semantic_resolution_schema_invalid"


def _retryable_transport_error(error: httpx.HTTPError) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in {408, 409, 425, 429} or (
            error.response.status_code >= 500
        )
    return isinstance(error, httpx.TransportError)


def _provider_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("LLM_PROVIDER_URL must be an absolute HTTP(S) URL")
    if parsed.query or parsed.fragment:
        raise ValueError("LLM_PROVIDER_URL must not contain a query or fragment")
    path = f"{parsed.path.rstrip('/')}/"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _trusted_vehicle_ids(context: PlanningContext) -> set[str]:
    candidates = context.displayed_vehicles or context.page_vehicles
    return {
        str(item["vehicleId"])
        for item in candidates
        if isinstance(item, dict)
        and isinstance(item.get("vehicleId"), str)
        and str(item["vehicleId"]).startswith("veh-")
    }


def _trusted_entity_references(context: PlanningContext) -> set[str]:
    references = {f"vehicle:{vehicle_id}" for vehicle_id in _trusted_vehicle_ids(context)}
    references.update(
        f"offer:{item['offerId']}"
        for item in context.displayed_offers
        if isinstance(item, dict) and item.get("offerId")
    )
    references.update(
        f"dealership:{item['dealershipId']}"
        for item in context.displayed_dealerships
        if isinstance(item, dict) and item.get("dealershipId")
    )
    references.update(
        str(item["entityReference"])
        for item in context.displayed_choices
        if isinstance(item, dict)
        and isinstance(item.get("entityReference"), str)
        and ":" in str(item["entityReference"])
    )
    entity_namespaces = {
        "vehicleId": "vehicle",
        "offerId": "offer",
        "dealershipId": "dealership",
        "serviceTypeId": "service",
        "slotId": "appointment",
    }
    entities = dict(context.workflow_state.get("entities") or {})
    references.update(
        f"{namespace}:{entities[field]}"
        for field, namespace in entity_namespaces.items()
        if entities.get(field)
    )
    return references


def _workflow_candidates(
    candidates: CandidateSet,
    context: PlanningContext,
    catalogue: UnifiedToolCatalog,
) -> CandidateSet:
    """Keep active capability tools available without making a language-routing decision."""

    state = context.workflow_state
    active = str(state.get("activeWorkflow") or "")
    # This read is the safe fallback for policy, finance, and dealership questions whose exact
    # answer is not present in a more specific live result. Keeping it available prevents a
    # supported information clause from disappearing solely because semantic retrieval ranked a
    # transactional neighbour more highly.
    required: list[str] = ["get_business_information"]
    spec = CapabilityRegistry.for_active_workflow(active)
    if spec is not None:
        required.append(spec.agent_tool)
        required.extend(resolution.tool for resolution in spec.live_field_resolvers)
        required.extend(spec.continuation_tools)
        required.append("cancel_active_capability")
    if isinstance(state.get("pausedWorkflow"), dict):
        required.append("resume_paused_capability")
    tools = []
    seen: set[str] = set()
    for name in required:
        if name in seen:
            continue
        try:
            tools.append(catalogue.get(name))
        except ValueError:
            continue
        seen.add(name)
    for definition in candidates.tools:
        if definition.id not in seen:
            tools.append(definition)
            seen.add(definition.id)
    return CandidateSet(tuple(tools[:12]), candidates.knowledge)


def _semantic_candidates(
    candidates: CandidateSet,
    context: PlanningContext,
    catalogue: UnifiedToolCatalog,
) -> CandidateSet:
    """Compile the complete legal tool set for the AI's validated intent.

    Vector retrieval helps the model discover likely meaning; it cannot decide whether a validated
    product capability exists. Once semantic resolution selects an intent, include every
    planner-visible operation that declares that intent, including each clause of a compound turn.
    """

    understanding = context.turn_understanding
    if understanding is None or understanding.ambiguity != "none" or not understanding.intentKinds:
        return candidates
    intents = set(understanding.intentKinds)
    selected = {
        definition.id: definition
        for definition in candidates.tools
        if supported_intents(definition.id) & intents
    }
    for definition in catalogue.planner_tools():
        if supported_intents(definition.id) & intents:
            selected.setdefault(definition.id, definition)
    return CandidateSet(tuple(selected.values()), candidates.knowledge)


def _resolved_knowledge_reply(
    candidates: CandidateSet,
    understanding,
    context: PlanningContext,
) -> ProviderReply | None:
    """Compile a cited static answer selected by the typed understanding phase."""

    if understanding is None:
        return None
    citation_ids = tuple(
        context_id.removeprefix("knowledge:")
        for context_id in understanding.supportingContextIds
        if context_id.startswith("knowledge:")
    )
    if not citation_ids:
        return None
    evidence = _customer_knowledge(candidates)
    response = _knowledge_response({"citationIds": list(citation_ids)}, evidence)
    return _provider_reply(
        TurnProposal(response=response),
        candidates,
        customer_reason=context.latest_customer_message,
    )


def _unique_affordance_fallback(
    affordances,
    context: PlanningContext,
) -> ProviderReply | None:
    """Compile a plan only when typed semantics leave exactly one operation per outcome.

    This is a safety net for invalid planner representations, not a language router. It consumes
    only the validated ``TurnUnderstanding``, catalogue intent declarations, executable
    affordances, and trusted references. Ambiguous, blocked, or under-specified choices remain with
    the planner/clarification path.
    """

    understanding = context.turn_understanding
    if understanding is None or understanding.ambiguity != "none" or not understanding.intentKinds:
        return None
    executable = set(affordances.executable_tool_ids)
    selected: dict[str, tuple[Any, str]] = {}
    for intent in understanding.intentKinds:
        matches = [
            definition
            for definition in affordances.candidates.tools
            if definition.id in executable and intent in supported_intents(definition.id)
        ]
        if len(matches) != 1:
            return None
        selected.setdefault(matches[0].id, (matches[0], intent))
    calls = [
        ToolCall(
            id=f"semantic-affordance-{index}",
            name=definition.id,
            arguments=trusted_reference_arguments(
                definition.provider_input_schema,
                definition.reference_inputs,
                context,
            ),
            intent_kind=intent,
        )
        for index, (definition, intent) in enumerate(selected.values(), start=1)
    ]
    return ProviderReply(text="", tool_calls=calls)


def _bind_resolved_reference_arguments(
    proposal: TurnProposal,
    context: PlanningContext,
    catalogue: UnifiedToolCatalog,
) -> TurnProposal:
    """Compile semantic entity selections into business calls.

    Entity identity is decided by the validated semantic-resolution boundary, not by the
    generative business planner.  The planner still chooses the appropriate operation and its
    non-identity filters, but a single trusted reference is projected into every compatible call
    before plan validation.  This prevents spelling, label, or casing variants such as ``MOT``
    from replacing the canonical ``service:mot`` selection, and applies equally to every declared
    reference namespace.
    """

    if context.turn_understanding is None or not proposal.tool_calls:
        return proposal
    calls: list[ToolCall] = []
    changed = False
    for call in proposal.tool_calls:
        definition = catalogue.get(call.name)
        trusted = trusted_reference_arguments(
            definition.provider_input_schema,
            definition.reference_inputs,
            context,
        )
        if not trusted:
            calls.append(call)
            continue
        arguments = {**call.arguments, **trusted}
        changed = changed or arguments != call.arguments
        calls.append(replace(call, arguments=arguments))
    return replace(proposal, tool_calls=calls) if changed else proposal


def _validate_plan_against_understanding(
    proposal: TurnProposal,
    context: PlanningContext,
    catalogue: UnifiedToolCatalog,
) -> None:
    """Keep business planning faithful to the preceding semantic decision."""

    understanding = context.turn_understanding
    if understanding is None:
        return
    if understanding.ambiguity != "none":
        if proposal.tool_calls:
            raise ValueError("ambiguous semantic resolution cannot execute a business tool")
        if proposal.response is None or proposal.response.mode != "clarify":
            raise ValueError("ambiguous semantic resolution requires clarification")
        if understanding.ambiguity == "reference" and set(
            proposal.response.candidate_references
        ) != set(understanding.referenceCandidates):
            raise ValueError("reference clarification conflicts with semantic candidates")
        if understanding.ambiguity == "intent" and set(proposal.response.candidate_intents) != set(
            understanding.intentKinds
        ):
            raise ValueError("intent clarification conflicts with semantic candidates")
        return
    if understanding.dialogueAct in {"accept", "decline"}:
        if proposal.interaction_decision != understanding.dialogueAct:
            raise ValueError("interaction decision conflicts with semantic resolution")
        return
    if proposal.interaction_decision is not None:
        raise ValueError("interaction decision was not resolved from the customer turn")
    if understanding.dialogueAct == "social":
        if proposal.response is None or proposal.response.mode != "conversation":
            raise ValueError("social turn requires the social response capability")
        return
    if proposal.response is not None and proposal.response.mode == "conversation":
        raise ValueError("business turn cannot be replaced with a social response")
    if understanding.dialogueAct == "interrupt_with_information_request" and any(
        catalogue.get(call.name).result_mode == "workflow" for call in proposal.tool_calls
    ):
        raise ValueError("information request cannot start a workflow")
    declared = set(understanding.intentKinds)
    for call in proposal.tool_calls:
        if call.intent_kind not in declared:
            raise ValueError("business plan conflicts with resolved customer intent")
    resolved = set(understanding.resolvedReferences)
    if not resolved:
        return
    planned_references = {
        f"{namespace}:{call.arguments[field]}"
        for call in proposal.tool_calls
        for field, namespace in reference_input_bindings(catalogue.get(call.name))
        if call.arguments.get(field) and not isinstance(call.arguments[field], list)
    }
    planned_references.update(
        f"{namespace}:{value}"
        for call in proposal.tool_calls
        for field, namespace in reference_input_bindings(catalogue.get(call.name))
        for value in (
            call.arguments.get(field) if isinstance(call.arguments.get(field), list) else ()
        )
        if isinstance(value, str) and value
    )
    resolved_namespaces = {item.split(":", 1)[0] for item in resolved}
    conflicting = {
        reference
        for reference in planned_references
        if reference.split(":", 1)[0] in resolved_namespaces and reference not in resolved
    }
    if conflicting:
        raise ValueError("business plan conflicts with resolved entity references")
    pending_question_kind = str((context.pending_interaction or {}).get("kind") or "")
    if (
        understanding.dialogueAct == "answer_open_question"
        and pending_question_kind == "reference_choice"
        and not _binds_answered_reference_choice(
            proposal.tool_calls,
            catalogue,
            context,
        )
    ):
        raise ValueError("business plan omitted the resolved answer reference")


def _safe_composition_failure(error: Exception) -> str:
    """Give the composer a bounded actionable schema-repair reason without echoing data."""

    message = str(error).casefold()
    categories = (
        (
            "finance total requires complete trusted terms",
            "state_that_the_total_is_unavailable_and_name_the_missing_finance_terms",
        ),
        ("extra inputs", "remove_unsupported_response_fields"),
        ("field required", "supply_every_required_response_field"),
        ("less than or equal", "respect_response_collection_limits"),
        ("string too long", "shorten_response_content"),
    )
    return next(
        (code for marker, code in categories if marker in message),
        "return_a_valid_grounded_response_schema",
    )


def _safe_planner_failure(error: Exception) -> str:
    """Return a bounded repair code instead of reflecting provider output."""

    if isinstance(error, ValidationError):
        details = []
        for item in error.errors(include_input=False, include_url=False)[:4]:
            location = ".".join(str(part) for part in item.get("loc") or ()) or "proposal"
            error_type = str(item.get("type") or "invalid")
            message = str(item.get("msg") or "invalid value")
            details.append(f"{location} [{error_type}]: {message}")
        return "planner_schema_invalid: " + "; ".join(details)[:600]
    message = str(error).casefold()
    categories = (
        (
            "missing or incompatible intent declaration",
            "use_the_tool_intent_allowed_by_its_schema",
        ),
        (
            "choose exactly one response branch",
            "choose_exactly_one_of_execute_clarify_or_respond",
        ),
        ("more than one customer response", "return_only_one_customer_response"),
        (
            "business plan conflicts with resolved customer intent",
            "use_only_the_intent_declared_by_turn_understanding",
        ),
        (
            "business plan conflicts with resolved entity references",
            "use_only_selected_or_persisted_entity_references",
        ),
        (
            "business plan omitted the resolved answer reference",
            "copy_the_selected_reference_into_the_business_call",
        ),
        (
            "semantic ambiguity",
            "clarify_only_between_two_or_more_retrieved_candidate_intents",
        ),
        (
            "intent clarification",
            "clarify_only_between_two_or_more_retrieved_candidate_intents",
        ),
        (
            "customer response exposes implementation context",
            "keep_page_and_application_context_internal",
        ),
        (
            "clarification",
            "clarification_requires_a_real_required_field_or_declared_precondition",
        ),
    )
    return next(
        (code for marker, code in categories if marker in message),
        "return_a_valid_typed_customer_turn_proposal",
    )


def _strict_function_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return the strict-function subset required by Responses providers.

    Pydantic correctly represents application defaults by omitting those fields
    from ``required``.  Strict function tools use a narrower contract: every
    declared object property must be required and defaults are unsupported.
    The public application model may retain its ergonomic defaults; only the
    provider-facing copy is normalised here.
    """
    normalized = deepcopy(schema)

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            value.pop("default", None)
            # Pydantic's discriminated unions use ``oneOf`` plus the optional
            # ``discriminator`` annotation. Responses strict function schemas
            # accept the equivalent JSON Schema ``anyOf`` form.
            if "oneOf" in value:
                value["anyOf"] = value.pop("oneOf")
            value.pop("discriminator", None)
            properties = value.get("properties")
            if value.get("type") == "object" and isinstance(properties, dict):
                value["required"] = list(properties)
                value["additionalProperties"] = False
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(normalized)
    return normalized
