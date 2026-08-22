from typing import Any

import pytest

from webchat.integrations.contracts import ProviderReply
from webchat.integrations.llm import FakeLlmProvider
from webchat.integrations.local_assistant import LocalAssistant
from webchat.integrations.local_assistant.context import ConversationContext


class StubProvider:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] | None = None

    async def generate_turn(self, messages: list[dict[str, Any]]) -> ProviderReply:
        self.messages = messages
        return ProviderReply("delegated")


class CustomRouter:
    def route(self, context: ConversationContext) -> ProviderReply | None:
        if "custom" in context.words:
            return ProviderReply("custom route")
        return None


class CustomToolResultHandler:
    def respond(self, context: ConversationContext) -> ProviderReply:
        return ProviderReply(f"handled {context.latest_tool_name()}")


@pytest.mark.asyncio
async def test_fake_provider_is_only_a_compatibility_adapter() -> None:
    provider = StubProvider()
    messages = [{"role": "user", "content": "hello"}]

    reply = await FakeLlmProvider(provider).generate_turn(messages)

    assert reply.text == "delegated"
    assert provider.messages == messages


@pytest.mark.asyncio
async def test_local_assistant_can_be_extended_with_an_injected_router() -> None:
    assistant = LocalAssistant(routers=[CustomRouter()])

    reply = await assistant.generate_turn([{"role": "user", "content": "custom"}])

    assert reply.text == "custom route"


@pytest.mark.asyncio
async def test_local_assistant_uses_an_injected_tool_result_handler() -> None:
    assistant = LocalAssistant(routers=[], tool_results=CustomToolResultHandler())

    reply = await assistant.generate_turn(
        [
            {"role": "user", "content": "anything"},
            {
                "role": "assistant",
                "tool_calls": [{"id": "1", "name": "custom_tool", "arguments": {}}],
            },
            {"role": "tool", "tool_call_id": "1", "content": "{}"},
        ]
    )

    assert reply.text == "handled custom_tool"
