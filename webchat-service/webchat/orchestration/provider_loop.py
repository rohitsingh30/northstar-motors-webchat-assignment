"""Bounded provider and concrete-tool execution loop."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from webchat.integrations.contracts import LlmProvider, ProviderReply, SemanticMessages, ToolCall
from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.policy import ToolPolicyGate
from webchat.orchestration.presentation.registry import RendererRegistry
from webchat.orchestration.state import WorkflowStateReducer


class WorkflowStateStore(Protocol):
    def update_workflow_state(
        self, conversation_id: str, workflow_state: dict[str, Any]
    ) -> None: ...


@dataclass(frozen=True)
class TurnReferences:
    displayed_vehicles: list[dict[str, object]]
    vehicle_search_state: dict[str, object] | None
    displayed_offers: list[dict[str, object]]
    page_vehicles: list[dict[str, object]]
    displayed_dealerships: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class ProviderLoopResult:
    reply: ProviderReply | None = None
    terminal_result: Any | None = None


class ProviderToolLoop:
    """Validate reviewed proposals, execute real tools, and expose only trusted results."""

    def __init__(
        self,
        provider: LlmProvider,
        tools: UnifiedToolCatalog | None,
        state_reducer: WorkflowStateReducer,
        workflow_states: WorkflowStateStore | None,
        timeout_seconds: float,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.state_reducer = state_reducer
        self.workflow_states = workflow_states
        self.timeout_seconds = timeout_seconds
        self.policy = ToolPolicyGate(tools) if tools is not None else None
        self.renderers = RendererRegistry(tools)

    async def run(
        self,
        history: SemanticMessages,
        workflow_state: dict[str, Any],
        conversation_id: str,
        references: TurnReferences,
    ) -> ProviderLoopResult:
        last_tool_result = None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_seconds

        def remaining_budget() -> float:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("provider turn budget exhausted")
            return remaining

        for _ in range(4):
            reply = await asyncio.wait_for(
                self.provider.generate_turn(history), timeout=remaining_budget()
            )
            if getattr(self.provider, "requires_review", False) and reply.review is None:
                raise ValueError("hosted provider must return an independently reviewed proposal")
            if reply.tool_calls and reply.text.strip():
                raise ValueError("provider cannot mix tool calls and customer text")
            if reply.interaction_decision and (reply.tool_calls or reply.text.strip()):
                raise ValueError("provider cannot mix an interaction decision with another branch")
            if not reply.tool_calls:
                return ProviderLoopResult(reply=reply)
            if self.tools is None or self.policy is None:
                raise ValueError("provider requested tools but no catalogue is configured")
            decision = self.policy.validate(
                reply.tool_calls,
                state=workflow_state,
                references=references,
                latest_customer_message=history.reviewer_context.latest_customer_message,
                recent_customer_messages=tuple(
                    history.reviewer_context.recent_customer_messages
                ),
            )
            if decision.clarification:
                return ProviderLoopResult(
                    reply=ProviderReply(
                        decision.clarification,
                        review=reply.review,
                        response_mode="clarify",
                    ),
                )
            _append_assistant_calls(history, decision.calls)
            terminal_result = None
            for call in decision.calls:
                definition = self.tools.get(call.name)
                last_tool_result = await asyncio.wait_for(
                    self.tools.execute(call.name, call.arguments, conversation_id),
                    timeout=remaining_budget(),
                )
                workflow_state = self.state_reducer.advance(
                    workflow_state,
                    call.name,
                    call.arguments,
                    last_tool_result.view_type,
                    last_tool_result.facts,
                )
                self._save_state(conversation_id, workflow_state)
                _append_tool_result(history, call, last_tool_result, workflow_state)
                history.reviewer_context = replace(
                    history.reviewer_context,
                    workflow_state=workflow_state,
                    trusted_tool_facts={
                        "tool": call.name,
                        "facts": last_tool_result.facts,
                    },
                )
                has_view = bool(
                    last_tool_result.view_type is not None
                    or last_tool_result.view_payload is not None
                )
                if has_view and not self.renderers.can_render(call.name, last_tool_result):
                    raise ValueError(f"tool returned an undeclared renderer: {call.name}")
                if self.renderers.can_render(
                    call.name, last_tool_result
                ) or definition.result_mode in {
                    "render",
                    "workflow",
                }:
                    terminal_result = last_tool_result
            if terminal_result is not None:
                return ProviderLoopResult(
                    terminal_result=terminal_result,
                )
        raise ValueError("provider tool loop exceeded limit")

    def _save_state(self, conversation_id: str, workflow_state: dict[str, Any]) -> None:
        if self.workflow_states is not None:
            self.workflow_states.update_workflow_state(conversation_id, workflow_state)


def _append_assistant_calls(history: list[dict], calls: list[ToolCall]) -> None:
    history.append(
        {
            "role": "assistant",
            "tool_calls": [
                {"id": call.id, "name": call.name, "arguments": call.arguments} for call in calls
            ],
        }
    )


def _append_tool_result(
    history: list[dict], call: ToolCall, result, workflow_state: dict[str, Any]
) -> None:
    history.extend(
        [
            {
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result.facts, separators=(",", ":")),
            },
            {
                "role": "developer",
                "content": "Active application workflow state (trusted data): "
                + json.dumps(workflow_state, separators=(",", ":")),
            },
        ]
    )
