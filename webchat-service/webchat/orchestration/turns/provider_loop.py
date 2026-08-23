"""Bounded local and semantic-provider tool execution loops."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Protocol

from webchat.integrations.contracts import LlmProvider, ProviderReply, ToolCall
from webchat.orchestration.planning.plan_policy import (
    REPLAN_INSTRUCTION,
    SemanticPlanPolicy,
)
from webchat.orchestration.planning.transitions import TransitionController
from webchat.orchestration.presentation.response import (
    DIRECT_ANSWER_TOOLS,
    is_renderable,
)
from webchat.orchestration.tools.contracts import ToolExecutor


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


@dataclass(frozen=True)
class ProviderLoopResult:
    reply: ProviderReply | None = None
    last_tool_result: Any | None = None
    terminal_result: Any | None = None
    planned_reply: bool = False
    suggestion_dimension: str | None = None


class ProviderToolLoop:
    """Coordinate the bounded semantic-provider and validated-tool exchange."""

    def __init__(
        self,
        provider: LlmProvider,
        tools: ToolExecutor | None,
        transitions: TransitionController,
        workflow_states: WorkflowStateStore | None,
        timeout_seconds: float,
        semantic_plan_policy: SemanticPlanPolicy | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.transitions = transitions
        self.workflow_states = workflow_states
        self.timeout_seconds = timeout_seconds
        self.semantic_plan_policy = semantic_plan_policy

    async def run(
        self,
        history: list[dict],
        workflow_state: dict[str, Any],
        conversation_id: str,
        user_text: str,
        references: TurnReferences,
    ) -> ProviderLoopResult:
        last_tool_result = None
        conversation_response_retry_used = False
        for _ in range(4):
            reply = await asyncio.wait_for(
                self.provider.generate_turn(history), timeout=self.timeout_seconds
            )
            if self.semantic_plan_policy is not None:
                gate = self.semantic_plan_policy.evaluate(
                    reply,
                    history,
                    user_text=user_text,
                    workflow_state=workflow_state,
                    retry_used=conversation_response_retry_used,
                    conversation_id=conversation_id,
                )
                if gate.action == "replan":
                    conversation_response_retry_used = True
                    history.append(
                        {"role": "developer", "content": REPLAN_INSTRUCTION}
                    )
                    continue
                reply = gate.reply
            reply, workflow_state, planned, dimension = self._apply_plan(
                reply,
                workflow_state,
                conversation_id,
                user_text,
                references,
            )
            if not reply.tool_calls:
                return ProviderLoopResult(
                    reply=reply,
                    last_tool_result=last_tool_result,
                    planned_reply=planned,
                    suggestion_dimension=dimension,
                )
            self._validate_tool_calls(reply.tool_calls)
            _append_assistant_calls(history, reply.tool_calls)
            terminal_result = None
            for call in reply.tool_calls:
                last_tool_result = await self.tools.execute(
                    call.name, call.arguments, conversation_id
                )
                workflow_state = self.transitions.advance(
                    workflow_state, call.name, last_tool_result.view_type
                )
                self._save_state(conversation_id, workflow_state)
                _append_tool_result(history, call, last_tool_result, workflow_state)
                if _terminal_tool_result(call, last_tool_result):
                    terminal_result = last_tool_result
            if terminal_result is not None:
                return ProviderLoopResult(
                    last_tool_result=last_tool_result,
                    terminal_result=terminal_result,
                )
        raise ValueError("provider tool loop exceeded limit")

    def _apply_plan(
        self,
        reply: ProviderReply,
        workflow_state: dict[str, Any],
        conversation_id: str,
        user_text: str,
        references: TurnReferences,
    ) -> tuple[ProviderReply, dict[str, Any], bool, str | None]:
        if reply.plan is None:
            return reply, workflow_state, False, None
        transition = self.transitions.resolve(
            reply.plan,
            workflow_state,
            user_text=user_text,
            displayed_vehicles=references.displayed_vehicles,
            vehicle_search_state=references.vehicle_search_state,
            displayed_offers=references.displayed_offers,
            page_vehicles=references.page_vehicles,
        )
        workflow_state = transition.state
        self._save_state(conversation_id, workflow_state)
        if transition.tool_call is not None:
            return ProviderReply("", [transition.tool_call]), workflow_state, False, None
        return (
            ProviderReply(transition.response),
            workflow_state,
            True,
            transition.suggestion_dimension,
        )

    def _validate_tool_calls(self, calls: list[ToolCall]) -> None:
        if self.tools is None or len(calls) > 4:
            raise ValueError("provider requested disallowed tools")

    def _save_state(self, conversation_id: str, workflow_state: dict[str, Any]) -> None:
        if self.workflow_states is not None:
            self.workflow_states.update_workflow_state(conversation_id, workflow_state)


def _terminal_tool_result(call: ToolCall, result) -> bool:
    return is_renderable(result) or (
        call.name in DIRECT_ANSWER_TOOLS and result.view_type is None
    )


def _append_assistant_calls(history: list[dict], calls: list[ToolCall]) -> None:
    history.append(
        {
            "role": "assistant",
            "tool_calls": [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in calls
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
