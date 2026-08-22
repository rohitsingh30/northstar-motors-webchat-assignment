from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from webchat.integrations.contracts import ProviderReply

from .context import ConversationContext
from .responses import ToolResultResponder
from .routes import IntentRouter, SupportRouter, VehicleRouter, WorkshopRouter


class ToolResultHandler(Protocol):
    def respond(self, context: ConversationContext) -> ProviderReply: ...


class LocalAssistant:
    """Coordinates independently testable routers for development without an API key."""

    def __init__(
        self,
        routers: Iterable[IntentRouter] | None = None,
        tool_results: ToolResultHandler | None = None,
    ) -> None:
        # Explicit support actions are more specific than broad workshop words such
        # as "service", so they must be offered the turn first.
        default_routers = (SupportRouter(), WorkshopRouter(), VehicleRouter())
        self.routers = tuple(routers) if routers is not None else default_routers
        self.tool_results = tool_results if tool_results is not None else ToolResultResponder()

    async def generate_turn(self, messages: list[dict[str, Any]]) -> ProviderReply:
        context = ConversationContext.from_messages(messages)
        if context.has_tool_result:
            return self.tool_results.respond(context)
        for router in self.routers:
            if reply := router.route(context):
                return reply
        if any(word in context.words for word in ("hello", "hi", "hey")):
            return ProviderReply(
                "Hello — I can help you find vehicles, current offers, dealerships, or services. "
                "For example, try “cars below £35,000”."
            )
        return ProviderReply(
            "I can help with vehicles, offers, dealerships, and servicing. "
            "Try “cars below £35,000” or ask about current offers."
        )
