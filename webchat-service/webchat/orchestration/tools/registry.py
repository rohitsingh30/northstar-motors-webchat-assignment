"""Registry that dispatches validated tool calls to capability handlers."""

from __future__ import annotations

from typing import Any, Protocol

from webchat.integrations.dealership import DealershipClient
from webchat.orchestration.tools.catalog import CatalogueToolHandler
from webchat.orchestration.tools.forms import FormToolHandler
from webchat.orchestration.tools.result import ToolResult
from webchat.orchestration.tools.vehicles import VehicleToolHandler
from webchat.orchestration.tools.workflows import WorkflowToolHandler
from webchat.orchestration.tools.workshop import WorkshopReadToolHandler


class RoutedToolHandler(Protocol):
    routes: dict

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult: ...


class ToolRegistry:
    """Route allow-listed tools to focused capability handlers."""

    def __init__(self, dealership: DealershipClient, workflows=None):
        handlers: tuple[RoutedToolHandler, ...] = (
            VehicleToolHandler(dealership),
            CatalogueToolHandler(dealership),
            WorkshopReadToolHandler(dealership),
            FormToolHandler(dealership),
        )
        self.routes = {
            tool_name: handler
            for handler in handlers
            for tool_name in handler.routes
        }
        self.workflow_tools = WorkflowToolHandler(dealership, workflows)

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        conversation_id: str | None = None,
    ) -> ToolResult:
        if self.workflow_tools.supports(name):
            return await self.workflow_tools.execute(name, arguments, conversation_id)
        handler = self.routes.get(name)
        if handler is None:
            raise ValueError(f"Unknown or disallowed tool: {name}")
        return await handler.execute(name, arguments)
