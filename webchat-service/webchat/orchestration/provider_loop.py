"""Bounded provider and concrete-tool execution loop."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from webchat.domain.interactions import composition_continuation
from webchat.integrations.contracts import LlmProvider, ProviderReply, SemanticMessages, ToolCall
from webchat.integrations.dealership import DealershipError
from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.contracts.semantics import TurnUnderstanding
from webchat.orchestration.fact_normalization import FactNormalizer
from webchat.orchestration.policy import PolicyClarification, ToolPolicyGate
from webchat.orchestration.presentation.registry import RendererRegistry
from webchat.orchestration.state import WorkflowStateReducer
from webchat.orchestration.tools.actions import bind_pending_action_handoff
from webchat.orchestration.tools.result import ToolResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnReferences:
    displayed_vehicles: list[dict[str, object]]
    vehicle_search_state: dict[str, object] | None
    displayed_offers: list[dict[str, object]]
    page_vehicles: list[dict[str, object]]
    displayed_dealerships: list[dict[str, object]] = field(default_factory=list)
    displayed_choices: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class ProviderLoopResult:
    reply: ProviderReply | None = None
    terminal_result: Any | None = None
    terminal_results: tuple[Any, ...] = ()
    normalized_results: tuple[Any, ...] = ()
    workflow_state: dict[str, Any] = field(default_factory=dict)
    turn_understanding: TurnUnderstanding | None = None


class ProviderToolLoop:
    """Validate AI proposals, execute real tools, and expose only trusted results."""

    def __init__(
        self,
        provider: LlmProvider,
        tools: UnifiedToolCatalog | None,
        state_reducer: WorkflowStateReducer,
        timeout_seconds: float,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.state_reducer = state_reducer
        self.timeout_seconds = timeout_seconds
        self.policy = ToolPolicyGate(tools) if tools is not None else None
        self.renderers = RendererRegistry()
        self.normalizer = FactNormalizer()

    async def run(
        self,
        history: SemanticMessages,
        workflow_state: dict[str, Any],
        conversation_id: str,
        references: TurnReferences,
    ) -> ProviderLoopResult:
        last_tool_result = None
        normalized_results = []
        terminal_results = []
        turn_understanding = None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_seconds

        def remaining_budget() -> float:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("provider turn budget exhausted")
            return remaining

        for _ in range(4):
            try:
                reply = await asyncio.wait_for(
                    self.provider.generate_turn(history), timeout=remaining_budget()
                )
            except Exception as error:
                if not terminal_results:
                    raise
                # A trusted, renderable tool result already exists. AI composition is an
                # optional presentation layer and must not turn that result into a failed turn.
                logger.exception(
                    "AI composition failed after successful tool execution: %s",
                    type(error).__name__,
                )
                return ProviderLoopResult(
                    reply=None,
                    terminal_result=terminal_results[-1],
                    terminal_results=tuple(terminal_results),
                    normalized_results=tuple(normalized_results),
                    workflow_state=workflow_state,
                    turn_understanding=turn_understanding,
                )
            if reply.turn_understanding is not None:
                turn_understanding = reply.turn_understanding
            if reply.tool_calls and reply.text.strip():
                raise ValueError("provider cannot mix tool calls and customer text")
            if reply.interaction_decision and (reply.tool_calls or reply.text.strip()):
                raise ValueError("provider cannot mix an interaction decision with another branch")
            if reply.interaction_proposal and (
                reply.tool_calls
                or reply.text.strip()
                or reply.interaction_decision
                or reply.response_draft
            ):
                raise ValueError("provider cannot mix an interaction proposal with another branch")
            if not reply.tool_calls:
                return ProviderLoopResult(
                    reply=reply,
                    terminal_result=terminal_results[-1] if terminal_results else None,
                    terminal_results=tuple(terminal_results),
                    normalized_results=tuple(normalized_results),
                    workflow_state=workflow_state,
                    turn_understanding=turn_understanding,
                )
            if self.tools is None or self.policy is None:
                raise ValueError("provider requested tools but no catalogue is configured")
            if reply.approved_content:
                normalized_results.append(
                    self.normalizer.normalize_approved_content(
                        result_id=f"result-{uuid.uuid4().hex[:16]}",
                        intent_id="intent-1",
                        entries=reply.approved_content,
                    )
                )
            try:
                decision = self.policy.validate(
                    reply.tool_calls,
                    state=workflow_state,
                    references=references,
                    latest_customer_message=history.planning_context.latest_customer_message,
                    recent_customer_messages=tuple(
                        history.planning_context.recent_customer_messages
                    ),
                    turn_understanding=turn_understanding,
                )
            except PolicyClarification as error:
                result = error.result
                normalized = self.normalizer.normalize(
                    result_id=f"result-{uuid.uuid4().hex[:16]}",
                    call_id=f"call-{uuid.uuid4().hex[:12]}",
                    intent_id="intent-1",
                    tool="clarify_vehicle_range",
                    result=result,
                )
                return ProviderLoopResult(
                    reply=None,
                    terminal_result=result,
                    terminal_results=(result,),
                    normalized_results=(normalized,),
                    workflow_state=workflow_state,
                    turn_understanding=turn_understanding,
                )
            except ValueError as error:
                logger.warning(
                    "provider tool proposal rejected by deterministic policy",
                    extra={
                        "context": {
                            "reason": _safe_policy_failure(error),
                            "tools": [call.name for call in reply.tool_calls],
                            "argument_fields": {
                                call.name: sorted(call.arguments) for call in reply.tool_calls
                            },
                        }
                    },
                )
                history.append(
                    {
                        "role": "developer",
                        "content": (
                            "The previous proposed tool call was rejected by deterministic "
                            "policy. Re-plan the same customer request without repeating it. "
                            f"Reason: {_safe_policy_failure(error)}."
                        ),
                    }
                )
                continue
            if len(normalized_results) + len(decision.calls) > 4:
                raise ValueError("a turn can execute at most four business tool calls")
            calls = []
            action_transitions: dict[str, str] = {}
            for call in decision.calls:
                binding = bind_pending_action_handoff(
                    call.name,
                    call.arguments,
                    history.planning_context.pending_interaction,
                    workflow_state,
                )
                if binding is None:
                    calls.append(call)
                    continue
                definition = self.tools.get(call.name)
                calls.append(
                    replace(
                        call,
                        arguments=definition.validate_arguments(binding.arguments),
                    )
                )
                action_transitions[call.id] = binding.transition
            _append_assistant_calls(history, calls)
            batch_terminal_results = []
            for call in calls:
                definition = self.tools.get(call.name)
                try:
                    last_tool_result = await asyncio.wait_for(
                        self.tools.execute(call.name, call.arguments, conversation_id),
                        timeout=remaining_budget(),
                    )
                except DealershipError as error:
                    recovery = error.recovery if isinstance(error.recovery, dict) else {}
                    last_tool_result = ToolResult(
                        str(error),
                        None,
                        None,
                        {
                            "operationUnavailable": True,
                            "errorCode": error.code,
                            "message": str(error),
                            "retryable": error.retryable,
                            "recovery": error.recovery,
                        },
                        alternative_offer=(
                            recovery.get("alternativeOffer")
                            if isinstance(recovery.get("alternativeOffer"), dict)
                            else None
                        ),
                    )
                has_view = bool(
                    last_tool_result.view_type is not None
                    or last_tool_result.view_payload is not None
                )
                if has_view and not self.renderers.can_render(last_tool_result):
                    raise ValueError("tool returned an unknown application renderer")
                normalized = self.normalizer.normalize(
                    result_id=f"result-{uuid.uuid4().hex[:16]}",
                    call_id=_safe_call_id(call.id),
                    intent_id=call.intent_id or f"intent-{len(normalized_results) + 1}",
                    tool=call.name,
                    result=last_tool_result,
                )
                if not isinstance(last_tool_result.facts, dict) or not last_tool_result.facts.get(
                    "operationUnavailable"
                ):
                    transition = action_transitions.get(call.id) or (
                        self.state_reducer.execution_transition(
                            workflow_state,
                            definition.candidate_subject_field,
                            last_tool_result.facts,
                            turn_understanding,
                        )
                    )
                    workflow_state = self.state_reducer.advance(
                        workflow_state,
                        call.name,
                        call.arguments,
                        last_tool_result.view_type,
                        last_tool_result.facts,
                        transition=transition,
                    )
                normalized_results.append(normalized)
                _append_tool_result(history, call, normalized, workflow_state)
                if self.renderers.can_render(last_tool_result) or definition.result_mode in {
                    "render",
                    "workflow",
                }:
                    batch_terminal_results.append(last_tool_result)
            if batch_terminal_results:
                terminal_results.extend(batch_terminal_results)
                history.planning_context = replace(
                    history.planning_context,
                    workflow_state=workflow_state,
                    trusted_tool_facts={
                        "results": [
                            result.model_dump(mode="json") for result in normalized_results
                        ],
                        # This is deliberately a boolean, not protected payload. It tells the
                        # composer to ask for the declared secure fields without exposing values.
                        "secureInputActivation": any(
                            _is_secure_input_result(result) for result in terminal_results
                        ),
                        "selectionPromptActivation": any(
                            _is_selection_only_result(result) for result in terminal_results
                        ),
                        # Structured collections are rendered by the application from trusted
                        # display items. The composer may answer other intents, but must not turn
                        # collection facts into an improvised paragraph.
                        "collectionPresentationActivation": any(
                            _is_structured_collection_result(result)
                            for result in terminal_results
                        ),
                        "applicationContinuation": _application_continuation(terminal_results),
                    },
                )
        raise ValueError("provider tool loop exceeded limit")

def _append_assistant_calls(history: list[dict], calls: list[ToolCall]) -> None:
    history.append(
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": call.id,
                    "name": call.name,
                    "arguments": {
                        **call.arguments,
                        **({"intentKind": call.intent_kind} if call.intent_kind else {}),
                    },
                }
                for call in calls
            ],
        }
    )


def _is_secure_input_result(result: Any) -> bool:
    return bool(
        result.view_type in {"draft", "private_booking_lookup", "part_exchange_estimate_form"}
        and isinstance(result.view_payload, dict)
        and result.view_payload.get("secureInputReady") is True
    )


def _is_selection_only_result(result: Any) -> bool:
    return bool(
        result.view_type == "service_list"
        and isinstance(result.view_payload, dict)
        and result.view_payload.get("selectionOnly") is True
    )


def _is_structured_collection_result(result: Any) -> bool:
    return bool(
        isinstance(result.view_payload, dict)
        and isinstance(result.view_payload.get("collectionPresentation"), dict)
    )


def _application_continuation(results: list[Any]) -> dict[str, Any] | None:
    interaction = next(
        (result.interaction for result in reversed(results) if result.interaction is not None),
        None,
    )
    return composition_continuation(interaction)


def _append_tool_result(
    history: list[dict],
    call: ToolCall,
    result,
    workflow_state: dict[str, Any],
) -> None:
    ai_projection = result.ai_projection()
    history.extend(
        [
            {
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(ai_projection, separators=(",", ":")),
            },
            {
                "role": "developer",
                "content": "Active application workflow state (trusted data): "
                + json.dumps(workflow_state, separators=(",", ":")),
            },
        ]
    )


def _safe_call_id(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in "_-" else "-"
        for character in value
    )
    normalized = normalized[:48] or uuid.uuid4().hex[:8]
    return f"call-{normalized}" if not normalized.startswith("call-") else normalized


def _safe_policy_failure(error: ValueError) -> str:
    """Expose only application-authored policy reasons, never provider argument values."""

    message = str(error)
    known_reasons = {
        "a proposal must contain between one and four tool calls",
        "a proposal can contain at most four terminal reads",
        "a proposal can start at most one workflow",
        "confirmed writes are never model callable",
        "service selection contains an untrusted choice",
        "vehicle search refinement requires current server-owned search state",
        "workshop refinement requires a service candidate",
        "workshop refinement requires current workshop search state",
        "page vehicle selection contains an untrusted ID",
        "vehicle comparison contains an untrusted ID",
        "vehicle operation contains an untrusted ID",
        "offer operation contains an untrusted ID",
        "dealership operation contains an untrusted ID",
        "workflow contains an untrusted vehicle ID",
        "workflow contains an untrusted appointment ID",
        "workflow contains an untrusted service ID",
        "workflow contains an untrusted dealership ID",
        "there is no active capability",
        "there is no paused capability",
        "dealership selection requires clarification",
        "dealership selection is ambiguous",
    }
    return message if message in known_reasons else type(error).__name__
