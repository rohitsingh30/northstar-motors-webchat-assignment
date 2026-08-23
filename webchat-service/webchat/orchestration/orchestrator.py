from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

from webchat.integrations.contracts import LlmProvider
from webchat.orchestration.context.builder import (
    ConversationHistoryBuilder,
    TurnContext,
)
from webchat.orchestration.planning.plan_policy import SemanticPlanPolicy
from webchat.orchestration.planning.transitions import TransitionController
from webchat.orchestration.presentation.response import ResponsePresenter
from webchat.orchestration.tools.actions import StructuredActionHandler
from webchat.orchestration.turns.provider_loop import (
    ProviderToolLoop,
    TurnReferences,
)
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
        semantic_plan_policy: SemanticPlanPolicy | None = None,
    ):
        self.messages = messages
        self.turns = turns
        self.provider = provider
        self.conversations = conversations
        self.timeout_seconds = timeout_seconds
        self.transitions = TransitionController()
        self.history_builder = ConversationHistoryBuilder(conversations)
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._semantic_plan_policy = semantic_plan_policy
        self._tools = None
        self.tools = tools

    @property
    def tools(self):
        return self._tools

    @tools.setter
    def tools(self, tools) -> None:
        """Keep tool-dependent collaborators aligned when tests replace the executor."""
        self._tools = tools
        self.action_handler = (
            StructuredActionHandler(tools, self.transitions) if tools is not None else None
        )
        self.provider_loop = ProviderToolLoop(
            self.provider,
            tools,
            self.transitions,
            self.conversations,
            self.timeout_seconds,
            self._semantic_plan_policy,
        )
        self.presenter = ResponsePresenter(tools)

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
                return await self._execute_turn(
                    conversation_id, turn.id, text, action
                )
            except TimeoutError:
                self.turns.finish(turn.id, "failed", "LLM_TIMEOUT")
                return turn.id, "failed", self.messages.list_for_turn(turn.id)
            # This is the provider/tool failure boundary; private exception details
            # never reach users.
            except Exception as error:
                logger.exception(
                    "conversation turn failed: %s", type(error).__name__
                )
                self.turns.finish(turn.id, "failed", "LLM_INVALID_RESPONSE")
                return turn.id, "failed", self.messages.list_for_turn(turn.id)

    async def _execute_turn(
        self,
        conversation_id: str,
        turn_id: str,
        user_text: str,
        action: dict | None,
    ) -> tuple[str, str, list]:
        conversation_messages = self.messages.list(conversation_id)
        context = self.history_builder.build(
            conversation_id, conversation_messages, action
        )
        action_result = await self._structured_action_result(
            action, conversation_id, conversation_messages, context
        )
        if action_result is not None:
            return self._finish_tool_turn(conversation_id, turn_id, action_result)

        loop = await self.provider_loop.run(
            context.history,
            context.workflow_state,
            conversation_id,
            user_text,
            _turn_references(context),
        )
        if loop.terminal_result is not None:
            return self._finish_tool_turn(
                conversation_id, turn_id, loop.terminal_result
            )
        reply = loop.reply
        if reply is None or reply.tool_calls:
            raise ValueError("provider tool loop exceeded limit")
        if not reply.text.strip() or len(reply.text) > 8000:
            raise ValueError("provider returned invalid text")
        presented = await self.presenter.present(
            reply,
            loop.last_tool_result,
            conversation_id=conversation_id,
            user_text=user_text,
            planned_reply=loop.planned_reply,
            planned_suggestion_dimension=loop.suggestion_dimension,
        )
        return self._finish_presented_turn(conversation_id, turn_id, presented)

    async def _structured_action_result(
        self,
        action: dict | None,
        conversation_id: str,
        conversation_messages: list,
        context: TurnContext,
    ):
        if self.action_handler is None or not action:
            return None
        result = await self.action_handler.execute(
            action,
            conversation_id,
            conversation_messages,
            context.workflow_state,
        )
        if result is None:
            return None
        workflow_state = self.transitions.advance_action(
            context.workflow_state, action, result.view_type
        )
        if self.conversations is not None:
            self.conversations.update_workflow_state(
                conversation_id, workflow_state
            )
        return result

    def _finish_presented_turn(self, conversation_id: str, turn_id: str, presented):
        self.messages.add(
            conversation_id,
            "assistant",
            presented.text.strip(),
            turn_id,
            presented.view_type,
            json.dumps(presented.view_payload) if presented.view_payload else None,
        )
        self.turns.finish(turn_id, "completed")
        return turn_id, "completed", self.messages.list_for_turn(turn_id)

    def _finish_tool_turn(self, conversation_id: str, turn_id: str, result):
        payload = dict(result.view_payload) if result.view_payload else None
        self.messages.add(
            conversation_id,
            "assistant",
            result.text,
            turn_id,
            result.view_type,
            json.dumps(payload) if payload else None,
        )
        self.turns.finish(turn_id, "completed")
        return turn_id, "completed", self.messages.list_for_turn(turn_id)


def _turn_references(context: TurnContext) -> TurnReferences:
    return TurnReferences(
        context.displayed_vehicles,
        context.vehicle_search_state,
        context.displayed_offers,
        context.page_vehicles,
    )
