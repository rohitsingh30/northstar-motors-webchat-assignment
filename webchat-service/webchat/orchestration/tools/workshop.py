"""Workshop services, locations, slots, and test-drive read tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from webchat.domain.business_semantics import money
from webchat.orchestration.presentation.suggestions import (
    service_type_suggestions,
    unknown_dealership_suggestions,
    vehicle_availability_suggestions,
    workshop_location_suggestions,
    workshop_no_availability_suggestions,
)
from webchat.orchestration.tools.helpers import dealership_in_town
from webchat.orchestration.tools.inputs import Filters, ServiceQuery
from webchat.orchestration.tools.result import ToolResult
from webchat.orchestration.tools.service_resolution import resolve_live_service
from webchat.orchestration.tools.vehicles import (
    vehicle_availability_result,
    vehicle_item,
)


class WorkshopGateway(Protocol):
    async def list_service_types(self) -> dict[str, Any]: ...

    async def list_workshop_locations(self) -> dict[str, Any]: ...

    async def list_workshop_slots(self, filters: dict[str, Any]) -> dict[str, Any]: ...

    async def list_test_drive_slots(self, filters: dict[str, Any]) -> dict[str, Any]: ...

    async def list_dealerships(self) -> dict[str, Any]: ...

    async def get_vehicle(self, vehicle_id: str) -> dict[str, Any]: ...

    async def get_vehicle_availability(
        self, vehicle_id: str
    ) -> dict[str, Any]: ...


ToolMethod = Callable[[dict[str, Any]], Awaitable[ToolResult]]


class WorkshopReadToolHandler:
    """Own workshop catalogue, location, and live slot reads."""

    def __init__(self, dealership: WorkshopGateway):
        self.dealership = dealership
        self.routes: dict[str, ToolMethod] = {
            "get_service_information": self._service_information,
            "list_service_types": self._list_service_types,
            "list_workshop_locations": self._list_workshop_locations,
            "list_test_drive_slots": self._test_drive_slots,
            "list_workshop_slots": self._workshop_slots,
        }

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        return await self.routes[name](arguments)

    async def _service_information(self, arguments: dict[str, Any]) -> ToolResult:
        query = ServiceQuery.model_validate(arguments)
        data = await self.dealership.list_service_types()
        items = data.get("items", [])
        matched = next(
            (
                item
                for item in items
                if query.serviceTypeId is not None
                and str(item.get("id")) == query.serviceTypeId
            ),
            None,
        )
        if matched is None and query.q is not None:
            resolution = resolve_live_service(items, query.q)
            if resolution.status != "matched":
                return _service_resolution_result(resolution.status, resolution.candidates)
            matched = resolution.service
        if matched is None:
            return _service_resolution_result("unsupported", ())
        name = str(matched.get("name") or "This service")
        price_value = matched.get("priceFromPence")
        price = (
            f"from {money(price_value)}"
            if isinstance(price_value, int)
            else "priced on request"
        )
        duration = matched.get("durationMinutes")
        duration_text = (
            f" and takes about {duration} minutes"
            if isinstance(duration, int)
            else ""
        )
        description = str(matched.get("description") or "").strip()
        return ToolResult(
            f"{name} is {price}{duration_text}."
            + (f" {description}" if description else ""),
            None,
            None,
            {
                "resolution": {"status": "matched"},
                "service": matched,
            },
        )

    async def _list_service_types(self, arguments: dict[str, Any]) -> ToolResult:
        del arguments
        data = await self.dealership.list_service_types()
        items = data.get("items", [])
        return ToolResult(
            "Here are our supported services.",
            "service_list",
            {
                "version": 1,
                "items": items,
                "suggestions": service_type_suggestions(items),
            },
            data,
        )

    async def _list_workshop_locations(
        self, arguments: dict[str, Any]
    ) -> ToolResult:
        del arguments
        data = await self.dealership.list_workshop_locations()
        return ToolResult(
            "Here are our workshop locations.",
            "workshop_location_list",
            {
                "version": 1,
                "items": data.get("items", []),
                "suggestions": workshop_location_suggestions(),
            },
            data,
        )

    async def _test_drive_slots(self, arguments: dict[str, Any]) -> ToolResult:
        filters = Filters.model_validate(arguments).model_dump(exclude_none=True)
        vehicle_id = str(filters["vehicleId"])
        availability = await self.dealership.get_vehicle_availability(vehicle_id)
        if availability.get("availability") != "available":
            return await vehicle_availability_result(
                self.dealership, vehicle_id, availability
            )
        data = await self.dealership.list_test_drive_slots(filters)
        items = data.get("items", [])
        payload = {
            "version": 1,
            "items": items,
            "vehicleId": filters.get("vehicleId"),
        }
        if filters.get("vehicleId"):
            payload["vehicle"] = vehicle_item(
                await self.dealership.get_vehicle(filters["vehicleId"])
            )
        if not items:
            payload["suggestions"] = vehicle_availability_suggestions(availability)
        return _slot_result("test_drive_slot_picker", items, payload, data)

    async def _workshop_slots(self, arguments: dict[str, Any]) -> ToolResult:
        filters = Filters.model_validate(arguments).model_dump(exclude_none=True)
        workflow_mode = filters.pop("workflowMode", "booking")
        service_name = filters.pop("serviceTypeName", None)
        service_label = service_name
        town = filters.pop("dealershipTown", None)
        requested_town: str | None = None
        if town and not filters.get("dealershipId"):
            dealerships = await self.dealership.list_dealerships()
            matched = dealership_in_town(dealerships.get("items", []), town)
            if not matched:
                return ToolResult(
                    f"Northstar does not currently have a workshop in {town.title()}.",
                    "suggestion_list",
                    {
                        "version": 1,
                        "suggestions": unknown_dealership_suggestions(),
                    },
                    {"requestedTown": town, "items": []},
                )
            filters["dealershipId"] = matched["id"]
            requested_town = str(matched.get("town") or town)
        if not service_name and not filters.get("serviceTypeId"):
            service_types = await self.dealership.list_service_types()
            return _service_selection_result(
                service_types.get("items", []),
                "Please choose a workshop service before selecting a time.",
            )
        if service_name and not filters.get("serviceTypeId"):
            resolution = await self._resolve_service(service_name)
            if isinstance(resolution, ToolResult):
                return resolution
            filters["serviceTypeId"] = resolution["id"]
            service_label = str(resolution.get("name") or service_name)

        filters.setdefault("dateFrom", datetime.now(UTC).date().isoformat())
        data = await self.dealership.list_workshop_slots(filters)
        now = datetime.now(UTC)
        data["items"] = _future_slots(data.get("items", []), now)
        alternative_slots: list[dict[str, Any]] = []
        if not data["items"]:
            alternative_filters = {
                key: value for key, value in filters.items() if key != "dealershipId"
            }
            alternative_data = await self.dealership.list_workshop_slots(
                alternative_filters
            )
            alternative_slots = _future_slots(alternative_data.get("items", []), now)
        items = data["items"]
        payload = {"version": 1, "items": items, "mode": workflow_mode}
        if not items:
            payload["suggestions"] = workshop_no_availability_suggestions(
                alternative_slots, service_label, filters.get("serviceTypeId")
            )
            service_phrase = (
                f" for {service_label}" if service_label else ""
            )
            location_phrase = (
                f" in {requested_town}" if requested_town else ""
            )
            empty_message = (
                f"No appointments{service_phrase} are currently available"
                f"{location_phrase}."
            )
            payload["emptyMessage"] = empty_message
            return ToolResult(empty_message, "slot_list", payload, data)
        return _slot_result("slot_list", items, payload, data)

    async def _resolve_service(self, service_name: str) -> dict | ToolResult:
        service_types = await self.dealership.list_service_types()
        candidates = service_types.get("items", [])
        resolution = resolve_live_service(candidates, service_name)
        if resolution.status == "matched" and resolution.service is not None:
            return resolution.service
        return _service_resolution_result(
            resolution.status, resolution.candidates
        )


def _future_slots(items: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if item.get("startsAt")
        and datetime.fromisoformat(str(item["startsAt"])) > now
    ]


def _service_selection_result(items: list[dict], text: str) -> ToolResult:
    return ToolResult(
        text,
        "service_list",
        {
            "version": 1,
            "items": items,
            "suggestions": service_type_suggestions(items),
        },
        {"items": items},
    )


def _service_resolution_result(
    status: str, candidates: tuple[dict[str, Any], ...]
) -> ToolResult:
    if status == "ambiguous":
        items = list(candidates)
        return ToolResult(
            "I found more than one possible workshop service. Please choose the one you mean.",
            "service_list",
            {
                "version": 1,
                "items": items,
                "suggestions": service_type_suggestions(items),
                "resolution": {"status": "ambiguous"},
            },
            {"resolution": {"status": "ambiguous"}, "items": items},
        )
    suggestions = [
        {
            "label": "View supported services",
            "text": "What workshop services do you support?",
            "action": {"type": "show_workshop_services"},
        }
    ]
    return ToolResult(
        "That service is not currently listed as a supported Northstar workshop service.",
        "suggestion_list",
        {
            "version": 1,
            "suggestions": suggestions,
            "resolution": {"status": "unsupported"},
        },
        {"resolution": {"status": "unsupported"}, "items": []},
    )


def _slot_result(
    view_type: str,
    items: list[dict],
    payload: dict[str, Any],
    facts: dict[str, Any],
) -> ToolResult:
    return ToolResult(
        f"Found {len(items)} result{'s' if len(items) != 1 else ''}.",
        view_type,
        payload,
        facts,
    )
