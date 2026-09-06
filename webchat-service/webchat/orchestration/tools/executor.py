"""Dispatch application-backed catalogue tools to focused capability handlers."""

from __future__ import annotations

from typing import Any, Protocol

from webchat.integrations.dealership import DealershipClient
from webchat.orchestration.tools.business import BusinessInformationToolHandler
from webchat.orchestration.tools.dealerships import DealershipToolHandler
from webchat.orchestration.tools.forms import CollectorToolHandler
from webchat.orchestration.tools.offers import OfferToolHandler
from webchat.orchestration.tools.result import ToolResult
from webchat.orchestration.tools.vehicles import VehicleToolHandler
from webchat.orchestration.tools.workflows import WorkflowToolHandler
from webchat.orchestration.tools.workshop import WorkshopReadToolHandler


class ApplicationToolHandler(Protocol):
    routes: dict

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult: ...


class ApplicationToolExecutor:
    """Execute local implementations selected through the unified catalogue."""

    def __init__(self, dealership: DealershipClient, workflows=None):
        handlers: tuple[ApplicationToolHandler, ...] = (
            VehicleToolHandler(dealership),
            OfferToolHandler(dealership),
            DealershipToolHandler(dealership),
            BusinessInformationToolHandler(dealership),
            WorkshopReadToolHandler(dealership),
            CollectorToolHandler(dealership),
        )
        self.routes = {tool_name: handler for handler in handlers for tool_name in handler.routes}
        self.workflow_tools = WorkflowToolHandler(dealership, workflows)

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        conversation_id: str | None = None,
        *,
        replacement_draft_id: str | None = None,
    ) -> ToolResult:
        if self.workflow_tools.supports(name):
            return await self.workflow_tools.execute(
                name,
                arguments,
                conversation_id,
                replacement_draft_id=replacement_draft_id,
            )
        handler = self.routes.get(name)
        if handler is None:
            raise ValueError(f"Unknown or disallowed tool: {name}")
        return await handler.execute(name, arguments)
