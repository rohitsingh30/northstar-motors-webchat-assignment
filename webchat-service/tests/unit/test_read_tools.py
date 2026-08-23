from datetime import UTC, datetime, timedelta

import pytest

from webchat.orchestration.tools.registry import ToolRegistry


class FakeDealership:
    def __init__(self):
        self.last_search_filters = None
        self.last_workshop_filters = None

    async def search_vehicles(self, filters):
        self.last_search_filters = filters
        if filters["pageSize"] == 50:
            assert filters["availability"] == "available"
            return {
                "items": [
                    {
                        "make": "Dynamic Make",
                        "model": "Dynamic Model",
                        "fuelType": "Future Fuel",
                        "transmission": "Direct",
                        "bodyStyle": "Crossover",
                    }
                ],
                "pagination": {"totalPages": 1},
            }
        assert filters["pageSize"] in {1, 3}
        assert filters["availability"] in {"available", "reserved", "sold"}
        return {
            "items": [
                {
                    "id": "veh-019",
                    "make": "Volvo",
                    "model": "XC40",
                    "pricePence": None,
                    "images": ["http://platform/assets/vehicles/veh-019.jpg"],
                    "availability": "available",
                }
            ],
            "pagination": {
                "page": filters.get("page", 1),
                "pageSize": filters["pageSize"],
                "totalItems": 17,
            },
        }

    async def list_dealerships(self):
        return {
            "items": [
                {"id": "dealer-manchester", "town": "Manchester"},
                {"id": "dealer-stockport", "town": "Stockport"},
            ]
        }

    async def get_dealership(self, dealership_id):
        return next(
            item
            for item in (await self.list_dealerships())["items"]
            if item["id"] == dealership_id
        )

    async def get_opening_hours(self, dealership_id):
        return {
            "weekly": [
                {"department": "sales", "day": "Saturday", "opensAt": "09:00", "closesAt": "17:00", "closed": False},
                {"department": "service", "day": "Saturday", "opensAt": "09:00", "closesAt": "13:00", "closed": False},
                {"department": "parts", "day": "Saturday", "opensAt": "09:00", "closesAt": "13:00", "closed": False},
            ],
            "holidayExceptions": [
                {"department": "sales", "date": "2026-08-31", "label": "Bank holiday", "opensAt": "10:00", "closesAt": "16:00", "closed": False},
                {"department": "service", "date": "2026-08-31", "label": "Bank holiday", "opensAt": None, "closesAt": None, "closed": True},
            ],
        }

    async def get_vehicle(self, vehicle_id):
        return {
            "id": vehicle_id,
            "make": "Volvo",
            "model": "XC40",
            "pricePence": 3_000_000,
            "images": [],
            "availability": "available",
        }

    async def get_vehicle_availability(self, vehicle_id):
        return {
            "vehicleId": vehicle_id,
            "availability": "available",
            "canEnquire": True,
            "canBookTestDrive": True,
            "canRegisterInterest": False,
        }

    async def list_test_drive_slots(self, filters):
        assert filters == {"vehicleId": "veh-019"}
        return {
            "items": [
                {
                    "id": "td-slot-0001",
                    "vehicleId": "veh-019",
                    "startsAt": "2026-08-24T09:00:00Z",
                    "status": "available",
                }
            ]
        }

    async def list_service_types(self):
        return {
            "items": [
                {"id": "mot", "name": "MOT", "description": "Annual MOT inspection.", "durationMinutes": 60, "priceFromPence": 5499},
                {"id": "full-service", "name": "Full service", "description": "Comprehensive annual vehicle service.", "durationMinutes": 180, "priceFromPence": 32900},
                {"id": "tyre-fitting", "name": "Tyre fitting", "description": "Tyre replacement and balancing.", "durationMinutes": 90, "priceFromPence": None},
            ]
        }

    async def list_workshop_locations(self):
        return {"items": [{"id": "dealer-stockport", "name": "Northstar Stockport", "town": "Stockport"}]}

    async def list_workshop_slots(self, filters):
        self.last_workshop_filters = filters
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        future = (datetime.now(UTC) + timedelta(days=1)).isoformat().replace("+00:00", "Z")
        return {
            "items": [
                {"id": "ws-slot-past", "startsAt": past},
                {"id": "ws-slot-future", "startsAt": future},
            ]
        }

    async def estimate_part_exchange(self, body):
        return {
            "status": "estimated",
            "registration": body["registration"],
            "mileage": body["mileage"],
            "condition": body["condition"],
            "estimateLowPence": 1_000_000,
            "estimateHighPence": 1_200_000,
            "estimateNotice": "Indicative only.",
        }


