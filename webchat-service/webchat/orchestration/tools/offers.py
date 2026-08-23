"""Application tool handlers for published vehicle offers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from webchat.orchestration.presentation.suggestions import offer_suggestions
from webchat.orchestration.tools.inputs import Filters, StableId
from webchat.orchestration.tools.result import ToolResult


class OfferGateway(Protocol):
    async def list_offers(self, query: dict[str, Any]) -> dict[str, Any]: ...

    async def get_offer(self, offer_id: str) -> dict[str, Any]: ...

    async def get_business_information(self) -> dict[str, Any]: ...


ToolMethod = Callable[[dict[str, Any]], Awaitable[ToolResult]]


class OfferToolHandler:
    """Render published offers and their authoritative finance notice."""

    def __init__(self, dealership: OfferGateway):
        self.dealership = dealership
        self.routes: dict[str, ToolMethod] = {
            "list_offers": self._list,
            "get_offer": self._get,
        }

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        return await self.routes[name](arguments)

    async def _list(self, arguments: dict[str, Any]) -> ToolResult:
        filters = Filters.model_validate(arguments).model_dump(exclude_none=True)
        data = await self.dealership.list_offers(filters)
        business = await self.dealership.get_business_information()
        items = data.get("items", [])
        return ToolResult(
            f"Found {len(items)} result{'s' if len(items) != 1 else ''}.",
            "offer_list",
            {
                "version": 1,
                "items": items,
                "financeNotice": business.get("finance", {}).get("notice"),
                "suggestions": offer_suggestions(items[0] if len(items) == 1 else None),
            },
            data,
        )

    async def _get(self, arguments: dict[str, Any]) -> ToolResult:
        offer_id = StableId.model_validate(arguments).id
        data = await self.dealership.get_offer(offer_id)
        return ToolResult(
            "Here are the current platform details.",
            "offer_list",
            {
                "version": 1,
                "items": [data],
                "suggestions": offer_suggestions(data),
                "detailView": True,
            },
            data,
        )
