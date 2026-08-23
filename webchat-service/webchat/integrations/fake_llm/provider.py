from typing import Any

from webchat.orchestration.planning.turn_plan import parse_turn_plan

from ..contracts import LlmProvider, ProviderReply, ToolCall
from .planner import DeterministicTurnPlanner


class FakeLlmProvider:
    """Deterministic fake provider with the same validated plan boundary as OpenAI."""

    def __init__(self, planner: LlmProvider | None = None) -> None:
        self._planner = planner if planner is not None else DeterministicTurnPlanner()

    async def generate_turn(self, messages: list[dict[str, Any]]) -> ProviderReply:
        reply = await self._planner.generate_turn(messages)
        if reply.plan is None or reply.tool_calls:
            raise ValueError("fake provider must return exactly one typed turn plan")
        plan = parse_turn_plan(
            {
                "version": reply.plan.version,
                "domain": reply.plan.domain,
                "goal": reply.plan.goal,
                "response": reply.plan.response or None,
                **reply.plan.arguments,
            }
        )
        return ProviderReply(text=reply.text, plan=plan)


__all__ = ["FakeLlmProvider", "LlmProvider", "ProviderReply", "ToolCall"]