@pytest.mark.asyncio
async def test_vehicle_list_uses_closed_safe_view() -> None:
    result = await ToolRegistry(FakeDealership()).execute("search_vehicles", {"make": "Volvo"})

    assert result.view_type == "vehicle_list"
    assert result.view_payload["items"][0]["price"] == "Price on request"
    assert result.view_payload["items"][0]["url"] == "/?vehicle=veh-019"


@pytest.mark.asyncio
async def test_vehicle_details_use_a_distinct_closed_view() -> None:
    result = await ToolRegistry(FakeDealership()).execute(
        "get_vehicle", {"id": "veh-019"}
    )

    assert result.view_type == "vehicle_details"
    assert result.view_payload["vehicle"]["id"] == "veh-019"
    assert "items" not in result.view_payload


@pytest.mark.asyncio
async def test_page_vehicle_selection_ranks_only_supplied_live_vehicle_ids() -> None:
    class RankedDealership(FakeDealership):
        async def get_vehicle(self, vehicle_id):
            mileage = {"veh-020": 22_250, "veh-032": 47_250, "veh-044": 12_000}[vehicle_id]
            return {
                "id": vehicle_id,
                "make": "Land Rover",
                "model": "Range Rover Evoque",
                "year": 2024,
                "mileage": mileage,
                "pricePence": 3_775_000,
                "availability": "available",
            }

    result = await ToolRegistry(RankedDealership()).execute(
        "select_page_vehicles",
        {
            "vehicleIds": ["veh-020", "veh-032", "veh-044"],
            "sort": "mileageAsc",
            "limit": 1,
        },
    )

    assert result.view_type == "vehicle_list"
    assert [item["id"] for item in result.view_payload["items"]] == ["veh-044"]
    assert result.view_payload["scope"] == "currentPage"
    assert result.view_payload["total"] == 3


@pytest.mark.asyncio
async def test_vehicle_search_resolves_town_to_live_dealership_id() -> None:
    dealership = FakeDealership()
    await ToolRegistry(dealership).execute(
        "search_vehicles", {"dealershipTown": "stockport"}
    )

    assert dealership.last_search_filters["dealershipId"] == "dealer-stockport"
    assert "dealershipTown" not in dealership.last_search_filters


@pytest.mark.asyncio
async def test_vehicle_search_for_unknown_town_does_not_return_empty_cards() -> None:
    result = await ToolRegistry(FakeDealership()).execute(
        "search_vehicles", {"dealershipTown": "Newcastle"}
    )

    assert result.view_type == "suggestion_list"
    assert result.facts == {"requestedTown": "Newcastle", "items": []}


@pytest.mark.asyncio
async def test_vehicle_search_preserves_requested_page_for_show_more() -> None:
    result = await ToolRegistry(FakeDealership()).execute(
        "search_vehicles", {"page": 2, "make": "Volvo"}
    )

    assert result.view_payload["page"] == 2
    assert result.view_payload["pageSize"] == 3
    assert result.view_payload["search"] == {
        "filters": {"make": "Volvo", "sort": "newest"},
        "page": 2,
    }


@pytest.mark.asyncio
async def test_explicit_reserved_or_sold_search_is_not_forced_back_to_available() -> None:
    dealership = FakeDealership()

    await ToolRegistry(dealership).execute(
        "search_vehicles", {"availability": "reserved"}
    )

    assert dealership.last_search_filters["availability"] == "reserved"


