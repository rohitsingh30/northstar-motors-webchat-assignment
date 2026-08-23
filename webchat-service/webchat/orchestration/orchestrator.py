from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

from webchat.domain.interactions import (
    PendingInteraction,
    interaction_from_view,
    preceding_interaction,
)
from webchat.integrations.contracts import LlmProvider, ReviewUnavailableError
from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.context import (
    ConversationHistoryBuilder,
    TurnContext,
)
from webchat.orchestration.presentation.clarifications import clarification_suggestion_result
from webchat.orchestration.provider_loop import (
    ProviderToolLoop,
    TurnReferences,
)
from webchat.orchestration.state import WorkflowStateReducer
from webchat.orchestration.tools.actions import StructuredActionHandler
from webchat.orchestration.tools.result import ToolResult
from webchat.persistence.repositories import (
    ConversationRepository,
    MessageRepository,
    TurnRepository,
)

logger = logging.getLogger(__name__)


class Orchestrator:
    """Own turn lifecycle; delegate context, routing, tools, and presentation."""

    def __init__(
        self,
        messages: MessageRepository,
        turns: TurnRepository,
        provider: LlmProvider,
        tools=None,
        conversations: ConversationRepository | None = None,
        timeout_seconds: float = 20,
    ):
        self.messages = messages
        self.turns = turns
        self.provider = provider
        self.conversations = conversations
        self.timeout_seconds = timeout_seconds
        self.state_reducer = WorkflowStateReducer()
        self.history_builder = ConversationHistoryBuilder(conversations)
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tools = None
        self.tools = tools

    @property
    def tools(self):
        return self._tools

    @tools.setter
    def tools(self, tools) -> None:
        """Keep tool-dependent collaborators aligned when tests replace the executor."""
        if tools is not None and not isinstance(tools, UnifiedToolCatalog):
            tools = UnifiedToolCatalog(tools)
        self._tools = tools
        self.action_handler = StructuredActionHandler(tools) if tools is not None else None
        self.provider_loop = ProviderToolLoop(
            self.provider,
            tools,
            self.state_reducer,
            self.conversations,
            self.timeout_seconds,
        )

    async def run(
        self,
        conversation_id: str,
        client_message_id: str,
        text: str,
        action: dict | None = None,
    ) -> tuple[str, str, list]:
        async with self._locks[conversation_id]:
            existing = self.turns.get_by_client_id(conversation_id, client_message_id)
            if existing:
                return existing.id, existing.status, self.messages.list_for_turn(existing.id)

            turn = self.turns.create(conversation_id, client_message_id)
            self.messages.add(conversation_id, "user", text, turn.id)
            try:
                return await self._execute_turn(conversation_id, turn.id, action)
            except TimeoutError:
                self.turns.finish(turn.id, "failed", "LLM_TIMEOUT")
                return turn.id, "failed", self.messages.list_for_turn(turn.id)
            except ReviewUnavailableError:
                self.turns.finish(turn.id, "failed", "LLM_REVIEW_FAILED")
                return turn.id, "failed", self.messages.list_for_turn(turn.id)
            # This is the provider/tool failure boundary; private exception details
            # never reach users.
            except Exception as error:
                logger.exception("conversation turn failed: %s", type(error).__name__)
                self.turns.finish(turn.id, "failed", "LLM_INVALID_RESPONSE")
                return turn.id, "failed", self.messages.list_for_turn(turn.id)

    async def _execute_turn(
        self,
        conversation_id: str,
        turn_id: str,
        action: dict | None,
    ) -> tuple[str, str, list]:
        conversation_messages = self.messages.list(conversation_id)
        context = self.history_builder.build(conversation_id, conversation_messages, action)
        action_result = await self._structured_action_result(
            action, conversation_id, conversation_messages, context
        )
        if action_result is not None:
            return self._finish_tool_turn(conversation_id, turn_id, action_result)

        loop = await self.provider_loop.run(
            context.history,
            context.workflow_state,
            conversation_id,
            _turn_references(context),
        )
        if loop.terminal_result is not None:
            return self._finish_tool_turn(conversation_id, turn_id, loop.terminal_result)
        reply = loop.reply
        if reply is None or reply.tool_calls:
            raise ValueError("provider tool loop exceeded limit")
        if reply.interaction_decision:
            result = await self._interaction_decision_result(
                reply.interaction_decision,
                conversation_id,
                conversation_messages,
                context,
            )
            if isinstance(result, ToolResult):
                return self._finish_tool_turn(conversation_id, turn_id, result)
            return self._finish_text_turn(conversation_id, turn_id, result)
        if not reply.text.strip() or len(reply.text) > 8000:
            raise ValueError("provider returned invalid text")
        suggestion_result = clarification_suggestion_result(
            reply.text,
            reply.suggestions,
            reply.interaction,
        )
        if reply.suggestions and suggestion_result is None:
            raise ValueError("provider returned invalid clarification suggestions")
        if suggestion_result is not None:
            return self._finish_tool_turn(conversation_id, turn_id, suggestion_result)
        return self._finish_text_turn(
            conversation_id,
            turn_id,
            reply.text,
            reply.interaction,
        )

    async def _interaction_decision_result(
        self,
        decision: str,
        conversation_id: str,
        conversation_messages: list,
        context: TurnContext,
    ) -> ToolResult | str:
        pending = preceding_interaction(conversation_messages)
        if pending is None:
            raise ValueError("provider decided an interaction that is not pending")
        interaction, assistant_message = pending
        if interaction.kind == "input":
            raise ValueError("provider cannot accept or decline an input interaction")
        if decision == "decline":
            return (
                "No action has been taken."
                if interaction.kind == "protected_confirmation"
                else "Okay — I won't continue with that."
            )
        if decision != "accept":
            raise ValueError("provider returned an unknown interaction decision")
        if interaction.kind == "single_action":
            result = await self._structured_action_result(
                interaction.actions[0],
                conversation_id,
                conversation_messages,
                context,
            )
            return result or "That option is no longer available. Please choose another action."
        return _repeat_pending_view(interaction, assistant_message)

    async def _structured_action_result(
        self,
        action: dict | None,
        conversation_id: str,
        conversation_messages: list,
        context: TurnContext,
    ):
        if self.action_handler is None or not action:
            return None
        execution = await self.action_handler.execute(
            action,
            conversation_id,
            conversation_messages,
            context.workflow_state,
        )
        if execution is None:
            return None
        result = execution.result
        workflow_state = self.state_reducer.advance(
            context.workflow_state,
            execution.tool_name,
            execution.arguments,
            result.view_type,
            result.facts,
        )
        if self.conversations is not None:
            self.conversations.update_workflow_state(conversation_id, workflow_state)
        return result

    def _finish_text_turn(
        self,
        conversation_id: str,
        turn_id: str,
        text: str,
        interaction: PendingInteraction | None = None,
    ):
        self.messages.add(
            conversation_id,
            "assistant",
            text.strip(),
            turn_id,
            interaction_json=interaction.as_json() if interaction else None,
        )
        self.turns.finish(turn_id, "completed")
        return turn_id, "completed", self.messages.list_for_turn(turn_id)

    def _finish_tool_turn(self, conversation_id: str, turn_id: str, result):
        payload = dict(result.view_payload) if result.view_payload else None
        interaction = result.interaction or interaction_from_view(
            result.text,
            result.view_type,
            payload,
        )
        self.messages.add(
            conversation_id,
            "assistant",
            result.text,
            turn_id,
            result.view_type,
            json.dumps(payload) if payload else None,
            interaction.as_json() if interaction else None,
        )
        self.turns.finish(turn_id, "completed")
        return turn_id, "completed", self.messages.list_for_turn(turn_id)


def _repeat_pending_view(interaction: PendingInteraction, message) -> ToolResult | str:
    prompt = (
        "Please use the confirmation controls below to complete or cancel this request."
        if interaction.kind == "protected_confirmation"
        else "Please choose one of the available options."
    )
    if not message.view_type or not message.view_payload_json:
        return prompt
    try:
        payload = json.loads(message.view_payload_json)
    except (TypeError, ValueError):
        return prompt
    if not isinstance(payload, dict):
        return prompt
    return ToolResult(
        prompt,
        message.view_type,
        payload,
        {"interactionRepeated": True},
        interaction,
    )


def _turn_references(context: TurnContext) -> TurnReferences:
    return TurnReferences(
        context.displayed_vehicles,
        context.vehicle_search_state,
        context.displayed_offers,
        context.page_vehicles,
        context.displayed_dealerships,
    )
