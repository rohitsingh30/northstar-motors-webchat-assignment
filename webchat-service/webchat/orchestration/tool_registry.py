from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from webchat.domain.business_semantics import availability_text, money
from webchat.integrations.dealership import DealershipClient
from webchat.orchestration.service_resolution import match_live_service
from webchat.orchestration.suggestions import (
    dealership_suggestions,
    no_vehicle_results_suggestions,
    offer_suggestions,
    opening_hours_suggestions,
    service_type_suggestions,
    unknown_dealership_suggestions,
    vehicle_availability_suggestions,
    vehicle_comparison_suggestions,
    vehicle_search_suggestions,
    workshop_location_suggestions,
    workshop_no_availability_suggestions,
)


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VehicleSearch(ToolInput):
    page: int = Field(default=1, ge=1, le=100)
    q: str | None = Field(default=None, max_length=100)
    make: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    fuelType: str | None = Field(default=None, max_length=40)
    transmission: str | None = Field(default=None, max_length=40)
    bodyStyle: str | None = Field(default=None, max_length=40)
    availability: Literal["available", "reserved", "sold"] | None = None
    dealershipId: str | None = Field(default=None, max_length=80)
    dealershipTown: str | None = Field(default=None, max_length=80)
    minPricePence: int | None = Field(default=None, ge=0)
    maxPricePence: int | None = Field(default=None, ge=0)
    maxMileage: int | None = Field(default=None, ge=0)
    minYear: int | None = Field(default=None, ge=1900, le=2100)
    sort: Literal["newest", "priceAsc", "priceDesc", "mileageAsc"] = "newest"


class PageVehicleSelection(ToolInput):
    vehicleIds: list[str] = Field(min_length=1, max_length=50)
    q: str | None = Field(default=None, max_length=100)
    make: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    fuelType: str | None = Field(default=None, max_length=40)
    transmission: str | None = Field(default=None, max_length=40)
    bodyStyle: str | None = Field(default=None, max_length=40)
    availability: Literal["available", "reserved", "sold"] | None = None
    dealershipId: str | None = Field(default=None, max_length=80)
    dealershipTown: str | None = Field(default=None, max_length=80)
    minPricePence: int | None = Field(default=None, ge=0)
    maxPricePence: int | None = Field(default=None, ge=0)
    maxMileage: int | None = Field(default=None, ge=0)
    minYear: int | None = Field(default=None, ge=1900, le=2100)
    sort: Literal["newest", "priceAsc", "priceDesc", "mileageAsc"] = "newest"
    limit: int = Field(default=3, ge=1, le=12)

    @field_validator("vehicleIds")
    @classmethod
    def valid_unique_vehicle_ids(cls, values):
        if any(not re.fullmatch(r"veh-[0-9]{3}", value) for value in values):
            raise ValueError("invalid vehicle ID")
        return list(dict.fromkeys(values))


