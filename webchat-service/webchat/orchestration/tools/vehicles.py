"""Vehicle search, detail, availability, and comparison tools."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from webchat.domain.business_semantics import availability_text, money
from webchat.orchestration.presentation.suggestions import (
    current_page_vehicle_suggestions,
    no_vehicle_results_suggestions,
    unknown_dealership_suggestions,
    vehicle_availability_suggestions,
    vehicle_comparison_suggestions,
    vehicle_facet_prompt,
    vehicle_facet_suggestions,
    vehicle_filter_summary,
    vehicle_resolution_suggestions,
    vehicle_search_suggestions,
    vehicle_starting_point_suggestions,
)
from webchat.orchestration.tools.helpers import dealership_in_town, dealership_towns
from webchat.orchestration.tools.inputs import (
    PageVehicleSelection,
    StableId,
    VehicleComparisonRuntime,
    VehicleIdentityQuery,
    VehicleModelComparison,
    VehiclePreferenceRequest,
    VehicleSearch,
)
from webchat.orchestration.tools.result import ToolResult, alternative_offer


class VehicleGateway(Protocol):
    async def search_vehicles(self, query: dict[str, Any]) -> dict[str, Any]: ...

    async def get_vehicle(self, vehicle_id: str) -> dict[str, Any]: ...

    async def get_vehicle_availability(self, vehicle_id: str) -> dict[str, Any]: ...

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
    available = str(current.get("availability") or "").casefold() == "available"
    return ToolResult(
        availability_text(str(current.get("availability"))),
        "vehicle_availability",
        {
            "version": 1,
            **current,
            "vehicle": vehicle,
            "suggestions": vehicle_availability_suggestions(current),
        },
        {**current, **({"outcome": "unavailable"} if not available else {})},
    )


class VehicleToolHandler:
    """Own live inventory discovery, filtering, details, and comparisons."""

    def __init__(self, dealership: VehicleGateway):
        self.dealership = dealership
        self.routes: dict[str, ToolMethod] = {
            "search_vehicles": self._search,
            "reset_vehicle_search": self._search,
            "refine_vehicle_search": self._search,
            "select_page_vehicles": self._select_from_page,
            "get_vehicle_facets": self._facets,
            "get_vehicle": self._get,
            "get_vehicle_availability": self._availability,
            "resolve_vehicle_availability": self._resolve_availability,
            "compare_vehicles": self._compare,
            "compare_vehicle_models": self._compare_models,
            "show_vehicle_preferences": self._preferences,
        }

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        return await self.routes[name](arguments)

    async def _search(self, arguments: dict[str, Any]) -> ToolResult:
        query = VehicleSearch.model_validate(arguments).model_dump(exclude_none=True)
        search_state = {key: value for key, value in query.items() if key != "page"}
        query.setdefault("availability", "available")
        query["pageSize"] = 3
        town = self._extract_town(query)
        if town and not query.get("dealershipId"):
            dealerships = await self.dealership.list_dealerships()
            matched = dealership_in_town(dealerships.get("items", []), town)
            if not matched:
                locations = dealership_towns(dealerships.get("items", []))
                return ToolResult(
                    f"Northstar does not currently have a dealership in {town.title()}.",
                    "suggestion_list",
                    {
                        "version": 1,
                        "suggestions": unknown_dealership_suggestions(),
                    },
                    {"outcome": "unavailable", "requestedTown": town, "items": []},
                    alternative_offer=alternative_offer(
                        reason_code="requested_dealership_location_unavailable",
                        requested_outcome=f"Available vehicles at a Northstar dealership in {town.title()}",
                        failure_reason=f"Northstar does not currently have a dealership in {town.title()}.",
                        offered_outcome=(
                            f"You can search vehicle stock at the current locations: {locations}."
                        ),
                        changes=[
                            {
                                "dimension": "Location",
                                "requested": town.title(),
                                "offered": locations,
                            }
                        ],
                        preserved=["Available vehicle search"],
                    ),
                )
            query["dealershipId"] = matched["id"]

        data = await self.dealership.search_vehicles(query)
        if int(data.get("pagination", {}).get("totalItems", len(data.get("items", [])))) == 0:
            resolved_query = await self._resolve_live_categories(query)
            if resolved_query is not None:
                query = resolved_query
                data = await self.dealership.search_vehicles(query)
        contract = VehicleSearch.model_validate(
            {key: value for key, value in query.items() if key != "pageSize"}
        )
        compliant_records = [
            item for item in data.get("items", []) if self._matches_selection(item, contract)
        ]
        items = [vehicle_item(item) for item in compliant_records]
        upstream_count = int(data.get("pagination", {}).get("totalItems", len(items)))
        violated_contract = len(compliant_records) != len(data.get("items", []))
        total = len(items) if violated_contract else upstream_count
        if violated_contract:
            data = {
                **data,
                "items": compliant_records,
                "pagination": {
                    **dict(data.get("pagination") or {}),
                    "totalItems": total,
                    "totalPages": 1 if total else 0,
                },
                "upstreamConstraintViolation": True,
            }
        filter_summary = vehicle_filter_summary(search_state)
        if total == 0:
            current_filters = filter_summary or "the requested criteria"
            return ToolResult(
                f"I couldn't find any available vehicles matching your current filters: "
                f"{current_filters}. Try changing or clearing a filter.",
                "suggestion_list",
                {
                    "version": 1,
                    "filterSummary": filter_summary,
                    "suggestions": no_vehicle_results_suggestions(search_state),
                },
                {
                    **data,
                    "outcome": "empty",
                    "appliedSearch": search_state,
                    "filterSummary": filter_summary,
                },
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
        displayed_makes = list(
            dict.fromkeys(
                str(item.get("make") or "").strip()
                for item in items
                if str(item.get("make") or "").strip()
            )
        )
        grounded_data = {
            **data,
            # Applied public criteria are authoritative tool output too. Exposing them as facts
            # lets the composer naturally acknowledge a customer's budget/filter without typing
            # an otherwise ungrounded critical value into prose.
            "appliedSearch": search_state,
            "inventorySummary": {
                "matchedResultSet": {
                    "count": total,
                    "scope": "all_matching_inventory",
                },
                "displayedPage": {
                    "page": page,
                    "itemCount": len(items),
                    "makes": displayed_makes,
                    "scope": "displayed_page_only",
                },
            },
        }
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
                "filterSummary": filter_summary,
                "suggestions": suggestions,
            },
            grounded_data,
        )

    async def _resolve_live_categories(self, query: dict[str, Any]) -> dict[str, Any] | None:
        """Expand natural categorical constraints into exact values from live inventory.

        The dealership API applies exact equality to category fields. Customer language may name
        a meaningful family (for example a base colour) that spans several authoritative values.
        Resolve that representation only after the exact query has no matches, preserving the
        customer's original search state and every non-category constraint.
        """

        bindings = (
            ("make", "makes"),
            ("model", "models"),
            ("colour", "colours"),
            ("fuelType", "fuelTypes"),
            ("transmission", "transmissions"),
            ("bodyStyle", "bodyStyles"),
        )
        if not any(query.get(singular) or query.get(plural) for singular, plural in bindings):
            return None
        catalogue_query = {
            "availability": query.get("availability", "available"),
            "pageSize": 100,
        }
        if query.get("dealershipId"):
            catalogue_query["dealershipId"] = query["dealershipId"]
        records: list[dict[str, Any]] = []
        page = 1
        while page <= 20:
            page_query = {
                **catalogue_query,
                **({"page": page} if page > 1 else {}),
            }
            catalogue = await self.dealership.search_vehicles(page_query)
            records.extend(item for item in catalogue.get("items") or [] if isinstance(item, dict))
            total_pages = int(catalogue.get("pagination", {}).get("totalPages", 1))
            if page >= total_pages:
                break
            page += 1
        if not records:
            return None

        expanded = dict(query)
        changed = False
        for singular, plural in bindings:
            requested = expanded.get(plural)
            if not requested and expanded.get(singular):
                requested = [expanded[singular]]
            if not isinstance(requested, list) or not requested:
                continue
            available = list(
                dict.fromkeys(
                    str(record.get(singular) or "").strip()
                    for record in records
                    if str(record.get(singular) or "").strip()
                )
            )
            matches = list(
                dict.fromkeys(
                    candidate
                    for value in requested
                    for candidate in available
                    if _category_value_matches(str(value), candidate)
                )
            )
            if not matches:
                continue
            original = [str(value).casefold() for value in requested]
            canonical = [value.casefold() for value in matches]
            if original == canonical:
                continue
            expanded.pop(singular, None)
            expanded[plural] = matches
            changed = True
        return expanded if changed else None

    @staticmethod
    def _extract_town(query: dict[str, Any]) -> str | None:
        return query.pop("dealershipTown", None)

    async def _select_from_page(self, arguments: dict[str, Any]) -> ToolResult:
        selection = PageVehicleSelection.model_validate(arguments)
        records = await asyncio.gather(
            *(self.dealership.get_vehicle(vehicle_id) for vehicle_id in selection.vehicleIds)
        )
        filtered = [record for record in records if self._matches_selection(record, selection)]
        sort_keys = {
            "newest": lambda item: -int(item.get("year") or 0),
            "priceAsc": lambda item: int(item.get("pricePence") or 0),
            "priceDesc": lambda item: -int(item.get("pricePence") or 0),
            "mileageAsc": lambda item: int(item.get("mileage") or 0),
            "mileageDesc": lambda item: -int(item.get("mileage") or 0),
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
                "suggestions": current_page_vehicle_suggestions(len(items)),
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
            ("colour", selection.colour),
            ("fuelType", selection.fuelType),
            ("transmission", selection.transmission),
            ("bodyStyle", selection.bodyStyle),
            ("availability", selection.availability),
            ("dealershipId", selection.dealershipId),
            ("dealershipTown", selection.dealershipTown),
        ):
            if (
                expected is not None
                and str(record.get(field) or "").casefold() != str(expected).casefold()
            ):
                return False
        for field, included in (
            ("make", selection.makes),
            ("model", selection.models),
            ("colour", selection.colours),
            ("fuelType", selection.fuelTypes),
            ("transmission", selection.transmissions),
            ("bodyStyle", selection.bodyStyles),
        ):
            if included and str(record.get(field) or "").casefold() not in {
                str(value).casefold() for value in included
            }:
                return False
        for field, excluded in (
            ("make", selection.excludedMakes),
            ("model", selection.excludedModels),
            ("fuelType", selection.excludedFuelTypes),
            ("transmission", selection.excludedTransmissions),
            ("bodyStyle", selection.excludedBodyStyles),
        ):
            if excluded and str(record.get(field) or "").casefold() in {
                str(value).casefold() for value in excluded
            }:
                return False
        numeric_limits = (
            ("pricePence", selection.minPricePence, lambda actual, limit: actual < limit),
            ("pricePence", selection.maxPricePence, lambda actual, limit: actual > limit),
            ("mileage", selection.minMileage, lambda actual, limit: actual < limit),
            ("mileage", selection.maxMileage, lambda actual, limit: actual > limit),
            ("year", selection.minYear, lambda actual, limit: actual < limit),
            ("year", selection.maxYear, lambda actual, limit: actual > limit),
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

    async def _resolve_availability(self, arguments: dict[str, Any]) -> ToolResult:
        """Resolve a natural vehicle description before checking authoritative live status."""

        request = VehicleIdentityQuery.model_validate(arguments)
        query = request.model_dump(exclude_none=True)
        town = query.pop("dealershipTown", None)
        if town and not query.get("dealershipId"):
            dealerships = await self.dealership.list_dealerships()
            matched = dealership_in_town(dealerships.get("items", []), town)
            if matched is None:
                return ToolResult(
                    f"Northstar does not currently have a dealership in {town.title()}.",
                    "suggestion_list",
                    {"version": 1, "suggestions": unknown_dealership_suggestions()},
                    {"outcome": "unavailable", "requestedTown": town, "items": []},
                )
            query["dealershipId"] = matched["id"]
        query["pageSize"] = 50
        data = await self.dealership.search_vehicles(query)
        records = list(data.get("items") or [])
        if not records:
            return ToolResult(
                "I couldn't find a current vehicle matching that description.",
                "suggestion_list",
                {"version": 1, "suggestions": no_vehicle_results_suggestions()},
                {"outcome": "unavailable", "items": []},
            )
        if len(records) > 1:
            candidates = records[:4]
            return ToolResult(
                "I found more than one vehicle matching that description. Which one did you mean?",
                "vehicle_list",
                {
                    "version": 1,
                    "items": [vehicle_item(item) for item in candidates],
                    "total": len(records),
                    "page": 1,
                    "pageSize": len(candidates),
                    "scope": "availabilityCandidates",
                    "suggestions": [],
                },
                {
                    "resolution": {"status": "ambiguous", "purpose": "vehicle_availability"},
                    "items": candidates,
                },
            )
        return await vehicle_availability_result(self.dealership, str(records[0]["id"]))

    async def _compare(self, arguments: dict[str, Any]) -> ToolResult:
        comparison = VehicleComparisonRuntime.model_validate(arguments)
        if any(not re.fullmatch(r"veh-[0-9]{3}", item) for item in comparison.vehicleIds):
            raise ValueError("invalid vehicle ID")
        vehicle_ids = list(dict.fromkeys(comparison.vehicleIds))
        if len(vehicle_ids) < 2:
            return _unresolved_comparison([])
        records = [await self.dealership.get_vehicle(vehicle_id) for vehicle_id in vehicle_ids]
        return _comparison_result(
            records,
            f"Compared {len(records)} vehicles using current stock details.",
            selection_basis=comparison.selectionBasis,
        )

    async def _compare_models(self, arguments: dict[str, Any]) -> ToolResult:
        request = VehicleModelComparison.model_validate(arguments)
        searches = await asyncio.gather(
            *(
                self.dealership.search_vehicles(
                    {
                        "q": query,
                        "availability": "available",
                        "pageSize": 50,
                        "sort": "newest",
                    }
                )
                for query in request.queries
            )
        )
        resolved: list[dict[str, Any] | None] = []
        first_ambiguity: tuple[int, str, list[dict[str, Any]]] | None = None
        for index, (query, data) in enumerate(zip(request.queries, searches, strict=True)):
            candidates = _model_identity_candidates(data.get("items", []), query)
            if not candidates:
                return _unavailable_model_comparison(request.queries, query)
            if len(candidates) > 1:
                resolved.append(None)
                if first_ambiguity is None:
                    first_ambiguity = (index, query, candidates[:4])
                continue
            resolved.append(candidates[0])

        if first_ambiguity is not None:
            index, query, candidates = first_ambiguity
            return ToolResult(
                "More than one current model matches part of the comparison.",
                "vehicle_list",
                {
                    "version": 1,
                    "items": [vehicle_item(item) for item in candidates],
                    "total": len(candidates),
                    "page": 1,
                    "pageSize": len(candidates),
                    "scope": "comparisonCandidates",
                    "suggestions": vehicle_resolution_suggestions(candidates),
                },
                {
                    "resolution": {
                        "status": "ambiguous",
                        "queries": list(request.queries),
                        "pendingQuery": query,
                        "pendingIndex": index,
                        "resolvedVehicleIds": [
                            str(item.get("id")) if item is not None else None for item in resolved
                        ],
                        "selectionBasis": (
                            "Newest currently available match for each named model; "
                            "the remaining model needs your choice."
                        ),
                    },
                    "items": candidates,
                },
            )

        records = [item for item in resolved if item is not None]
        if len({str(item.get("id")) for item in records}) < 2:
            return _unresolved_comparison(records, resolve_models=True)
        return _comparison_result(
            records,
            f"Compared {len(records)} current vehicles.",
            selection_basis="Newest currently available match for each named model.",
        )

    async def _preferences(self, arguments: dict[str, Any]) -> ToolResult:
        request = VehiclePreferenceRequest.model_validate(arguments)
        if request.dimension == "startingPoint":
            suggestions = vehicle_starting_point_suggestions()
            text = "Tell me what matters most, or choose a starting point below."
        else:
            facets = await self._facets({})
            suggestions = vehicle_facet_suggestions(
                request.dimension, facets.facts.get(request.dimension, [])
            )
            text = vehicle_facet_prompt(request.dimension, suggestions)
        return ToolResult(
            text,
            "suggestion_list",
            {"version": 1, "suggestions": suggestions},
            {"dimension": request.dimension},
        )


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
        {"version": 1, "suggestions": vehicle_comparison_suggestions(records)},
        {"items": records},
    )


def _comparison_result(
    records: list[dict[str, Any]],
    text: str,
    *,
    selection_basis: str | None = None,
) -> ToolResult:
    comparison: dict[str, Any] = {
        "vehicleIds": [str(item.get("id")) for item in records],
        "dimensions": [
            "pricePence",
            "monthlyPricePence",
            "mileage",
            "fuelType",
            "transmission",
            "bodyStyle",
            "year",
        ],
    }
    if selection_basis:
        comparison["selectionBasis"] = selection_basis
    return ToolResult(
        text,
        "vehicle_comparison",
        {
            "version": 1,
            "items": [vehicle_item(item) for item in records],
            "suggestions": vehicle_comparison_suggestions(records),
        },
        {"items": records, "comparison": comparison},
    )


def _model_identity_candidates(records: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Resolve a query by make/model identity before considering variant text.

    This is authoritative entity matching over returned inventory, not public intent parsing. It
    prevents a derivative word such as ``Cooper`` on a Countryman from replacing the actual Cooper
    model requested by the planner.
    """

    query_tokens = set(_identity_tokens(query))
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    fallback: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        identity = (str(record.get("make") or ""), str(record.get("model") or ""))
        if not all(identity):
            continue
        identity_tokens = set(_identity_tokens(" ".join(identity)))
        searchable_tokens = set(
            _identity_tokens(
                " ".join(
                    str(record.get(field) or "")
                    for field in ("make", "model", "variant", "year", "colour", "bodyStyle")
                )
            )
        )
        if query_tokens and (
            query_tokens.issubset(identity_tokens) or identity_tokens.issubset(query_tokens)
        ):
            groups.setdefault(identity, []).append(record)
        elif query_tokens and query_tokens.issubset(searchable_tokens):
            fallback.setdefault(identity, []).append(record)
    selected = groups or fallback
    return sorted(
        (_representative(items) for items in selected.values()),
        key=lambda item: (
            str(item.get("make") or "").casefold(),
            str(item.get("model") or "").casefold(),
        ),
    )


def _representative(records: list[dict[str, Any]]) -> dict[str, Any]:
    return min(
        records,
        key=lambda item: (
            -int(item.get("year") or 0),
            int(item.get("mileage") or 0),
            int(item.get("pricePence") or 0),
            str(item.get("id") or ""),
        ),
    )


def _identity_tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.casefold())


def _category_value_matches(requested: str, candidate: str) -> bool:
    requested_tokens = set(_identity_tokens(requested))
    candidate_tokens = set(_identity_tokens(candidate))
    return bool(requested_tokens) and requested_tokens.issubset(candidate_tokens)


def _unavailable_model_comparison(queries: list[str], unresolved: str) -> ToolResult:
    return ToolResult(
        "One of the requested vehicle descriptions has no current available match.",
        "suggestion_list",
        {"version": 1, "suggestions": []},
        {
            "resolution": {
                "status": "unavailable",
                "queries": list(queries),
                "unresolvedQuery": unresolved,
            },
            "items": [],
        },
    )