@pytest.mark.asyncio
async def test_vehicle_search_suggests_next_page_only_when_more_results_exist() -> None:
    registry = ToolRegistry(FakeDealership())

    first_page = await registry.execute("search_vehicles", {"page": 1})
    last_page = await registry.execute("search_vehicles", {"page": 6})

    assert "Show me more" in [
        suggestion["label"] for suggestion in first_page.view_payload["suggestions"]
    ]
    assert "Show me more" not in [
        suggestion["label"] for suggestion in last_page.view_payload["suggestions"]
    ]


@pytest.mark.asyncio
async def test_department_list_is_derived_from_live_opening_hours() -> None:
    result = await ToolRegistry(FakeDealership()).execute(
        "list_dealership_departments", {}
    )

    assert result.view_type == "dealership_list"
    assert result.view_payload["items"][0]["departments"] == ["parts", "sales", "service"]


@pytest.mark.asyncio
async def test_unknown_dealership_town_returns_no_cards() -> None:
    result = await ToolRegistry(FakeDealership()).execute(
        "list_dealerships", {"town": "Newcastle"}
    )

    assert result.view_type == "suggestion_list"
    assert result.view_payload["suggestions"][0]["label"] == "Show all locations"
    assert "does not currently have a dealership in Newcastle" in result.text


@pytest.mark.asyncio
async def test_misspelled_dealership_town_returns_the_matching_card() -> None:
    result = await ToolRegistry(FakeDealership()).execute(
        "list_dealerships", {"town": "Manchaester"}
    )

    assert result.view_type == "dealership_list"
    assert result.view_payload["items"] == [
        {"id": "dealer-manchester", "town": "Manchester"}
    ]


@pytest.mark.asyncio
async def test_opening_hours_are_grouped_into_customer_facing_location_cards() -> None:
    result = await ToolRegistry(FakeDealership()).execute(
        "list_opening_hours", {"day": "Saturday"}
    )

    assert result.view_type == "opening_hours"
    assert result.view_payload["items"][0]["town"] == "Manchester"
    assert result.view_payload["items"][0]["departments"][0]["name"] == "Sales"
    assert result.view_payload["items"][0]["holidayExceptions"][0]["opensAt"] == "10:00"
    assert result.view_payload["items"][0]["holidayExceptions"][1]["closed"] is True


@pytest.mark.asyncio
async def test_opening_hours_can_be_filtered_to_a_live_dealership_town() -> None:
    result = await ToolRegistry(FakeDealership()).execute(
        "list_opening_hours", {"town": "Stockport"}
    )

    assert [item["town"] for item in result.view_payload["items"]] == ["Stockport"]


@pytest.mark.asyncio
async def test_opening_hours_can_be_filtered_to_one_department() -> None:
    result = await ToolRegistry(FakeDealership()).execute(
        "list_opening_hours", {"town": "Stockport", "department": "parts"}
    )

    item = result.view_payload["items"][0]
    assert {entry["name"] for entry in item["departments"]} == {"Parts"}
    assert item["holidayExceptions"] == []


@pytest.mark.asyncio
async def test_opening_hours_for_unknown_town_do_not_show_unrelated_cards() -> None:
    result = await ToolRegistry(FakeDealership()).execute(
        "list_opening_hours", {"town": "Newcastle"}
    )

    assert result.view_type == "suggestion_list"
    assert result.facts == {"requestedTown": "Newcastle", "items": []}
    assert result.view_payload["suggestions"] == [
        {"label": "Show all locations", "text": "show me all dealership locations"}
    ]


@pytest.mark.asyncio
async def test_single_dealership_hours_use_the_same_card_shape_as_list_hours() -> None:
    result = await ToolRegistry(FakeDealership()).execute(
        "get_opening_hours", {"id": "dealer-stockport"}
    )

    item = result.view_payload["items"][0]
    assert item["town"] == "Stockport"
    assert item["day"] == "Weekly"
    assert item["departments"][0]["name"] == "Sales"


