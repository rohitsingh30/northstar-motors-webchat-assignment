from __future__ import annotations

from typing import Any, Protocol

from webchat.domain.interactions import input_interaction
from webchat.integrations.contracts import ProviderReply, ToolCall

from .context import ConversationContext


class ApplicationRouteHandler(Protocol):
    def route(self, context: ConversationContext) -> ProviderReply | None: ...


def tool(call_id: str, name: str, arguments: dict[str, Any] | None = None) -> ProviderReply:
    return ProviderReply("", [ToolCall(call_id, name, arguments or {})])


def clarify(
    text: str,
    blocking_tool: str,
    *,
    fields: tuple[str, ...] = (),
    preconditions: tuple[str, ...] = (),
) -> ProviderReply:
    """Emit the same structured clarification contract as hosted planning."""
    return ProviderReply(
        text,
        response_mode="clarify",
        interaction=input_interaction(
            text,
            blocking_tool,
            list(fields),
            list(preconditions),
        ),
    )
