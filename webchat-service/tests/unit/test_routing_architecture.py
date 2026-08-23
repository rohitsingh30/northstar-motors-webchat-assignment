from typing import Any

import pytest

from webchat.api.models import TurnAction
from webchat.domain.workflows import REQUIRED_FIELDS
from webchat.integrations.contracts import ProviderReply, ToolCall, TurnPlan
from webchat.integrations.fake_llm import FakeLlmProvider
from webchat.orchestration.planning.transitions import TransitionController
from webchat.orchestration.routing import (
    DeterministicApplicationRouter,
    DeterministicPlanRouter,
)
from webchat.orchestration.routing.context import ConversationContext
from webchat.orchestration.tools.actions import StructuredActionHandler
from webchat.orchestration.tools.workflows import WorkflowToolHandler


class StubProvider:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] | None = None

    async def generate_turn(self, messages: list[dict[str, Any]]) -> ProviderReply:
        self.messages = messages
        return ProviderReply(
            "", plan=TurnPlan("conversation", "respond", response="delegated")
        )


class CustomRouter:
    def route(self, context: ConversationContext) -> ProviderReply | None:
        if "custom" in context.words:
            return ProviderReply("custom route")
        return None


class CustomToolResultHandler:
    def respond(self, context: ConversationContext) -> ProviderReply:
        return ProviderReply(f"handled {context.latest_tool_name()}")


def test_every_typed_widget_action_has_exactly_one_handler() -> None:
    handler = StructuredActionHandler(object(), TransitionController())
    action_types = set(TurnAction.model_fields["type"].annotation.__args__)

    assert set(handler._handlers) == action_types


def test_every_workflow_kind_has_exactly_one_preparation_tool() -> None:
    assert set(WorkflowToolHandler.TOOL_TO_KIND.values()) == set(REQUIRED_FIELDS)


@pytest.mark.asyncio
async def test_fake_provider_accepts_an_injected_typed_planner() -> None:
    provider = StubProvider()
    messages = [{"role": "user", "content": "hello"}]

    reply = await FakeLlmProvider(provider).generate_turn(messages)

    assert reply.plan is not None
    assert reply.plan.response == "delegated"
    assert provider.messages == messages


@pytest.mark.asyncio
async def test_fake_provider_rejects_direct_tool_calls_from_an_injected_planner() -> None:
    class DirectToolProvider:
        async def generate_turn(self, messages):
            del messages
            return ProviderReply("", [ToolCall("1", "list_offers", {})])

    with pytest.raises(ValueError, match="exactly one typed turn plan"):
        await FakeLlmProvider(DirectToolProvider()).generate_turn(
            [{"role": "user", "content": "offers"}]
        )


def test_application_router_can_be_extended_with_an_injected_router() -> None:
    router = DeterministicApplicationRouter(routers=[CustomRouter()])

    reply = router.route([{"role": "user", "content": "custom"}])

    assert reply is not None
    assert reply.text == "custom route"


def test_application_router_has_no_generic_conversation_fallback() -> None:
    router = DeterministicApplicationRouter(routers=[CustomRouter()])

    assert router.route([{"role": "user", "content": "hello"}]) is None
    reply = router.route([{"role": "user", "content": "custom"}])
    assert reply is not None
    assert reply.text == "custom route"


def test_online_safety_fallback_reenters_the_typed_plan_pipeline() -> None:
    reply = DeterministicPlanRouter().route(
        [{"role": "user", "content": "will you pick up the car?"}]
    )

    assert reply is not None
    assert reply.tool_calls == []
    assert reply.plan is not None
    assert (reply.plan.domain, reply.plan.goal) == ("vehicle", "search")


def test_application_router_uses_an_injected_tool_result_handler() -> None:
    router = DeterministicApplicationRouter(
        routers=[], tool_results=CustomToolResultHandler()
    )

    reply = router.route(
        [
            {"role": "user", "content": "anything"},
            {
                "role": "assistant",
                "tool_calls": [{"id": "1", "name": "custom_tool", "arguments": {}}],
            },
            {"role": "tool", "tool_call_id": "1", "content": "{}"},
        ]
    )

    assert reply is not None
    assert reply.text == "handled custom_tool"


def test_tool_result_remains_current_before_trusted_state_message() -> None:
    router = DeterministicApplicationRouter(
        routers=[], tool_results=CustomToolResultHandler()
    )

    reply = router.route(
        [
            {"role": "user", "content": "anything"},
            {
                "role": "assistant",
                "tool_calls": [{"id": "1", "name": "custom_tool", "arguments": {}}],
            },
            {"role": "tool", "tool_call_id": "1", "content": "{}"},
            {"role": "developer", "content": "Active application workflow state: {}"},
        ]
    )

    assert reply is not None
    assert reply.text == "handled custom_tool"