@pytest.mark.asyncio
async def test_workshop_locations_have_distinct_follow_up_actions() -> None:
    result = await ToolRegistry(FakeDealership()).execute("list_workshop_locations", {})

    assert result.view_type == "workshop_location_list"
    assert [item["label"] for item in result.view_payload["suggestions"]] == [
        "Book a service",
        "Find my booking",
    ]


@pytest.mark.asyncio
async def test_service_types_include_a_chip_for_each_live_choice() -> None:
    result = await ToolRegistry(FakeDealership()).execute("list_service_types", {})

    assert [item["label"] for item in result.view_payload["suggestions"]] == [
        "MOT",
        "Full service",
        "Tyre fitting",
    ]


@pytest.mark.asyncio
async def test_unsupported_named_service_returns_a_clear_outcome_not_the_catalogue() -> None:
    result = await ToolRegistry(FakeDealership()).execute(
        "get_service_information", {"q": "Do you do car cleaning?"}
    )

    assert result.view_type == "suggestion_list"
    assert result.facts == {
        "resolution": {"status": "unsupported"},
        "items": [],
    }
    assert "not currently listed" in result.text
    assert "items" not in result.view_payload


@pytest.mark.asyncio
async def test_write_like_tool_is_rejected() -> None:
    with pytest.raises(ValueError, match="disallowed"):
        await ToolRegistry(FakeDealership()).execute("create_test_drive", {})


@pytest.mark.asyncio
async def test_vehicle_comparison_uses_current_vehicle_records() -> None:
    result = await ToolRegistry(FakeDealership()).execute(
        "compare_vehicles", {"vehicleIds": ["veh-019", "veh-020"]}
    )

    assert result.view_type == "vehicle_comparison"
    assert [item["id"] for item in result.view_payload["items"]] == ["veh-019", "veh-020"]


@pytest.mark.asyncio
async def test_test_drive_options_include_vehicle_for_progressive_card() -> None:
    result = await ToolRegistry(FakeDealership()).execute(
        "list_test_drive_slots", {"vehicleId": "veh-019"}
    )

    assert result.view_type == "test_drive_slot_picker"
    assert result.view_payload["vehicle"]["id"] == "veh-019"
    assert result.view_payload["items"][0]["id"] == "td-slot-0001"


@pytest.mark.asyncio
async def test_reserved_vehicle_returns_interest_actions_instead_of_test_drive_times() -> None:
    class ReservedVehicle(FakeDealership):
        async def get_vehicle_availability(self, vehicle_id):
            return {
                "vehicleId": vehicle_id,
                "availability": "reserved",
                "canEnquire": True,
                "canBookTestDrive": False,
                "canRegisterInterest": True,
            }

        async def get_vehicle(self, vehicle_id):
            vehicle = await super().get_vehicle(vehicle_id)
            vehicle["availability"] = "reserved"
            return vehicle

        async def list_test_drive_slots(self, filters):
            raise AssertionError("Reserved vehicles must not request test-drive slots")

    result = await ToolRegistry(ReservedVehicle()).execute(
        "list_test_drive_slots", {"vehicleId": "veh-007"}
    )

    assert result.view_type == "vehicle_availability"
    assert result.view_payload["availability"] == "reserved"
    assert result.view_payload["suggestions"][0]["action"] == {
        "type": "start_vehicle_interest",
        "vehicleId": "veh-007",
    }


@pytest.mark.asyncio
async def test_named_workshop_filters_resolve_against_live_records() -> None:
    dealership = FakeDealership()
    result = await ToolRegistry(dealership).execute(
        "list_workshop_slots",
        {"dealershipTown": "Stockport", "serviceTypeName": "MOT"},
    )

    assert result.view_type == "slot_list"
    assert [item["id"] for item in result.view_payload["items"]] == ["ws-slot-future"]
    assert dealership.last_workshop_filters == {
        "dealershipId": "dealer-stockport",
        "serviceTypeId": "mot",
        "dateFrom": datetime.now(UTC).date().isoformat(),
    }


