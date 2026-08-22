from __future__ import annotations

from typing import Any, Protocol

from webchat.integrations.contracts import ProviderReply, ToolCall

from ..context import ConversationContext


class IntentRouter(Protocol):
    def route(self, context: ConversationContext) -> ProviderReply | None: ...


def tool(call_id: str, name: str, arguments: dict[str, Any] | None = None) -> ProviderReply:
    return ProviderReply("", [ToolCall(call_id, name, arguments or {})])
