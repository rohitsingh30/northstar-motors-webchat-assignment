from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from webchat.integrations.contracts import ProviderReply

from .context import ConversationContext
from .responses import ToolResultResponder
from .routes import ApplicationRouteHandler, SupportRouter, VehicleRouter, WorkshopRouter


class ToolResultHandler(Protocol):
    def respond(self, context: ConversationContext) -> ProviderReply: ...


class DeterministicApplicationRouter:
    """Resolve only high-confidence application routes shared by every provider."""

    def __init__(
        self,
        routers: Iterable[ApplicationRouteHandler] | None = None,
        tool_results: ToolResultHandler | None = None,
    ) -> None:
        # Explicit support actions are more specific than broad workshop words such
        # as "service", so they must be offered the turn first.
        default_routers = (SupportRouter(), WorkshopRouter(), VehicleRouter())
        self.routers = tuple(routers) if routers is not None else default_routers
        self.tool_results = tool_results if tool_results is not None else ToolResultResponder()

    def route(self, messages: list[dict[str, Any]]) -> ProviderReply | None:
        """Return a deterministic route or fact response, never generic conversation."""
        context = ConversationContext.from_messages(messages)
        return self._route_context(context)

    def _route_context(
        self, context: ConversationContext
    ) -> ProviderReply | None:
        if context.has_tool_result:
            return self.tool_results.respond(context)
        for router in self.routers:
            if reply := router.route(context):
                return reply
        return None
