from typing import Any

from .contracts import LlmProvider, ProviderReply, ToolCall
from .local_assistant import LocalAssistant


class FakeLlmProvider:
    """Compatibility adapter for the deterministic local assistant."""

    def __init__(self, assistant: LlmProvider | None = None) -> None:
        self._assistant = assistant if assistant is not None else LocalAssistant()

    async def generate_turn(self, messages: list[dict[str, Any]]) -> ProviderReply:
        return await self._assistant.generate_turn(messages)


__all__ = ["FakeLlmProvider", "LlmProvider", "ProviderReply", "ToolCall"]