class StableId(ToolInput):
    id: str = Field(max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class VehicleComparison(ToolInput):
    vehicleIds: list[str] = Field(min_length=2, max_length=3)


class VehicleModelComparison(ToolInput):
    """Natural-language comparison requests resolved against live stock."""

    queries: list[str] = Field(min_length=2, max_length=3)


class Filters(ToolInput):
    dealershipId: str | None = Field(default=None, max_length=80)
    vehicleId: str | None = Field(default=None, pattern=r"^veh-[0-9]{3}$")
    serviceTypeId: str | None = Field(default=None, max_length=80)
    serviceTypeName: str | None = Field(default=None, max_length=80)
    dealershipTown: str | None = Field(default=None, max_length=80)
    dateFrom: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    dateTo: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    make: str | None = Field(default=None, max_length=60)
    productType: Literal["PCP", "PCH"] | None = None
    workflowMode: Literal["booking", "amendment"] | None = None


class PartExchangeEstimate(ToolInput):
    registration: str = Field(min_length=2, max_length=12)
    mileage: int = Field(ge=0, le=1_000_000)
    condition: Literal["excellent", "good", "fair"]


class PartExchangeEstimateForm(ToolInput):
    registration: str | None = Field(default=None, min_length=2, max_length=12)
    mileage: int | None = Field(default=None, ge=0, le=1_000_000)
    condition: Literal["excellent", "good", "fair"] | None = None


class OpeningHoursQuery(ToolInput):
    day: Literal[
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ] | None = None
    town: str | None = Field(default=None, max_length=80)
    department: Literal["sales", "service", "parts"] | None = None


class DealershipQuery(ToolInput):
    town: str | None = Field(default=None, max_length=80)


class ServiceQuery(ToolInput):
    q: str | None = Field(default=None, min_length=1, max_length=200)
    serviceTypeId: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def has_service_reference(self):
        if self.q is None and self.serviceTypeId is None:
            raise ValueError("a service query or service type ID is required")
        return self


class BookingLookupForm(ToolInput):
    mode: Literal["lookup", "amend", "cancel"] = "lookup"


@dataclass(frozen=True)
class ToolResult:
    text: str
    view_type: str | None
    view_payload: dict[str, Any] | None
    facts: dict[str, Any]


def _live_numeric_choices(values: set[int], *, rounding: int) -> list[int]:
    """Create up to four useful thresholds from the live inventory distribution."""
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


class ToolRegistry:
    READ_TOOLS: ClassVar[set[str]] = {
        "search_vehicles",
        "select_page_vehicles",
        "get_vehicle",
        "get_vehicle_availability",
        "get_vehicle_facets",
        "compare_vehicles",
        "compare_vehicle_models",
        "list_offers",
        "get_offer",
        "list_dealerships",
        "list_dealership_departments",
        "get_dealership",
        "get_opening_hours",
        "list_opening_hours",
        "get_service_information",
        "list_service_types",
        "list_test_drive_slots",
        "list_workshop_locations",
        "list_workshop_slots",
        "get_business_information",
        "request_part_exchange_estimate_form",
        "estimate_part_exchange",
    }

    WORKFLOW_TO_KIND: ClassVar[dict[str, str]] = {
        "prepare_sales_enquiry": "sales_enquiry",
        "prepare_test_drive": "test_drive",
        "prepare_vehicle_interest": "vehicle_interest",
        "prepare_callback": "callback",
        "prepare_workshop_booking": "workshop_booking",
        "prepare_workshop_amendment": "workshop_amend",
        "prepare_workshop_cancellation": "workshop_cancel",
        "prepare_dealership_message": "dealership_message",
        "prepare_part_exchange": "part_exchange",
    }

    def __init__(self, dealership: DealershipClient, workflows=None):
        self.dealership = dealership
        self.workflows = workflows

    async def execute(
        self, name: str, arguments: dict[str, Any], conversation_id: str | None = None
    ) -> ToolResult:
        if name == "request_workshop_booking_lookup_form":
            request = BookingLookupForm.model_validate(arguments)
            descriptions = {
                "lookup": "Enter all four booking details to view the appointment securely.",
                "amend": "Enter all four booking details to continue directly to the change form.",
                "cancel": "Enter all four booking details to review the cancellation securely.",
            }
            return ToolResult(
                descriptions[request.mode],
                "private_booking_lookup",
                {"version": 1, "mode": request.mode},
                {"lookupFormRequested": True, "mode": request.mode},
            )
        if name == "request_part_exchange_estimate_form":
            known = PartExchangeEstimateForm.model_validate(arguments).model_dump(
                exclude_none=True
            )
            return ToolResult(
                "Enter the remaining vehicle details in the form below.",
                "part_exchange_estimate_form",
                {"version": 1, "values": known},
                {"estimateFormRequested": True, "values": known},
            )
        if name == "estimate_part_exchange":
            estimate = PartExchangeEstimate.model_validate(arguments)
            data = await self.dealership.estimate_part_exchange(estimate.model_dump())
            return ToolResult(
                "Here is the platform's indicative part-exchange range.",
                "part_exchange_estimate",
                {"version": 1, **data},
                data,
            )
        if name in self.WORKFLOW_TO_KIND:
            if self.workflows is None or conversation_id is None:
                raise ValueError("Workflow tools require a conversation")
            draft = self.workflows.prepare(
                conversation_id, self.WORKFLOW_TO_KIND[name], arguments
            )
            payload = {
                "version": 1,
                "draftId": draft.id,
                "kind": draft.kind,
                "status": draft.status,
                "missingFields": draft.missing_fields,
                "summary": draft.summary,
            }
            business = await self.dealership.get_business_information()
            payload["privacyContact"] = business.get("privacyContact")
            if draft.kind == "vehicle_interest" and draft.summary.get("vehicleId"):
                vehicle = await self.dealership.get_vehicle(draft.summary["vehicleId"])
                payload["vehicle"] = {
                    field: vehicle.get(field)
                    for field in (
                        "id",
                        "year",
                        "make",
                        "model",
                        "variant",
                        "pricePence",
                        "mileage",
                        "fuelType",
                        "transmission",
                        "availability",
                        "dealershipTown",
                    )
                }
            if draft.kind in {
                "part_exchange",
                "callback",
                "sales_enquiry",
                "dealership_message",
            }:
                dealerships = await self.dealership.list_dealerships()
                payload["dealerships"] = [
                    {
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "town": item.get("town"),
                    }
                    for item in dealerships.get("items", [])
                ]
            return ToolResult(
                "Please review and confirm these details."
                if draft.status == "awaiting_confirmation"
                else "I need a few more details before asking for confirmation.",
                "confirmation" if draft.status == "awaiting_confirmation" else "draft",
                payload,
                payload,
            )
        if name not in self.READ_TOOLS:
            raise ValueError(f"Unknown or disallowed tool: {name}")

        if name == "search_vehicles":
            query = VehicleSearch.model_validate(arguments).model_dump(exclude_none=True)
            # Retain validated search state so later natural-language and typed
            # follow-ups continue this result set instead of reconstructing it.
            search_state = {
                key: value
                for key, value in query.items()
                if key != "page"
            }
            query["pageSize"] = 3
            # Discovery defaults to actionable stock, while an explicit reserved/sold
            # request remains visible so status questions are answered truthfully.
            query.setdefault("availability", "available")
            town = query.pop("dealershipTown", None)
            if not town and query.get("q"):
                location_match = re.search(
                    r"\b(?:in|at|near)\s+([a-z][a-z -]{1,60})",
                    str(query["q"]),
                    re.IGNORECASE,
                )
                if location_match:
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
            if town and not query.get("dealershipId"):
                dealerships = await self.dealership.list_dealerships()
                normalized_town = re.sub(r"[^a-z0-9]+", " ", town.lower()).strip()
                matched = next(
                    (
                        dealership
                        for dealership in dealerships.get("items", [])
                        if re.sub(
                            r"[^a-z0-9]+",
                            " ",
                            str(dealership.get("town", "")).lower(),
                        ).strip()
                        == normalized_town
                    ),
                    None,
                )
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
                    f"I couldn't find any available vehicles matching {requested}. Try another make, model, budget, or location.",
                    "suggestion_list",
                    {"version": 1, "suggestions": no_vehicle_results_suggestions()},
                    data,
                )
            text = f"Found {total} matching vehicle{'s' if total != 1 else ''}."
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
                text,
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

        if name == "select_page_vehicles":
            selection = PageVehicleSelection.model_validate(arguments)
            records = await asyncio.gather(
                *(self.dealership.get_vehicle(vehicle_id) for vehicle_id in selection.vehicleIds)
            )

            def same(left, right) -> bool:
                return str(left or "").casefold() == str(right or "").casefold()

            def includes(record, query: str) -> bool:
                searchable = " ".join(
                    str(record.get(field) or "")
                    for field in ("make", "model", "variant", "bodyStyle", "colour", "fuelType")
                ).casefold()
                return all(token in searchable for token in query.casefold().split())

            filtered = []
            for record in records:
                if selection.q and not includes(record, selection.q):
                    continue
                if any(
                    expected is not None and not same(record.get(field), expected)
                    for field, expected in (
                        ("make", selection.make),
                        ("model", selection.model),
                        ("fuelType", selection.fuelType),
                        ("transmission", selection.transmission),
                        ("bodyStyle", selection.bodyStyle),
                        ("availability", selection.availability),
                        ("dealershipId", selection.dealershipId),
                        ("dealershipTown", selection.dealershipTown),
                    )
                ):
                    continue
                if selection.minPricePence is not None and int(record.get("pricePence") or 0) < selection.minPricePence:
                    continue
                if selection.maxPricePence is not None and int(record.get("pricePence") or 0) > selection.maxPricePence:
                    continue
                if selection.maxMileage is not None and int(record.get("mileage") or 0) > selection.maxMileage:
                    continue
                if selection.minYear is not None and int(record.get("year") or 0) < selection.minYear:
                    continue
                filtered.append(record)

            sort_keys = {
                "newest": lambda item: -int(item.get("year") or 0),
                "priceAsc": lambda item: int(item.get("pricePence") or 0),
                "priceDesc": lambda item: -int(item.get("pricePence") or 0),
                "mileageAsc": lambda item: int(item.get("mileage") or 0),
            }
            filtered.sort(key=sort_keys[selection.sort])
            total = len(filtered)
            items = [vehicle_item(item) for item in filtered[:selection.limit]]
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
                else f"Showing {len(items)} matching vehicle{'s' if len(items) != 1 else ''} from the current page."
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

        if name == "get_vehicle_facets":
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
                        value = vehicle.get(source)
                        if value:
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

        if name == "get_service_information":
            query = ServiceQuery.model_validate(arguments)
            data = await self.dealership.list_service_types()
            matched = next(
                (
                    item
                    for item in data.get("items", [])
                    if query.serviceTypeId is not None
                    and str(item.get("id")) == query.serviceTypeId
                ),
                None,
            )
            if matched is None and query.q is not None:
                matched = match_live_service(data.get("items", []), query.q)
            if matched is None:
                items = data.get("items", [])
                return ToolResult(
                    "Please choose which workshop service you mean.",
                    "service_list",
                    {
                        "version": 1,
                        "items": items,
                        "suggestions": service_type_suggestions(items),
                    },
                    {"items": items},
                )
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
                {"service": matched},
            )

        if name == "get_vehicle":
            stable_id = StableId.model_validate(arguments).id
            data = await self.dealership.get_vehicle(stable_id)
            item = vehicle_item(data)
            return ToolResult(
                f"{item['make']} {item['model']}: {item['price']}.",
                "vehicle_list",
                {"version": 1, "items": [item], "suggestions": []},
                data,
            )

        if name == "get_vehicle_availability":
            stable_id = StableId.model_validate(arguments).id
            data = await self.dealership.get_vehicle_availability(stable_id)
            vehicle = vehicle_item(await self.dealership.get_vehicle(stable_id))
            return ToolResult(
                availability_text(str(data.get("availability"))),
                "vehicle_availability",
                {
                    "version": 1,
                    **data,
                    "vehicle": vehicle,
                    "suggestions": vehicle_availability_suggestions(data),
                },
                data,
            )

        if name == "compare_vehicles":
            comparison = VehicleComparison.model_validate(arguments)
            if any(not re.fullmatch(r"veh-[0-9]{3}", item) for item in comparison.vehicleIds):
                raise ValueError("invalid vehicle ID")
            vehicle_ids = list(dict.fromkeys(comparison.vehicleIds))
            if len(vehicle_ids) < 2:
                return ToolResult(
                    "Please choose at least two different vehicles to compare.",
                    "suggestion_list",
                    {
                        "version": 1,
                        "suggestions": vehicle_comparison_suggestions(),
                    },
                    {"items": []},
                )
            records = [await self.dealership.get_vehicle(item) for item in vehicle_ids]
            items = [vehicle_item(item) for item in records]
            facts = {"items": records}
            return ToolResult(
                f"Compared {len(items)} vehicles using current stock details.",
                "vehicle_comparison",
                {
                    "version": 1,
                    "items": items,
                    "suggestions": vehicle_comparison_suggestions(),
                },
                facts,
            )

        if name == "compare_vehicle_models":
            request = VehicleModelComparison.model_validate(arguments)
            records: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            for query in request.queries:
                data = await self.dealership.search_vehicles(
                    {"q": query, "availability": "available", "pageSize": 1, "sort": "priceAsc"}
                )
                item = next(iter(data.get("items", [])), None)
                item_id = str(item.get("id")) if item else ""
                if item and item_id not in seen_ids:
                    records.append(item)
                    seen_ids.add(item_id)
            if len(records) < 2:
                return ToolResult(
                    "I couldn't resolve at least two different available vehicles from those descriptions.",
                    "suggestion_list",
                    {
                        "version": 1,
                        "suggestions": vehicle_comparison_suggestions(),
                    },
                    {"items": records},
                )
            items = [vehicle_item(item) for item in records]
            return ToolResult(
                f"Compared {len(items)} current vehicles.",
                "vehicle_comparison",
                {
                    "version": 1,
                    "items": items,
                    "suggestions": vehicle_comparison_suggestions(),
                },
                {"items": records},
            )

        if name in {"list_offers", "list_test_drive_slots", "list_workshop_slots"}:
            filters = Filters.model_validate(arguments).model_dump(exclude_none=True)
            if name == "list_offers":
                data = await self.dealership.list_offers(filters)
                business = await self.dealership.get_business_information()
                finance_notice = business.get("finance", {}).get("notice")
                view_type = "offer_list"
            elif name == "list_test_drive_slots":
                availability = await self.dealership.get_vehicle_availability(
                    str(filters["vehicleId"])
                )
                if availability.get("availability") != "available":
                    vehicle = vehicle_item(
                        await self.dealership.get_vehicle(str(filters["vehicleId"]))
                    )
                    return ToolResult(
                        availability_text(str(availability.get("availability"))),
                        "vehicle_availability",
                        {
                            "version": 1,
                            **availability,
                            "vehicle": vehicle,
                            "suggestions": vehicle_availability_suggestions(availability),
                        },
                        availability,
                    )
                data = await self.dealership.list_test_drive_slots(filters)
                view_type = "test_drive_slot_picker"
            else:
                workflow_mode = filters.pop("workflowMode", "booking")
                service_name = filters.pop("serviceTypeName", None)
                service_label = service_name
                town = filters.pop("dealershipTown", None)
                if town and not filters.get("dealershipId"):
                    dealerships = await self.dealership.list_dealerships()
                    normalized_town = re.sub(r"[^a-z0-9]+", " ", town.lower()).strip()
                    match = next(
                        (
                            item for item in dealerships.get("items", [])
                            if re.sub(r"[^a-z0-9]+", " ", str(item.get("town", "")).lower()).strip()
                            == normalized_town
                        ),
                        None,
                    )
                    if not match:
                        return ToolResult(
                            f"Northstar does not currently have a workshop in {town.title()}.",
                            "suggestion_list",
                            {
                                "version": 1,
                                "suggestions": unknown_dealership_suggestions(),
                            },
                            {"requestedTown": town, "items": []},
                        )
                    filters["dealershipId"] = match["id"]
                if not service_name and not filters.get("serviceTypeId"):
                    service_types = await self.dealership.list_service_types()
                    items = service_types.get("items", [])
                    return ToolResult(
                        "Please choose a workshop service before selecting a time.",
                        "service_list",
                        {
                            "version": 1,
                            "items": items,
                            "suggestions": service_type_suggestions(items),
                        },
                        {"items": items},
                    )
                if service_name and not filters.get("serviceTypeId"):
                    service_types = await self.dealership.list_service_types()
                    candidates = service_types.get("items", [])
                    match = match_live_service(candidates, service_name)
                    if match is None:
                        return ToolResult(
                            "Please choose which workshop service you mean.",
                            "service_list",
                            {
                                "version": 1,
                                "items": candidates,
                                "suggestions": service_type_suggestions(candidates),
                            },
                            {"items": candidates},
                        )
                    filters["serviceTypeId"] = match["id"]
                    service_label = str(match.get("name") or service_name)
                filters.setdefault("dateFrom", datetime.now(UTC).date().isoformat())
                data = await self.dealership.list_workshop_slots(filters)
                now = datetime.now(UTC)
                data["items"] = [
                    item
                    for item in data.get("items", [])
                    if item.get("startsAt")
                    and datetime.fromisoformat(str(item["startsAt"])) > now
                ]
                alternative_slots: list[dict[str, Any]] = []
                if not data["items"]:
                    alternative_filters = {
                        key: value for key, value in filters.items() if key != "dealershipId"
                    }
                    alternative_data = await self.dealership.list_workshop_slots(
                        alternative_filters
                    )
                    alternative_slots = [
                        item
                        for item in alternative_data.get("items", [])
                        if item.get("startsAt")
                        and datetime.fromisoformat(str(item["startsAt"])) > now
                    ]
                view_type = "slot_list"
            items = data.get("items", [])
            payload = {"version": 1, "items": items}
            if name == "list_test_drive_slots":
                payload["vehicleId"] = filters.get("vehicleId")
                if filters.get("vehicleId"):
                    payload["vehicle"] = vehicle_item(
                        await self.dealership.get_vehicle(filters["vehicleId"])
                    )
                if not items:
                    payload["suggestions"] = vehicle_availability_suggestions(
                        availability
                    )
            if name == "list_offers":
                payload["financeNotice"] = finance_notice
                payload["suggestions"] = offer_suggestions(
                    items[0] if len(items) == 1 else None
                )
            if name == "list_workshop_slots":
                payload["mode"] = workflow_mode
            if name == "list_workshop_slots" and not items:
                payload["suggestions"] = workshop_no_availability_suggestions(
                    alternative_slots, service_label, filters.get("serviceTypeId")
                )
            return ToolResult(
                f"Found {len(items)} result{'s' if len(items) != 1 else ''}.",
                view_type,
                payload,
                data,
            )

        if name == "list_opening_hours":
            query = OpeningHoursQuery.model_validate(arguments)
            dealerships = await self.dealership.list_dealerships()
            dealership_items = dealerships.get("items", [])
            if query.town:
                normalized_town = re.sub(r"[^a-z0-9]+", " ", query.town.lower()).strip()
                dealership_items = [
                    item
                    for item in dealership_items
                    if re.sub(
                        r"[^a-z0-9]+", " ", str(item.get("town", "")).lower()
                    ).strip()
                    == normalized_town
                ]
                if not dealership_items:
                    towns = ", ".join(
                        str(item.get("town"))
                        for item in dealerships.get("items", [])
                        if item.get("town")
                    )
                    return ToolResult(
                        f"Northstar does not currently have a dealership in {query.town.title()}. Our locations are {towns}.",
                        "suggestion_list",
                        {
                            "version": 1,
                            "suggestions": unknown_dealership_suggestions(),
                        },
                        {"requestedTown": query.town, "items": []},
                    )
            items = []
            for dealership in dealership_items:
                hours = await self.dealership.get_opening_hours(str(dealership["id"]))
                weekly = [
                    entry
                    for entry in hours.get("weekly", [])
                    if (query.day is None or entry.get("day") == query.day)
                    and (
                        query.department is None
                        or str(entry.get("department", "")).lower() == query.department
                    )
                ]
                departments = [
                    {
                        "name": str(entry.get("department", "")).title(),
                        "day": entry.get("day"),
                        "opensAt": entry.get("opensAt"),
                        "closesAt": entry.get("closesAt"),
                        "closed": bool(entry.get("closed")),
                    }
                    for entry in weekly
                ]
                items.append(
                    {
                        "name": dealership.get("name"),
                        "town": dealership.get("town"),
                        "day": query.day or "Weekly",
                        "departments": departments,
                        "holidayExceptions": [
                            exception
                            for exception in hours.get("holidayExceptions", [])
                            if query.department is None
                            or str(exception.get("department", "")).lower()
                            == query.department
                        ],
                    }
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

        if name in {"get_offer", "get_dealership", "get_opening_hours"}:
            stable_id = StableId.model_validate(arguments).id
            if name == "get_offer":
                data = await self.dealership.get_offer(stable_id)
                view_type = "offer_list"
                suggestions = offer_suggestions(data)
            elif name == "get_dealership":
                data = await self.dealership.get_dealership(stable_id)
                view_type = "dealership_list"
                suggestions = dealership_suggestions()
            else:
                hours = await self.dealership.get_opening_hours(stable_id)
                dealership = await self.dealership.get_dealership(stable_id)
                data = {
                    "name": dealership.get("name"),
                    "town": dealership.get("town"),
                    "day": "Weekly",
                    "departments": [
                        {
                            "name": str(entry.get("department", "")).title(),
                            "day": entry.get("day"),
                            "opensAt": entry.get("opensAt"),
                            "closesAt": entry.get("closesAt"),
                            "closed": bool(entry.get("closed")),
                        }
                        for entry in hours.get("weekly", [])
                    ],
                    "holidayExceptions": hours.get("holidayExceptions", []),
                }
                view_type = "opening_hours"
                suggestions = opening_hours_suggestions()
            return ToolResult(
                "Here are the current platform details.",
                view_type,
                {"version": 1, "items": [data], "suggestions": suggestions},
                data,
            )

        if name == "list_dealerships":
            data = await self.dealership.list_dealerships()
            query = DealershipQuery.model_validate(arguments)
            items = data.get("items", [])
            if query.town:
                normalized_town = re.sub(r"[^a-z0-9]+", " ", query.town.lower()).strip()
                items = [
                    item for item in items
                    if re.sub(r"[^a-z0-9]+", " ", str(item.get("town", "")).lower()).strip()
                    == normalized_town
                ]
                if not items:
                    towns = ", ".join(str(item.get("town")) for item in data.get("items", []) if item.get("town"))
                    return ToolResult(
                        f"Northstar does not currently have a dealership in {query.town.title()}. Our locations are {towns}.",
                        "suggestion_list",
                        {"version": 1, "suggestions": unknown_dealership_suggestions()},
                        {"requestedTown": query.town, "items": []},
                    )
            return ToolResult(
                "Here are our dealerships.",
                "dealership_list",
                {"version": 1, "items": items, "suggestions": dealership_suggestions()},
                {"items": items},
            )
        if name == "list_dealership_departments":
            dealerships = await self.dealership.list_dealerships()
            items = []
            for dealership in dealerships.get("items", []):
                hours = await self.dealership.get_opening_hours(str(dealership["id"]))
                departments = sorted({
                    str(entry.get("department"))
                    for entry in hours.get("weekly", [])
                    if entry.get("department")
                })
                items.append({
                    "id": dealership.get("id"),
                    "name": dealership.get("name"),
                    "town": dealership.get("town"),
                    "departments": departments,
                })
            return ToolResult(
                "Here are the departments available at each dealership.",
                "dealership_list",
                {"version": 1, "items": items, "suggestions": dealership_suggestions()},
                {"items": items},
            )
        if name == "list_workshop_locations":
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
        if name == "list_service_types":
            data = await self.dealership.list_service_types()
            return ToolResult(
                "Here are our supported services.",
                "service_list",
                {
                    "version": 1,
                    "items": data.get("items", []),
                    "suggestions": service_type_suggestions(data.get("items", [])),
                },
                data,
            )

        data = await self.dealership.get_business_information()
        return ToolResult("Here is the current Northstar business information.", "business_information", {"version": 1, **data}, data)
