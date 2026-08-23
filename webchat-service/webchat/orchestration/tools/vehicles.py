"""Vehicle search, detail, availability, and comparison tools."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from webchat.domain.business_semantics import availability_text, money
from webchat.orchestration.presentation.suggestions import (
    no_vehicle_results_suggestions,
    unknown_dealership_suggestions,
    vehicle_availability_suggestions,
    vehicle_comparison_suggestions,
    vehicle_search_suggestions,
)
from webchat.orchestration.tools.helpers import dealership_in_town
from webchat.orchestration.tools.inputs import (
    PageVehicleSelection,
    StableId,
    VehicleComparison,
    VehicleModelComparison,
    VehicleSearch,
)
from webchat.orchestration.tools.result import ToolResult


class VehicleGateway(Protocol):
    async def search_vehicles(self, query: dict[str, Any]) -> dict[str, Any]: ...

    async def get_vehicle(self, vehicle_id: str) -> dict[str, Any]: ...

    async def get_vehicle_availability(
        self, vehicle_id: str
    ) -> dict[str, Any]: ...

    async def list_dealerships(self) -> dict[str, Any]: ...


ToolMethod = Callable[[dict[str, Any]], Awaitable[ToolResult]]


def vehicle_item(vehicle: dict[str, Any]) -> dict[str, Any]:
    vehicle_id = str(vehicle.get("id", ""))
    if not vehicle_id.startswith("veh-"):
        raise ValueError("platform returned an invalid vehicle ID")
    return {
        "id": vehicle_id,
        "make": vehicle.get("make"),
        "model": vehicle.get("model"),
        "variant": vehicle.get("variant"),
        "year": vehicle.get("year"),
        "bodyStyle": vehicle.get("bodyStyle"),
        "colour": vehicle.get("colour"),
        "price": money(vehicle.get("pricePence")),
        "pricePence": vehicle.get("pricePence"),
        "monthlyPricePence": vehicle.get("monthlyPricePence"),
        "mileage": vehicle.get("mileage"),
        "fuelType": vehicle.get("fuelType"),
        "transmission": vehicle.get("transmission"),
        "availability": vehicle.get("availability"),
        "dealershipName": vehicle.get("dealershipName"),
        "dealershipTown": vehicle.get("dealershipTown"),
        "image": f"/api/chat/v1/vehicle-images/{vehicle_id}",
        "url": f"/?vehicle={vehicle_id}",
    }


async def vehicle_availability_result(
    dealership: VehicleGateway,
    vehicle_id: str,
    availability: dict[str, Any] | None = None,
) -> ToolResult:
    current = availability or await dealership.get_vehicle_availability(vehicle_id)
    vehicle = vehicle_item(await dealership.get_vehicle(vehicle_id))
    return ToolResult(
        availability_text(str(current.get("availability"))),
        "vehicle_availability",
        {
            "version": 1,
            **current,
            "vehicle": vehicle,
            "suggestions": vehicle_availability_suggestions(current),
        },
        current,
    )


class VehicleToolHandler:
    """Own live inventory discovery, filtering, details, and comparisons."""

    def __init__(self, dealership: VehicleGateway):
        self.dealership = dealership
        self.routes: dict[str, ToolMethod] = {
            "search_vehicles": self._search,
            "select_page_vehicles": self._select_from_page,
            "get_vehicle_facets": self._facets,
            "get_vehicle": self._get,
            "get_vehicle_availability": self._availability,
            "compare_vehicles": self._compare,
            "compare_vehicle_models": self._compare_models,
        }

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        return await self.routes[name](arguments)

    async def _search(self, arguments: dict[str, Any]) -> ToolResult:
        query = VehicleSearch.model_validate(arguments).model_dump(exclude_none=True)
        search_state = {key: value for key, value in query.items() if key != "page"}
        query["pageSize"] = 3
        query.setdefault("availability", "available")
        town = self._extract_town(query)
        if town and not query.get("dealershipId"):
            dealerships = await self.dealership.list_dealerships()
            matched = dealership_in_town(dealerships.get("items", []), town)
            if not matched:
                return ToolResult(
                    f"Northstar does not currently have a dealership in {town.title()}.",
                    "suggestion_list",
                    {
                        "version": 1,
                        "suggestions": unknown_dealership_suggestions(),
                    },
                    {"requestedTown": town, "items": []},
                )
            query["dealershipId"] = matched["id"]

        data = await self.dealership.search_vehicles(query)
        items = [vehicle_item(item) for item in data.get("items", [])]
        total = int(data.get("pagination", {}).get("totalItems", len(items)))
        if total == 0:
            requested = query.get("q") or query.get("make") or "that description"
            return ToolResult(
                f"I couldn't find any available vehicles matching {requested}. "
                "Try another make, model, budget, or location.",
                "suggestion_list",
                {"version": 1, "suggestions": no_vehicle_results_suggestions()},
                data,
            )

        page = int(data.get("pagination", {}).get("page", query.get("page", 1)))
        page_size = int(data.get("pagination", {}).get("pageSize", query["pageSize"]))
        suggestions = vehicle_search_suggestions(
            query=query,
            item_count=len(items),
            page=page,
            page_size=page_size,
            total=total,
        )
        return ToolResult(
            f"Found {total} matching vehicle{'s' if total != 1 else ''}.",
            "vehicle_list",
            {
                "version": 1,
                "items": items,
                "total": total,
                "page": page,
                "pageSize": page_size,
                "search": {"filters": search_state, "page": page},
                "suggestions": suggestions,
            },
            data,
        )

    @staticmethod
    def _extract_town(query: dict[str, Any]) -> str | None:
        town = query.pop("dealershipTown", None)
        if town or not query.get("q"):
            return town
        location_match = re.search(
            r"\b(?:in|at|near)\s+([a-z][a-z -]{1,60})",
            str(query["q"]),
            re.IGNORECASE,
        )
        if not location_match:
            return None
        town = re.split(
            r"\b(?:below|under|less than|up to|with|near|at|on|from)\b",
            location_match.group(1),
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" .,?!")
        remaining_query = re.sub(
            r"\b(?:cars?|vehicles?)\b", "", str(query["q"])[: location_match.start()]
        ).strip(" .,?!")
        if remaining_query:
            query["q"] = remaining_query
        else:
            query.pop("q", None)
        return town

    async def _select_from_page(self, arguments: dict[str, Any]) -> ToolResult:
        selection = PageVehicleSelection.model_validate(arguments)
        records = await asyncio.gather(
            *(
                self.dealership.get_vehicle(vehicle_id)
                for vehicle_id in selection.vehicleIds
            )
        )
        filtered = [
            record for record in records if self._matches_selection(record, selection)
        ]
        sort_keys = {
            "newest": lambda item: -int(item.get("year") or 0),
            "priceAsc": lambda item: int(item.get("pricePence") or 0),
            "priceDesc": lambda item: -int(item.get("pricePence") or 0),
            "mileageAsc": lambda item: int(item.get("mileage") or 0),
        }
        filtered.sort(key=sort_keys[selection.sort])
        total = len(filtered)
        items = [vehicle_item(item) for item in filtered[: selection.limit]]
        if not items:
            return ToolResult(
                "None of the vehicles currently shown match that request.",
                "suggestion_list",
                {"version": 1, "suggestions": no_vehicle_results_suggestions()},
                {"items": [], "scope": "currentPage"},
            )
        text = (
            f"This is the lowest-mileage vehicle among the {len(records)} currently shown."
            if selection.sort == "mileageAsc" and selection.limit == 1
            else f"Showing {len(items)} matching vehicle"
            f"{'s' if len(items) != 1 else ''} from the current page."
        )
        return ToolResult(
            text,
            "vehicle_list",
            {
                "version": 1,
                "items": items,
                "total": total,
                "page": 1,
                "pageSize": selection.limit,
                "scope": "currentPage",
                "suggestions": [],
            },
            {"items": filtered, "scope": "currentPage"},
        )

    @staticmethod
    def _matches_selection(record: dict, selection: PageVehicleSelection) -> bool:
        if selection.q:
            searchable = " ".join(
                str(record.get(field) or "")
                for field in (
                    "make",
                    "model",
                    "variant",
                    "bodyStyle",
                    "colour",
                    "fuelType",
                )
            ).casefold()
            if not all(token in searchable for token in selection.q.casefold().split()):
                return False
        for field, expected in (
            ("make", selection.make),
            ("model", selection.model),
            ("fuelType", selection.fuelType),
            ("transmission", selection.transmission),
            ("bodyStyle", selection.bodyStyle),
            ("availability", selection.availability),
            ("dealershipId", selection.dealershipId),
            ("dealershipTown", selection.dealershipTown),
        ):
            if expected is not None and str(record.get(field) or "").casefold() != str(
                expected
            ).casefold():
                return False
        numeric_limits = (
            ("pricePence", selection.minPricePence, lambda actual, limit: actual < limit),
            ("pricePence", selection.maxPricePence, lambda actual, limit: actual > limit),
            ("mileage", selection.maxMileage, lambda actual, limit: actual > limit),
            ("year", selection.minYear, lambda actual, limit: actual < limit),
        )
        return not any(
            limit is not None and rejected(int(record.get(field) or 0), limit)
            for field, limit, rejected in numeric_limits
        )

    async def _facets(self, arguments: dict[str, Any]) -> ToolResult:
        del arguments
        facet_fields = {
            "make": "makes",
            "model": "models",
            "fuelType": "fuelTypes",
            "transmission": "transmissions",
            "bodyStyle": "bodyStyles",
        }
        facets: dict[str, set[str]] = {name: set() for name in facet_fields.values()}
        prices: set[int] = set()
        mileages: set[int] = set()
        page = 1
        while page <= 20:
            data = await self.dealership.search_vehicles(
                {
                    "page": page,
                    "pageSize": 50,
                    "sort": "newest",
                    "availability": "available",
                }
            )
            for vehicle in data.get("items", []):
                for source, target in facet_fields.items():
                    if value := vehicle.get(source):
                        facets[target].add(str(value))
                if isinstance(vehicle.get("pricePence"), int):
                    prices.add(int(vehicle["pricePence"]))
                if isinstance(vehicle.get("mileage"), int):
                    mileages.add(int(vehicle["mileage"]))
            total_pages = int(data.get("pagination", {}).get("totalPages", 1))
            if page >= total_pages:
                break
            page += 1
        payload = {key: sorted(values) for key, values in facets.items()}
        payload["budgets"] = _live_numeric_choices(prices, rounding=500_000)
        payload["mileages"] = _live_numeric_choices(mileages, rounding=5_000)
        return ToolResult("Loaded current vehicle search options.", None, None, payload)

    async def _get(self, arguments: dict[str, Any]) -> ToolResult:
        vehicle_id = StableId.model_validate(arguments).id
        data = await self.dealership.get_vehicle(vehicle_id)
        item = vehicle_item(data)
        return ToolResult(
            f"{item['make']} {item['model']}: {item['price']}.",
            "vehicle_details",
            {"version": 1, "vehicle": item, "suggestions": []},
            data,
        )

    async def _availability(self, arguments: dict[str, Any]) -> ToolResult:
        vehicle_id = StableId.model_validate(arguments).id
        return await vehicle_availability_result(self.dealership, vehicle_id)

    async def _compare(self, arguments: dict[str, Any]) -> ToolResult:
        comparison = VehicleComparison.model_validate(arguments)
        if any(
            not re.fullmatch(r"veh-[0-9]{3}", item)
            for item in comparison.vehicleIds
        ):
            raise ValueError("invalid vehicle ID")
        vehicle_ids = list(dict.fromkeys(comparison.vehicleIds))
        if len(vehicle_ids) < 2:
            return _unresolved_comparison([])
        records = [
            await self.dealership.get_vehicle(vehicle_id) for vehicle_id in vehicle_ids
        ]
        return _comparison_result(
            records, f"Compared {len(records)} vehicles using current stock details."
        )

    async def _compare_models(self, arguments: dict[str, Any]) -> ToolResult:
        request = VehicleModelComparison.model_validate(arguments)
        records: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for query in request.queries:
            data = await self.dealership.search_vehicles(
                {
                    "q": query,
                    "availability": "available",
                    "pageSize": 1,
                    "sort": "priceAsc",
                }
            )
            item = next(iter(data.get("items", [])), None)
            item_id = str(item.get("id")) if item else ""
            if item and item_id not in seen_ids:
                records.append(item)
                seen_ids.add(item_id)
        if len(records) < 2:
            return _unresolved_comparison(records, resolve_models=True)
        return _comparison_result(records, f"Compared {len(records)} current vehicles.")


def _live_numeric_choices(values: set[int], *, rounding: int) -> list[int]:
    ordered = sorted(value for value in values if value >= 0)
    if not ordered:
        return []
    last = len(ordered) - 1
    thresholds = []
    for quartile in (1, 2, 3, 4):
        value = ordered[(last * quartile) // 4]
        rounded = ((value + rounding - 1) // rounding) * rounding
        if rounded > 0 and rounded not in thresholds:
            thresholds.append(rounded)
    return thresholds


def _unresolved_comparison(
    records: list[dict[str, Any]], *, resolve_models: bool = False
) -> ToolResult:
    text = (
        "I couldn't resolve at least two different available vehicles from those descriptions."
        if resolve_models
        else "Please choose at least two different vehicles to compare."
    )
    return ToolResult(
        text,
        "suggestion_list",
        {"version": 1, "suggestions": vehicle_comparison_suggestions()},
        {"items": records},
    )


def _comparison_result(records: list[dict[str, Any]], text: str) -> ToolResult:
    return ToolResult(
        text,
        "vehicle_comparison",
        {
            "version": 1,
            "items": [vehicle_item(item) for item in records],
            "suggestions": vehicle_comparison_suggestions(),
        },
        {"items": records},
    )
