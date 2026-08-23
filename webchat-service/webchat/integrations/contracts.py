from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class TurnPlan:
    """A semantic decision produced by the model and executed by the application."""

    domain: str
    goal: str
    arguments: dict[str, Any] = field(default_factory=dict)
    response: str = ""
    version: int = 2


@dataclass(frozen=True)
class ProviderReply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    plan: TurnPlan | None = None


class LlmProvider(Protocol):
    async def generate_turn(self, messages: list[dict[str, Any]]) -> ProviderReply: ...