@pytest.mark.asyncio
async def test_service_price_question_returns_plain_live_information() -> None:
    result = await ToolRegistry(FakeDealership()).execute(
        "get_service_information",
        {"q": "What would I pay to have my tyres fitted?"},
    )

    assert result.view_type is None
    assert result.view_payload is None
    assert result.text == (
        "Tyre fitting is priced on request and takes about 90 minutes. "
        "Tyre replacement and balancing."
    )


@pytest.mark.asyncio
async def test_workshop_slots_can_never_be_requested_without_a_service() -> None:
    dealership = FakeDealership()
    result = await ToolRegistry(dealership).execute("list_workshop_slots", {})

    assert result.view_type == "service_list"
    assert dealership.last_workshop_filters is None


@pytest.mark.asyncio
async def test_empty_workshop_availability_suggests_live_alternative_locations() -> None:
    class NoLocalWorkshopSlots(FakeDealership):
        async def list_workshop_slots(self, filters):
            self.last_workshop_filters = filters
            if filters.get("dealershipId") == "dealer-stockport":
                return {"items": []}
            future = (datetime.now(UTC) + timedelta(days=1)).isoformat().replace(
                "+00:00", "Z"
            )
            return {
                "items": [
                    {
                        "id": "ws-slot-0001",
                        "startsAt": future,
                        "dealershipTown": "Manchester",
                    }
                ]
            }

    result = await ToolRegistry(NoLocalWorkshopSlots()).execute(
        "list_workshop_slots",
        {"dealershipTown": "Stockport", "serviceTypeName": "MOT"},
    )

    assert result.view_payload["items"] == []
    assert result.text == "No appointments for MOT are currently available in Stockport."
    assert result.view_payload["emptyMessage"] == result.text
    assert result.view_payload["suggestions"] == [
        {
            "label": "Try Manchester",
            "text": "Find an appointment for MOT in Manchester",
        },
        {
            "label": "Choose another service",
            "text": "find a workshop appointment",
            "action": {"type": "show_workshop_services"},
        },
    ]


@pytest.mark.asyncio
async def test_named_vehicle_comparison_resolves_each_query_from_live_stock() -> None:
    result = await ToolRegistry(FakeDealership()).execute(
        "compare_vehicle_models", {"queries": ["BMW 1 Series", "BMW 3 Series"]}
    )

    assert result.view_type == "suggestion_list"
    assert "two different" in result.text


@pytest.mark.asyncio
async def test_duplicate_vehicle_ids_do_not_render_a_fake_comparison() -> None:
    result = await ToolRegistry(FakeDealership()).execute(
        "compare_vehicles", {"vehicleIds": ["veh-019", "veh-019"]}
    )

    assert result.view_type == "suggestion_list"
    assert "two different" in result.text


@pytest.mark.asyncio
async def test_vehicle_facets_are_derived_from_platform_inventory() -> None:
    result = await ToolRegistry(FakeDealership()).execute("get_vehicle_facets", {})

    assert result.facts == {
        "makes": ["Dynamic Make"],
        "models": ["Dynamic Model"],
        "fuelTypes": ["Future Fuel"],
        "transmissions": ["Direct"],
        "bodyStyles": ["Crossover"],
        "budgets": [],
        "mileages": [],
    }


@pytest.mark.asyncio
async def test_part_exchange_estimate_is_read_only_and_structured() -> None:
    result = await ToolRegistry(FakeDealership()).execute(
        "estimate_part_exchange",
        {"registration": "AB19 XYZ", "mileage": 45_000, "condition": "good"},
    )

    assert result.view_type == "part_exchange_estimate"
    assert result.view_payload["estimateLowPence"] == 1_000_000
    assert result.view_payload["estimateNotice"] == "Indicative only."


@pytest.mark.asyncio
async def test_part_exchange_estimate_form_only_collects_valuation_inputs() -> None:
    result = await ToolRegistry(FakeDealership()).execute(
        "request_part_exchange_estimate_form",
        {"registration": "AB19 XYZ"},
    )

    assert result.view_type == "part_exchange_estimate_form"
    assert result.view_payload == {
        "version": 1,
        "values": {"registration": "AB19 XYZ"},
    }
