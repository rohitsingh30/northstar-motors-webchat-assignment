from typing import Any

from ..contracts import LlmProvider, ProviderReply, ToolCall
from .planner import DeterministicToolPlanner


class FakeLlmProvider:
    """Deterministic fake provider with the hosted provider's direct-tool boundary."""

    requires_review = False

    def __init__(self, planner: LlmProvider | None = None) -> None:
        self._planner = planner if planner is not None else DeterministicToolPlanner()

    async def generate_turn(self, messages: list[dict[str, Any]]) -> ProviderReply:
        reply = await self._planner.generate_turn(messages)
        branches = bool(reply.tool_calls) + bool(reply.text.strip()) + bool(
            reply.interaction_decision
        )
        if len(reply.tool_calls) > 4 or branches != 1:
            raise ValueError("fake provider returned an invalid direct proposal")
        return reply


__all__ = ["FakeLlmProvider", "LlmProvider", "ProviderReply", "ToolCall"]
