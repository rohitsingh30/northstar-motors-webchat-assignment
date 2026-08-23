"""Application tool handlers for the dealership directory and opening hours."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from webchat.orchestration.presentation.suggestions import (
    dealership_contact_suggestions,
    dealership_suggestions,
    opening_hours_suggestions,
    unknown_dealership_suggestions,
)
from webchat.orchestration.tools.helpers import dealership_in_town, dealership_towns
from webchat.orchestration.tools.inputs import (
    DealershipQuery,
    DealershipScopeQuery,
    OpeningHoursQuery,
    StableId,
)
from webchat.orchestration.tools.result import ToolResult


class DealershipDirectoryGateway(Protocol):
    async def list_dealerships(self) -> dict[str, Any]: ...

    async def get_dealership(self, dealership_id: str) -> dict[str, Any]: ...

    async def get_opening_hours(self, dealership_id: str) -> dict[str, Any]: ...


ToolMethod = Callable[[dict[str, Any]], Awaitable[ToolResult]]


class DealershipToolHandler:
    """Own dealership discovery, departments, contact choices, and opening hours."""

    def __init__(self, dealership: DealershipDirectoryGateway):
        self.dealership = dealership
        self.routes: dict[str, ToolMethod] = {
            "list_dealerships": self._list_dealerships,
            "show_dealership_contact_options": self._contact_options,
            "list_dealership_departments": self._list_departments,
            "find_dealership_departments": self._find_departments,
            "get_dealership": self._get_dealership,
            "get_opening_hours": self._get_opening_hours,
            "list_opening_hours": self._list_opening_hours,
            "list_holiday_opening_hours": self._list_holiday_opening_hours,
        }

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        return await self.routes[name](arguments)

    async def _list_dealerships(self, arguments: dict[str, Any]) -> ToolResult:
        data = await self.dealership.list_dealerships()
        query = DealershipQuery.model_validate(arguments)
        items = data.get("items", [])
        if query.town:
            matched = dealership_in_town(items, query.town)
            if not matched:
                return _unknown_location(query.town, items)
            items = [item for item in items if dealership_in_town([item], query.town) is not None]
        return ToolResult(
            "Here are our dealerships.",
            "dealership_list",
            {"version": 1, "items": items, "suggestions": dealership_suggestions()},
            {"items": items},
        )

    async def _contact_options(self, arguments: dict[str, Any]) -> ToolResult:
        del arguments
        suggestions = dealership_contact_suggestions()
        return ToolResult(
            "Choose how you'd like to contact a Northstar dealership.",
            "suggestion_list",
            {"version": 1, "suggestions": suggestions},
            {"options": [suggestion["action"]["type"] for suggestion in suggestions]},
        )

    async def _list_departments(self, arguments: dict[str, Any]) -> ToolResult:
        del arguments
        dealerships = await self.dealership.list_dealerships()
        return await self._department_result(dealerships.get("items", []))

    async def _find_departments(self, arguments: dict[str, Any]) -> ToolResult:
        query = DealershipScopeQuery.model_validate(arguments)
        dealerships = await self.dealership.list_dealerships()
        items = dealerships.get("items", [])
        if query.dealershipId:
            items = [item for item in items if item.get("id") == query.dealershipId]
        elif query.town:
            matched = dealership_in_town(items, query.town)
            if not matched:
                return _unknown_location(query.town, items)
            items = [matched]
        return await self._department_result(items)

    async def _department_result(self, dealerships: list[dict[str, Any]]) -> ToolResult:
        items = []
        for dealership in dealerships:
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
            {"version": 1, "items": items, "suggestions": dealership_suggestions()},
            {"items": items},
        )

    async def _get_dealership(self, arguments: dict[str, Any]) -> ToolResult:
        dealership_id = StableId.model_validate(arguments).id
        data = await self.dealership.get_dealership(dealership_id)
        return _single_item_result("dealership_list", data, dealership_suggestions())

    async def _get_opening_hours(self, arguments: dict[str, Any]) -> ToolResult:
        dealership_id = StableId.model_validate(arguments).id
        hours = await self.dealership.get_opening_hours(dealership_id)
        dealership = await self.dealership.get_dealership(dealership_id)
        data = _opening_hours_item(dealership, hours)
        return _single_item_result("opening_hours", data, opening_hours_suggestions())

    async def _list_opening_hours(self, arguments: dict[str, Any]) -> ToolResult:
        query = OpeningHoursQuery.model_validate(arguments)
        dealership_items = await self._dealership_scope(query.dealershipId, query.town)
        if isinstance(dealership_items, ToolResult):
            return dealership_items
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
        return ToolResult(
            "Here are the current opening hours.",
            "opening_hours",
            {
                "version": 1,
                "items": items,
                "day": query.day or "Weekly",
                "suggestions": opening_hours_suggestions(),
            },
            {"day": query.day, "department": query.department, "items": items},
        )

    async def _list_holiday_opening_hours(self, arguments: dict[str, Any]) -> ToolResult:
        query = DealershipScopeQuery.model_validate(arguments)
        dealership_items = await self._dealership_scope(query.dealershipId, query.town)
        if isinstance(dealership_items, ToolResult):
            return dealership_items
        items = []
        for dealership in dealership_items:
            hours = await self.dealership.get_opening_hours(str(dealership["id"]))
            items.append(_opening_hours_item(dealership, hours, holiday_only=True))
        return ToolResult(
            "Published holiday opening hours are shown below.",
            "opening_hours",
            {
                "version": 1,
                "items": items,
                "day": "Holiday",
                "holidayOnly": True,
                "suggestions": opening_hours_suggestions(),
            },
            {"scheduleType": "holiday", "items": items},
        )

    async def _dealership_scope(
        self, dealership_id: str | None, town: str | None
    ) -> list[dict[str, Any]] | ToolResult:
        dealerships = await self.dealership.list_dealerships()
        items = dealerships.get("items", [])
        if dealership_id:
            return [item for item in items if item.get("id") == dealership_id]
        if not town:
            return items
        matched = dealership_in_town(items, town)
        if not matched:
            return _unknown_location(town, items)
        return [item for item in items if dealership_in_town([item], town) is not None]


def _opening_hours_item(
    dealership: dict[str, Any],
    hours: dict[str, Any],
    *,
    day: str | None = None,
    department: str | None = None,
    holiday_only: bool = False,
) -> dict[str, Any]:
    weekly = [
        entry
        for entry in hours.get("weekly", [])
        if not holiday_only
        and (day is None or entry.get("day") == day)
        and (department is None or str(entry.get("department", "")).lower() == department)
    ]
    return {
        "name": dealership.get("name"),
        "town": dealership.get("town"),
        "day": "Holiday" if holiday_only else day or "Weekly",
        "holidayOnly": holiday_only,
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
            if department is None or str(exception.get("department", "")).lower() == department
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
