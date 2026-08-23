"""Read tools for offers, dealerships, hours, and business information."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from webchat.orchestration.presentation.suggestions import (
    dealership_suggestions,
    offer_suggestions,
    opening_hours_suggestions,
    unknown_dealership_suggestions,
)
from webchat.orchestration.tools.business_information import (
    BusinessInformationResolver,
)
from webchat.orchestration.tools.helpers import (
    dealership_in_town,
    dealership_towns,
)
from webchat.orchestration.tools.inputs import (
    BusinessInformationQuery,
    DealershipQuery,
    Filters,
    OpeningHoursQuery,
    StableId,
)
from webchat.orchestration.tools.result import ToolResult


class CatalogueGateway(Protocol):
    async def list_offers(self, query: dict[str, Any]) -> dict[str, Any]: ...

    async def get_offer(self, offer_id: str) -> dict[str, Any]: ...

    async def list_dealerships(self) -> dict[str, Any]: ...

    async def get_dealership(self, dealership_id: str) -> dict[str, Any]: ...

    async def get_opening_hours(self, dealership_id: str) -> dict[str, Any]: ...

    async def get_business_information(self) -> dict[str, Any]: ...


ToolMethod = Callable[[dict[str, Any]], Awaitable[ToolResult]]


class CatalogueToolHandler:
    """Own offers, locations, opening hours, and public business information."""

    def __init__(
        self,
        dealership: CatalogueGateway,
        business_information: BusinessInformationResolver | None = None,
    ):
        self.dealership = dealership
        self.business_information = (
            business_information or BusinessInformationResolver()
        )
        self.routes: dict[str, ToolMethod] = {
            "list_offers": self._list_offers,
            "get_offer": self._get_offer,
            "list_dealerships": self._list_dealerships,
            "list_dealership_departments": self._list_departments,
            "get_dealership": self._get_dealership,
            "get_opening_hours": self._get_opening_hours,
            "list_opening_hours": self._list_opening_hours,
            "get_business_information": self._business_information,
        }

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        return await self.routes[name](arguments)

    async def _list_offers(self, arguments: dict[str, Any]) -> ToolResult:
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

    async def _get_offer(self, arguments: dict[str, Any]) -> ToolResult:
        offer_id = StableId.model_validate(arguments).id
        data = await self.dealership.get_offer(offer_id)
        return _single_item_result("offer_list", data, offer_suggestions(data))

    async def _list_dealerships(self, arguments: dict[str, Any]) -> ToolResult:
        data = await self.dealership.list_dealerships()
        query = DealershipQuery.model_validate(arguments)
        items = data.get("items", [])
        if query.town:
            matched = dealership_in_town(items, query.town)
            if not matched:
                return _unknown_location(query.town, items)
            items = [
                item
                for item in items
                if dealership_in_town([item], query.town) is not None
            ]
        return ToolResult(
            "Here are our dealerships.",
            "dealership_list",
            {
                "version": 1,
                "items": items,
                "suggestions": dealership_suggestions(),
            },
            {"items": items},
        )

    async def _list_departments(self, arguments: dict[str, Any]) -> ToolResult:
        del arguments
        dealerships = await self.dealership.list_dealerships()
        items = []
        for dealership in dealerships.get("items", []):
            hours = await self.dealership.get_opening_hours(str(dealership["id"]))
            departments = sorted(
                {
                    str(entry.get("department"))
                    for entry in hours.get("weekly", [])
                    if entry.get("department")
                }
            )
            items.append(
                {
                    "id": dealership.get("id"),
                    "name": dealership.get("name"),
                    "town": dealership.get("town"),
                    "departments": departments,
                }
            )
        return ToolResult(
            "Here are the departments available at each dealership.",
            "dealership_list",
            {
                "version": 1,
                "items": items,
                "suggestions": dealership_suggestions(),
            },
            {"items": items},
        )

    async def _get_dealership(self, arguments: dict[str, Any]) -> ToolResult:
        dealership_id = StableId.model_validate(arguments).id
        data = await self.dealership.get_dealership(dealership_id)
        return _single_item_result(
            "dealership_list", data, dealership_suggestions()
        )

    async def _get_opening_hours(self, arguments: dict[str, Any]) -> ToolResult:
        dealership_id = StableId.model_validate(arguments).id
        hours = await self.dealership.get_opening_hours(dealership_id)
        dealership = await self.dealership.get_dealership(dealership_id)
        data = _opening_hours_item(dealership, hours)
        return _single_item_result(
            "opening_hours", data, opening_hours_suggestions()
        )

    async def _list_opening_hours(self, arguments: dict[str, Any]) -> ToolResult:
        query = OpeningHoursQuery.model_validate(arguments)
        dealerships = await self.dealership.list_dealerships()
        dealership_items = dealerships.get("items", [])
        if query.town:
            matched = dealership_in_town(dealership_items, query.town)
            if not matched:
                return _unknown_location(query.town, dealership_items)
            dealership_items = [
                item
                for item in dealership_items
                if dealership_in_town([item], query.town) is not None
            ]
        items = []
        for dealership in dealership_items:
            hours = await self.dealership.get_opening_hours(str(dealership["id"]))
            items.append(
                _opening_hours_item(
                    dealership,
                    hours,
                    day=query.day,
                    department=query.department,
                )
            )
        facts = {
            "day": query.day,
            "department": query.department,
            "items": items,
        }
        return ToolResult(
            "Here are the current opening hours.",
            "opening_hours",
            {
                "version": 1,
                "items": items,
                "day": query.day or "Weekly",
                "suggestions": opening_hours_suggestions(),
            },
            facts,
        )

    async def _business_information(self, arguments: dict[str, Any]) -> ToolResult:
        query = BusinessInformationQuery.model_validate(arguments)
        data = await self.dealership.get_business_information()
        resolution = self.business_information.resolve(
            data,
            topic=query.topic,
            question=query.question,
        )
        facts = {
            "outcome": resolution.outcome,
            "topic": resolution.topic,
            "factKeys": [fact.key for fact in resolution.facts],
        }
        if resolution.outcome == "unavailable":
            return ToolResult(
                "I don't have confirmed Northstar information that answers that question. "
                "Would you like me to help you contact a dealership?",
                None,
                None,
                facts,
            )
        if resolution.outcome == "ambiguous":
            labels = ", ".join(dict.fromkeys(fact.label for fact in resolution.facts))
            return ToolResult(
                "I found more than one possible Northstar information topic. "
                f"Please ask specifically about {labels}.",
                None,
                None,
                facts,
            )
        view_facts = [fact.as_view() for fact in resolution.facts]
        facts["facts"] = view_facts
        return ToolResult(
            f"Here is the current Northstar {query.topic.replace('_', '-')} information.",
            "business_information",
            {
                "version": 2,
                "organisation": data.get("organisation") or "Northstar Motors",
                "topic": resolution.topic,
                "facts": view_facts,
            },
            facts,
        )


def _opening_hours_item(
    dealership: dict[str, Any],
    hours: dict[str, Any],
    *,
    day: str | None = None,
    department: str | None = None,
) -> dict[str, Any]:
    weekly = [
        entry
        for entry in hours.get("weekly", [])
        if (day is None or entry.get("day") == day)
        and (
            department is None
            or str(entry.get("department", "")).lower() == department
        )
    ]
    return {
        "name": dealership.get("name"),
        "town": dealership.get("town"),
        "day": day or "Weekly",
        "departments": [
            {
                "name": str(entry.get("department", "")).title(),
                "day": entry.get("day"),
                "opensAt": entry.get("opensAt"),
                "closesAt": entry.get("closesAt"),
                "closed": bool(entry.get("closed")),
            }
            for entry in weekly
        ],
        "holidayExceptions": [
            exception
            for exception in hours.get("holidayExceptions", [])
            if department is None
            or str(exception.get("department", "")).lower() == department
        ],
    }


def _single_item_result(
    view_type: str, data: dict[str, Any], suggestions: list[dict]
) -> ToolResult:
    return ToolResult(
        "Here are the current platform details.",
        view_type,
        {"version": 1, "items": [data], "suggestions": suggestions},
        data,
    )


def _unknown_location(town: str, dealerships: list[dict]) -> ToolResult:
    return ToolResult(
        f"Northstar does not currently have a dealership in {town.title()}. "
        f"Our locations are {dealership_towns(dealerships)}.",
        "suggestion_list",
        {"version": 1, "suggestions": unknown_dealership_suggestions()},
        {"requestedTown": town, "items": []},
    )
