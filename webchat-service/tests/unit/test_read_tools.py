from datetime import UTC, datetime, timedelta

import pytest

from webchat.orchestration.fact_normalization import FactNormalizer
from webchat.orchestration.tools.executor import ApplicationToolExecutor
from webchat.orchestration.tools.vehicles import VehicleToolHandler
from webchat.orchestration.tools.workshop import _future_slots


class FakeDealership:
    def __init__(self):
        self.last_search_filters = None
        self.last_workshop_filters = None

    async def search_vehicles(self, filters):
        self.last_search_filters = filters
        if filters.get("q") and filters["pageSize"] == 50:
            models = {
                "BMW 1 Series": ("veh-019", "1 Series"),
                "BMW 3 Series": ("veh-020", "3 Series"),
            }
            if filters["q"] in models:
                vehicle_id, model = models[filters["q"]]
                return {
                    "items": [
                        {
                            "id": vehicle_id,
                            "make": "BMW",
                            "model": model,
                            "year": 2026,
                            "mileage": 1000,
                            "pricePence": 2_000_000,
                            "availability": "available",
                        }
                    ],
                    "pagination": {"totalPages": 1},
                }
            return {"items": [], "pagination": {"totalPages": 1}}
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
                    "bodyStyle": filters.get("bodyStyle") or "SUV",
                    "fuelType": filters.get("fuelType") or "Electric",
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
            item for item in (await self.list_dealerships())["items"] if item["id"] == dealership_id
        )

    async def get_opening_hours(self, dealership_id):
        return {
            "weekly": [
                {
                    "department": "sales",
                    "day": "Saturday",
                    "opensAt": "09:00",
                    "closesAt": "17:00",
                    "closed": False,
                },
                {
                    "department": "service",
                    "day": "Saturday",
                    "opensAt": "09:00",
                    "closesAt": "13:00",
                    "closed": False,
                },
                {
                    "department": "parts",
                    "day": "Saturday",
                    "opensAt": "09:00",
                    "closesAt": "13:00",
                    "closed": False,
                },
            ],
            "holidayExceptions": [
                {
                    "department": "sales",
                    "date": "2026-08-31",
                    "label": "Bank holiday",
                    "opensAt": "10:00",
                    "closesAt": "16:00",
                    "closed": False,
                },
                {
                    "department": "service",
                    "date": "2026-08-31",
                    "label": "Bank holiday",
                    "opensAt": None,
                    "closesAt": None,
                    "closed": True,
                },
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
                {
                    "id": "mot",
                    "name": "MOT",
                    "description": "Annual MOT inspection.",
                    "durationMinutes": 60,
                    "priceFromPence": 5499,
                },
                {
                    "id": "full-service",
                    "name": "Full service",
                    "description": "Comprehensive annual vehicle service.",
                    "durationMinutes": 180,
                    "priceFromPence": 32900,
                },
                {
                    "id": "tyre-fitting",
                    "name": "Tyre fitting",
                    "description": "Tyre replacement and balancing.",
                    "durationMinutes": 90,
                    "priceFromPence": None,
                },
            ]
        }

    async def list_workshop_locations(self):
        return {
            "items": [
                {"id": "dealer-stockport", "name": "Northstar Stockport", "town": "Stockport"}
            ]
        }

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
async def test_schedule_prompt_exposes_vehicle_identity_without_operational_ids() -> None:
    class LocatedVehicle(FakeDealership):
        async def get_vehicle(self, vehicle_id):
            vehicle = await super().get_vehicle(vehicle_id)
            return {**vehicle, "dealershipName": "Northstar Liverpool"}

    result = await ApplicationToolExecutor(LocatedVehicle()).execute(
        "list_test_drive_slots", {"vehicleId": "veh-019"}
    )
    envelope = FactNormalizer().normalize(
        result_id="result-test-drive-prompt",
        call_id="call-test-drive-prompt",
        intent_id="intent-test-drive-prompt",
        tool="list_test_drive_slots",
        result=result,
    )

    public = {fact.field: fact.displayValue for fact in envelope.ai_safe_facts()}
    assert public["vehicle.make"] == "Volvo"
    assert public["vehicle.model"] == "XC40"
    assert public["vehicle.dealershipName"] == "Northstar Liverpool"
    assert envelope.responseObligation is not None
    required = set(envelope.responseObligation.requiredFactIds)
    assert {
        fact.factId for fact in envelope.facts if fact.field == "vehicle.dealershipName"
    } == required
    assert "Northstar Liverpool" in result.text
    assert not any(field == "vehicle.id" or field.endswith("vehicleId") for field in public)
    assert "veh-019" not in {fact.displayValue for fact in envelope.ai_safe_facts()}
    assert "vehicle:veh-019" in {entity.reference for entity in envelope.entities}
    assert any(
        fact.field == "vehicle.id" and fact.sensitivity == "internal" for fact in envelope.facts
    )
    assert "veh-019" not in {fact["displayValue"] for fact in envelope.ai_projection()["facts"]}


def test_workshop_time_of_day_filter_never_surfaces_afternoon_as_morning() -> None:
    now = datetime(2026, 9, 4, 9, tzinfo=UTC)
    slots = [
        {"id": "morning", "startsAt": "2026-09-07T11:00:00+01:00"},
        {"id": "afternoon", "startsAt": "2026-09-07T14:00:00+01:00"},
    ]

    assert [item["id"] for item in _future_slots(slots, now, time_of_day="morning")] == ["morning"]
    assert [item["id"] for item in _future_slots(slots, now, time_of_day="afternoon")] == [
        "afternoon"
    ]


@pytest.mark.asyncio
async def test_equivalent_test_drive_preserves_requested_schedule_and_discloses_substitution() -> (
    None
):
    class EquivalentVehicleSlots(FakeDealership):
        def __init__(self):
            super().__init__()
            self.alternative_slot_filters = None

        async def get_vehicle(self, vehicle_id):
            return {
                "id": vehicle_id,
                "year": 2026,
                "make": "BMW",
                "model": "3 Series",
                "variant": "320d M Sport",
                "availability": "available",
            }

        async def search_vehicles(self, filters):
            assert filters["make"] == "BMW"
            assert filters["model"] == "3 Series"
            return {
                "items": [
                    await self.get_vehicle("veh-001"),
                    {
                        "id": "veh-002",
                        "year": 2020,
                        "make": "BMW",
                        "model": "3 Series",
                        "variant": "320d M Sport",
                        "availability": "available",
                    },
                ]
            }

        async def list_test_drive_slots(self, filters):
            if filters.get("vehicleId"):
                return {"items": []}
            self.alternative_slot_filters = dict(filters)
            return {
                "items": [
                    {
                        "id": "td-saturday-afternoon",
                        "vehicleId": "veh-002",
                        "startsAt": "2026-09-05T16:00:00Z",
                        "dealershipTown": "Stockport",
                    },
                    {
                        "id": "td-monday-morning",
                        "vehicleId": "veh-002",
                        "startsAt": "2026-09-07T09:00:00Z",
                        "dealershipTown": "Stockport",
                    },
                    {
                        "id": "td-monday-noon",
                        "vehicleId": "veh-002",
                        "startsAt": "2026-09-07T11:00:00Z",
                        "dealershipTown": "Stockport",
                    },
                    {
                        "id": "td-monday-afternoon",
                        "vehicleId": "veh-002",
                        "startsAt": "2026-09-07T14:00:00Z",
                        "dealershipTown": "Stockport",
                    },
                ]
            }

    dealership = EquivalentVehicleSlots()
    result = await ApplicationToolExecutor(dealership).execute(
        "list_test_drive_slots",
        {
            "vehicleId": "veh-001",
            "dateFrom": "2026-09-07",
            "dateTo": "2026-09-07",
            "timeOfDay": "morning",
        },
    )

    assert dealership.alternative_slot_filters == {
        "dateFrom": "2026-09-07",
        "dateTo": "2026-09-07",
    }
    assert [item["id"] for item in result.view_payload["items"]] == ["td-monday-morning"]
    assert result.facts["resolution"]["continuation"] == "offer_equivalent_vehicle_slots"
    assert result.view_payload["collectionViewType"] == "choice_list"
    assert result.view_payload["choiceEntityType"] == "appointment"
    assert result.view_payload["choiceField"] == "slotId"
    assert result.view_payload["collectionPresentation"]["purpose"] == "choice"
    assert result.alternative_offer == {
        "reasonCode": "selected_vehicle_no_future_appointments",
        "requestedOutcome": "2026 BMW 3 Series 320d M Sport at 7 Sep 2026, morning",
        "failureReason": "The exact selected vehicle has no future test-drive appointments.",
        "offeredOutcome": (
            "Here are matching times for a different currently available BMW 3 Series listing."
        ),
        "changes": [
            {
                "dimension": "Vehicle listing",
                "requested": "2026 BMW 3 Series 320d M Sport",
                "offered": "a different stock listing identified with each appointment",
            }
        ],
        "preserved": ["BMW 3 Series", "7 Sep 2026, morning"],
        "candidateReferences": ["appointment:td-monday-morning"],
        "requiresCustomerAcceptance": True,
    }
    normalized = FactNormalizer().normalize(
        result_id="result-equivalent-test-drive",
        call_id="call-equivalent-test-drive",
        intent_id="intent-equivalent-test-drive",
        tool="list_test_drive_slots",
        result=result,
    )
    assert normalized.availableCollections[0].viewType == "choice_list"
    assert normalized.availableCollections[0].presentation.items[0].label == (
        "Mon, 7 Sept 2026, 10:00"
    )
    assert normalized.alternativeOffer.candidateReferences == ["appointment:td-monday-morning"]


@pytest.mark.asyncio
async def test_vehicle_list_uses_closed_safe_view() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "search_vehicles", {"make": "Volvo"}
    )

    assert result.view_type == "vehicle_list"
    assert result.view_payload["items"][0]["price"] == "Price on request"
    assert result.view_payload["items"][0]["url"] == "/?vehicle=veh-019"


@pytest.mark.asyncio
async def test_vehicle_inventory_summary_does_not_claim_page_makes_cover_all_matches() -> None:
    class PagedBlackInventory(FakeDealership):
        async def search_vehicles(self, filters):
            return {
                "items": [
                    {
                        "id": "veh-019",
                        "make": "Volvo",
                        "model": "XC40",
                        "colour": "Black Stone",
                        "availability": "available",
                    }
                ],
                "pagination": {
                    "page": 1,
                    "pageSize": filters["pageSize"],
                    "totalItems": 17,
                },
            }

    result = await ApplicationToolExecutor(PagedBlackInventory()).execute(
        "search_vehicles", {"q": "black"}
    )

    assert result.facts["inventorySummary"] == {
        "matchedResultSet": {"count": 17, "scope": "all_matching_inventory"},
        "displayedPage": {
            "page": 1,
            "itemCount": 1,
            "makes": ["Volvo"],
            "scope": "displayed_page_only",
        },
    }


@pytest.mark.asyncio
async def test_vehicle_details_use_a_distinct_closed_view() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
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

    result = await ApplicationToolExecutor(RankedDealership()).execute(
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
    assert result.view_payload["suggestions"] == [
        {
            "label": "Book a test drive",
            "text": "book a test drive for one of these vehicles",
        },
        {
            "label": "Search full inventory",
            "text": "search the full vehicle inventory with these filters",
            "action": {"type": "search_vehicle_inventory"},
        },
        {"label": "Find another car", "text": "help me find another car"},
        {"label": "Current offers", "text": "show me current offers"},
    ]


@pytest.mark.asyncio
async def test_vehicle_search_resolves_town_to_live_dealership_id() -> None:
    dealership = FakeDealership()
    await ApplicationToolExecutor(dealership).execute(
        "search_vehicles", {"dealershipTown": "stockport"}
    )

    assert dealership.last_search_filters["dealershipId"] == "dealer-stockport"
    assert "dealershipTown" not in dealership.last_search_filters


@pytest.mark.asyncio
async def test_vehicle_search_for_unknown_town_does_not_return_empty_cards() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "search_vehicles", {"dealershipTown": "Newcastle"}
    )

    assert result.view_type == "suggestion_list"
    assert result.facts == {
        "outcome": "unavailable",
        "requestedTown": "Newcastle",
        "items": [],
    }


@pytest.mark.asyncio
async def test_vehicle_search_preserves_requested_page_for_show_more() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "search_vehicles", {"page": 2, "make": "Volvo"}
    )

    assert result.view_payload["page"] == 2
    assert result.view_payload["pageSize"] == 3
    assert result.view_payload["search"] == {
        "filters": {"make": "Volvo", "sort": "newest"},
        "page": 2,
    }
    assert result.view_payload["filterSummary"] == "Make: Volvo"


@pytest.mark.asyncio
async def test_empty_vehicle_results_explain_retained_filters_and_offer_real_reset() -> None:
    class EmptyDealership(FakeDealership):
        async def search_vehicles(self, filters):
            self.last_search_filters = filters
            return {
                "items": [],
                "pagination": {"page": 1, "pageSize": 3, "totalItems": 0},
            }

    result = await ApplicationToolExecutor(EmptyDealership()).execute(
        "refine_vehicle_search",
        {"fuelType": "Hybrid", "make": "BMW"},
    )

    assert result.text == (
        "I couldn't find any available vehicles matching your current filters: "
        "Make: BMW; Fuel: Hybrid. Try changing or clearing a filter."
    )
    assert result.facts["appliedSearch"] == {
        "make": "BMW",
        "fuelType": "Hybrid",
        "sort": "newest",
    }
    assert result.facts["filterSummary"] == "Make: BMW; Fuel: Hybrid"
    assert [suggestion["label"] for suggestion in result.view_payload["suggestions"]] == [
        "Change fuel",
        "Any fuel",
        "Show all cars",
        "Cheapest cars",
    ]
    assert result.view_payload["suggestions"][2]["action"] == {"type": "reset_vehicle_search"}
    assert result.alternative_offer is None


@pytest.mark.asyncio
async def test_empty_filtered_offer_search_keeps_filter_changes_as_recovery_actions() -> None:
    class EmptyOfferDealership:
        async def list_offers(self, query):
            assert query == {"make": "BMW", "productType": "PCP"}
            return {"items": []}

        async def get_business_information(self):
            return {"finance": {"notice": "Finance is subject to status."}}

    result = await ApplicationToolExecutor(EmptyOfferDealership()).execute(
        "list_offers",
        {"make": "BMW", "productType": "PCP"},
    )

    assert result.facts["outcome"] == "empty"
    assert result.alternative_offer is None


@pytest.mark.asyncio
async def test_offer_search_preserves_a_customer_supplied_make_and_model() -> None:
    class ModelOfferDealership:
        async def list_offers(self, query):
            assert query == {"make": "MINI"}
            return {
                "items": [
                    {"id": "offer-cooper", "make": "MINI", "model": "Cooper"},
                    {"id": "offer-countryman", "make": "MINI", "model": "Countryman"},
                ]
            }

        async def get_business_information(self):
            return {"finance": {"notice": "Finance is subject to status."}}

    result = await ApplicationToolExecutor(ModelOfferDealership()).execute(
        "list_offers",
        {"make": "MINI", "model": "Cooper"},
    )

    assert [item["id"] for item in result.facts["items"]] == ["offer-cooper"]


@pytest.mark.asyncio
async def test_vehicle_search_forwards_generic_negative_preferences() -> None:
    dealership = FakeDealership()

    result = await ApplicationToolExecutor(dealership).execute(
        "refine_vehicle_search",
        {
            "bodyStyle": "SUV",
            "fuelType": "Hybrid",
            "maxPricePence": 4_500_000,
            "excludedMakes": ["Land Rover"],
        },
    )

    assert dealership.last_search_filters == {
        "page": 1,
        "bodyStyle": "SUV",
        "fuelType": "Hybrid",
        "excludedMakes": ["Land Rover"],
        "maxPricePence": 4_500_000,
        "sort": "newest",
        "pageSize": 3,
        "availability": "available",
    }
    assert result.view_payload["search"]["filters"]["excludedMakes"] == ["Land Rover"]


@pytest.mark.asyncio
async def test_vehicle_search_defensively_removes_upstream_constraint_violations() -> None:
    class NonCompliantDealership(FakeDealership):
        async def search_vehicles(self, filters):
            self.last_search_filters = filters
            return {
                "items": [
                    {"id": "veh-001", "make": "BMW", "availability": "available"},
                    {"id": "veh-002", "make": "MINI", "availability": "available"},
                ],
                "pagination": {"page": 1, "pageSize": 3, "totalItems": 2, "totalPages": 1},
            }

    result = await ApplicationToolExecutor(NonCompliantDealership()).execute(
        "search_vehicles", {"excludedMakes": ["bmw"]}
    )

    assert [item["id"] for item in result.view_payload["items"]] == ["veh-002"]
    assert result.facts["upstreamConstraintViolation"] is True


@pytest.mark.asyncio
async def test_explicit_reserved_or_sold_search_is_not_forced_back_to_available() -> None:
    dealership = FakeDealership()

    await ApplicationToolExecutor(dealership).execute(
        "search_vehicles", {"availability": "reserved"}
    )

    assert dealership.last_search_filters["availability"] == "reserved"


@pytest.mark.asyncio
async def test_natural_vehicle_category_expands_to_all_matching_live_values() -> None:
    class LiveColourDealership(FakeDealership):
        def __init__(self):
            super().__init__()
            self.searches = []

        async def search_vehicles(self, filters):
            self.searches.append(dict(filters))
            if filters.get("colour") == "black":
                return {
                    "items": [],
                    "pagination": {"page": 1, "pageSize": 3, "totalItems": 0},
                }
            if filters.get("pageSize") == 100:
                return {
                    "items": [
                        {"colour": "Black Stone"},
                        {"colour": "Midnight Black"},
                        {"colour": "Black Sapphire"},
                        {"colour": "Alpine White"},
                    ],
                    "pagination": {"page": 1, "pageSize": 100, "totalItems": 4},
                }
            assert filters["colours"] == [
                "Black Stone",
                "Midnight Black",
                "Black Sapphire",
            ]
            return {
                "items": [
                    {
                        "id": "veh-019",
                        "make": "MINI",
                        "model": "Countryman",
                        "colour": "Black Stone",
                        "availability": "available",
                    }
                ],
                "pagination": {"page": 1, "pageSize": 3, "totalItems": 3},
            }

    dealership = LiveColourDealership()
    result = await ApplicationToolExecutor(dealership).execute(
        "search_vehicles", {"colour": "black"}
    )

    assert dealership.searches[-1]["colours"] == [
        "Black Stone",
        "Midnight Black",
        "Black Sapphire",
    ]
    assert result.view_type == "vehicle_list"
    assert result.view_payload["total"] == 3
    assert result.view_payload["search"]["filters"] == {
        "colour": "black",
        "sort": "newest",
    }
    assert result.facts["appliedSearch"] == {"colour": "black", "sort": "newest"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("singular", "plural", "requested", "available", "expected"),
    [
        ("make", "makes", "land", ["Land Rover", "MINI"], ["Land Rover"]),
        ("model", "models", "series", ["1 Series", "3 Series", "i4"], ["1 Series", "3 Series"]),
        (
            "colour",
            "colours",
            "blue",
            ["Blazing Blue", "Arctic Race Blue", "Alpine White"],
            ["Blazing Blue", "Arctic Race Blue"],
        ),
        ("fuelType", "fuelTypes", "plug in", ["Plug-in Hybrid", "Petrol"], ["Plug-in Hybrid"]),
        (
            "transmission",
            "transmissions",
            "automatic",
            ["Semi Automatic", "Manual"],
            ["Semi Automatic"],
        ),
        ("bodyStyle", "bodyStyles", "sport", ["Sport Utility", "Estate"], ["Sport Utility"]),
    ],
)
async def test_live_category_resolution_is_dimension_agnostic(
    singular: str,
    plural: str,
    requested: str,
    available: list[str],
    expected: list[str],
) -> None:
    class CategoryDealership:
        async def search_vehicles(self, filters):
            assert filters == {"availability": "available", "pageSize": 100}
            return {
                "items": [{singular: value} for value in available],
                "pagination": {"totalItems": len(available)},
            }

    resolved = await VehicleToolHandler(CategoryDealership())._resolve_live_categories(
        {singular: requested, "availability": "available", "pageSize": 3}
    )

    assert resolved is not None
    assert singular not in resolved
    assert resolved[plural] == expected


@pytest.mark.asyncio
async def test_described_vehicle_availability_resolves_across_all_stock_states() -> None:
    class SoldVehicleDealership(FakeDealership):
        async def search_vehicles(self, filters):
            self.last_search_filters = dict(filters)
            assert "availability" not in filters
            return {
                "items": [
                    {
                        "id": "veh-042",
                        "make": "BMW",
                        "model": "1 Series",
                        "colour": "Atlantic Blue",
                        "year": 2025,
                        "pricePence": 2_900_000,
                        "availability": "sold",
                    }
                ],
                "pagination": {"totalItems": 1, "totalPages": 1},
            }

        async def get_vehicle(self, vehicle_id):
            assert vehicle_id == "veh-042"
            return {
                "id": vehicle_id,
                "make": "BMW",
                "model": "1 Series",
                "colour": "Atlantic Blue",
                "year": 2025,
                "pricePence": 2_900_000,
                "availability": "sold",
            }

        async def get_vehicle_availability(self, vehicle_id):
            assert vehicle_id == "veh-042"
            return {
                "vehicleId": vehicle_id,
                "availability": "sold",
                "canEnquire": True,
                "canBookTestDrive": False,
                "canRegisterInterest": False,
            }

    dealership = SoldVehicleDealership()
    result = await ApplicationToolExecutor(dealership).execute(
        "resolve_vehicle_availability",
        {
            "make": "BMW",
            "model": "1 Series",
            "colour": "Atlantic Blue",
            "minYear": 2025,
            "maxYear": 2025,
        },
    )

    assert dealership.last_search_filters == {
        "make": "BMW",
        "model": "1 Series",
        "colour": "Atlantic Blue",
        "minYear": 2025,
        "maxYear": 2025,
        "pageSize": 50,
    }
    assert result.view_type == "vehicle_availability"
    assert result.view_payload["vehicle"]["id"] == "veh-042"
    assert result.view_payload["availability"] == "sold"


@pytest.mark.asyncio
async def test_vehicle_search_suggests_next_page_only_when_more_results_exist() -> None:
    registry = ApplicationToolExecutor(FakeDealership())

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
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "list_dealership_departments", {}
    )

    assert result.view_type == "dealership_list"
    assert result.view_payload["items"][0]["departments"] == ["parts", "sales", "service"]


@pytest.mark.asyncio
async def test_departments_can_be_scoped_to_one_live_dealership() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "find_dealership_departments", {"town": "Stockport", "department": "sales"}
    )

    assert result.view_type == "dealership_list"
    assert [item["town"] for item in result.view_payload["items"]] == ["Stockport"]
    assert result.view_payload["items"][0]["departments"] == [
        "parts",
        "sales",
        "service",
    ]


@pytest.mark.asyncio
async def test_unknown_dealership_town_returns_no_cards() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "list_dealerships", {"town": "Newcastle"}
    )

    assert result.view_type == "suggestion_list"
    assert result.view_payload["suggestions"][0]["label"] == "Show all locations"
    assert "does not currently have a dealership in Newcastle" in result.text
    assert result.alternative_offer["reasonCode"] == "requested_dealership_location_unavailable"
    assert result.alternative_offer["changes"] == [
        {
            "dimension": "Location",
            "requested": "Newcastle",
            "offered": "Manchester, Stockport",
        }
    ]


@pytest.mark.asyncio
async def test_misspelled_dealership_town_returns_the_matching_card() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "list_dealerships", {"town": "Manchaester"}
    )

    assert result.view_type == "dealership_list"
    assert result.view_payload["items"] == [{"id": "dealer-manchester", "town": "Manchester"}]


@pytest.mark.asyncio
async def test_opening_hours_are_grouped_into_customer_facing_location_cards() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "list_opening_hours", {"day": "Saturday"}
    )

    assert result.view_type == "opening_hours"
    assert result.view_payload["items"][0]["town"] == "Manchester"
    assert result.view_payload["items"][0]["departments"][0]["name"] == "Sales"
    assert result.view_payload["items"][0]["holidayExceptions"][0]["opensAt"] == "10:00"
    assert result.view_payload["items"][0]["holidayExceptions"][1]["closed"] is True


@pytest.mark.asyncio
async def test_opening_hours_can_be_filtered_to_a_live_dealership_town() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "list_opening_hours", {"town": "Stockport"}
    )

    assert [item["town"] for item in result.view_payload["items"]] == ["Stockport"]


@pytest.mark.asyncio
async def test_holiday_opening_hours_do_not_mix_in_regular_weekday_hours() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "list_holiday_opening_hours", {}
    )

    assert result.text == "Published holiday opening hours are shown below."
    assert result.view_payload["holidayOnly"] is True
    assert result.view_payload["day"] == "Holiday"
    assert all(item["departments"] == [] for item in result.view_payload["items"])
    assert all(item["holidayExceptions"] for item in result.view_payload["items"])


@pytest.mark.asyncio
async def test_missing_department_holiday_hours_are_explicitly_unknown() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "list_holiday_opening_hours",
        {"town": "Stockport", "department": "parts", "date": "2026-08-31"},
    )

    item = result.view_payload["items"][0]
    assert item["holidayExceptions"] == []
    assert item["holidayDepartment"] == {
        "name": "Parts",
        "date": "2026-08-31",
        "status": "not_published",
    }
    assert "not published" in result.text


@pytest.mark.asyncio
async def test_opening_hours_can_be_filtered_to_one_department() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "list_opening_hours", {"town": "Stockport", "department": "parts"}
    )

    item = result.view_payload["items"][0]
    assert {entry["name"] for entry in item["departments"]} == {"Parts"}
    assert item["holidayExceptions"] == []


@pytest.mark.asyncio
async def test_opening_hours_for_unknown_town_do_not_show_unrelated_cards() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "list_opening_hours", {"town": "Newcastle"}
    )

    assert result.view_type == "suggestion_list"
    assert result.facts == {
        "outcome": "unavailable",
        "requestedTown": "Newcastle",
        "items": [],
    }
    assert result.view_payload["suggestions"] == [
        {"label": "Show all locations", "text": "show me all dealership locations"},
        {"label": "Opening hours", "text": "show me the opening hours"},
        {"label": "Departments", "text": "what departments do the dealerships have?"},
        {
            "label": "Request a callback",
            "text": "please have a dealership call me",
            "action": {"type": "start_callback"},
        },
    ]


@pytest.mark.asyncio
async def test_single_dealership_hours_use_the_same_card_shape_as_list_hours() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "get_opening_hours", {"id": "dealer-stockport"}
    )

    item = result.view_payload["items"][0]
    assert item["town"] == "Stockport"
    assert item["day"] == "Weekly"
    assert item["departments"][0]["name"] == "Sales"


@pytest.mark.asyncio
async def test_workshop_locations_have_distinct_follow_up_actions() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute("list_workshop_locations", {})

    assert result.view_type == "workshop_location_list"
    assert [item["label"] for item in result.view_payload["suggestions"]] == [
        "Book a service",
        "Find my booking",
        "Service types",
        "Request a callback",
    ]


@pytest.mark.asyncio
async def test_service_types_include_a_bullet_for_each_live_choice() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute("list_service_types", {})

    assert [item["label"] for item in result.view_payload["suggestions"]] == [
        "MOT",
        "Full service",
        "Tyre fitting",
    ]
    assert result.text == "We currently provide these workshop services:"
    assert result.view_payload["collectionPresentation"] == {
        "schemaVersion": 1,
        "layout": "bullet_list",
        "purpose": "information",
        "items": [
            {"label": "MOT", "description": "Annual MOT inspection."},
            {
                "label": "Full service",
                "description": "Comprehensive annual vehicle service.",
            },
            {"label": "Tyre fitting", "description": "Tyre replacement and balancing."},
        ],
    }


@pytest.mark.asyncio
async def test_unsupported_named_service_returns_a_clear_outcome_not_the_catalogue() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "get_service_information", {"q": "Do you do car cleaning?"}
    )

    assert result.view_type == "suggestion_list"
    assert result.facts == {
        "resolution": {"status": "unavailable"},
        "items": [],
    }
    assert "not currently listed" in result.text
    assert "items" not in result.view_payload
    assert result.alternative_offer is None


@pytest.mark.asyncio
async def test_write_like_tool_is_rejected() -> None:
    with pytest.raises(ValueError, match="disallowed"):
        await ApplicationToolExecutor(FakeDealership()).execute("create_test_drive", {})


@pytest.mark.asyncio
async def test_vehicle_comparison_uses_current_vehicle_records() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "compare_vehicles", {"vehicleIds": ["veh-019", "veh-020"]}
    )

    assert result.view_type == "vehicle_comparison"
    assert [item["id"] for item in result.view_payload["items"]] == ["veh-019", "veh-020"]
    assert [item["action"] for item in result.view_payload["suggestions"]] == [
        {"type": "select_test_drive_vehicle", "vehicleId": "veh-019"},
        {"type": "select_test_drive_vehicle", "vehicleId": "veh-020"},
    ]


@pytest.mark.asyncio
async def test_test_drive_waits_for_schedule_preferences_before_reading_slots() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "list_test_drive_slots", {"vehicleId": "veh-019"}
    )

    assert result.view_type is None
    assert result.view_payload is None
    assert result.facts["vehicle"]["id"] == "veh-019"
    assert result.facts["resolution"]["status"] == "ready"
    assert result.facts["resolution"]["continuation"] == "request_schedule_preferences"


@pytest.mark.asyncio
async def test_test_drive_without_online_slots_has_clear_recovery_actions() -> None:
    class NoOnlineSlots(FakeDealership):
        async def get_vehicle_availability(self, vehicle_id):
            return {
                "vehicleId": vehicle_id,
                "availability": "available",
                "canEnquire": True,
                "canBookTestDrive": False,
                "canRegisterInterest": False,
            }

        async def list_test_drive_slots(self, filters):
            assert filters["vehicleId"] == "veh-019"
            assert filters["dateFrom"] == datetime.now(UTC).date().isoformat()
            return {"items": []}

        async def search_vehicles(self, filters):
            assert filters == {
                "make": "Volvo",
                "model": "XC40",
                "availability": "available",
                "pageSize": 100,
            }
            return {"items": []}

    result = await ApplicationToolExecutor(NoOnlineSlots()).execute(
        "list_test_drive_slots",
        {"vehicleId": "veh-019", "schedulePreferenceMode": "clear"},
    )

    assert result.view_payload["emptyMessage"] == (
        "No online test-drive times are currently available for this vehicle."
    )
    assert [item["label"] for item in result.view_payload["suggestions"]] == [
        "Find another car",
        "Send a sales enquiry",
    ]


@pytest.mark.asyncio
async def test_test_drive_location_change_returns_same_vehicle_at_live_fallback_location() -> None:
    class LocationFallback(FakeDealership):
        def __init__(self):
            super().__init__()
            self.test_drive_calls = []

        async def list_test_drive_slots(self, filters):
            self.test_drive_calls.append(dict(filters))
            if filters.get("dealershipId") == "dealer-manchester":
                return {"items": []}
            starts_at = (datetime.now(UTC) + timedelta(days=5)).replace(
                hour=16, minute=0, second=0, microsecond=0
            )
            return {
                "items": [
                    {
                        "id": "td-slot-0042",
                        "vehicleId": "veh-019",
                        "dealershipId": "dealer-stockport",
                        "dealershipName": "Northstar Stockport",
                        "startsAt": starts_at.isoformat(),
                        "status": "available",
                    }
                ]
            }

    dealership = LocationFallback()
    result = await ApplicationToolExecutor(dealership).execute(
        "list_test_drive_slots",
        {
            "vehicleId": "veh-019",
            "dealershipId": "dealer-manchester",
            "schedulePreferenceMode": "clear",
        },
    )

    assert [item["id"] for item in result.view_payload["items"]] == ["td-slot-0042"]
    assert result.view_payload["requestedDealershipHadNoAvailability"] is True
    assert result.facts["resolution"]["status"] == "empty"
    assert result.facts["resolution"]["continuation"] == "offer_location_alternatives"
    assert dealership.test_drive_calls[0]["dealershipId"] == "dealer-manchester"
    assert "dealershipId" not in dealership.test_drive_calls[1]


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

    result = await ApplicationToolExecutor(ReservedVehicle()).execute(
        "list_test_drive_slots", {"vehicleId": "veh-007"}
    )

    assert result.view_type == "vehicle_availability"
    assert result.view_payload["availability"] == "reserved"
    assert result.alternative_offer is None
    assert result.view_payload["suggestions"][0]["action"] == {
        "type": "start_vehicle_interest",
        "vehicleId": "veh-007",
    }


@pytest.mark.asyncio
async def test_named_workshop_filters_resolve_against_live_records() -> None:
    dealership = FakeDealership()
    result = await ApplicationToolExecutor(dealership).execute(
        "list_workshop_slots",
        {
            "dealershipTown": "Stockport",
            "serviceTypeName": "MOT",
            "schedulePreferenceMode": "clear",
        },
    )

    assert result.view_type == "slot_list"
    assert [item["id"] for item in result.view_payload["items"]] == ["ws-slot-future"]
    assert dealership.last_workshop_filters == {
        "dealershipId": "dealer-stockport",
        "serviceTypeId": "mot",
        "dateFrom": datetime.now(UTC).date().isoformat(),
    }


@pytest.mark.asyncio
async def test_broad_workshop_availability_is_one_bounded_finite_choice() -> None:
    class LongWorkshopHorizon(FakeDealership):
        async def list_workshop_slots(self, filters):
            start = datetime.now(UTC) + timedelta(days=1)
            return {
                "items": [
                    {
                        "id": f"ws-slot-{index:04d}",
                        "dealershipId": "dealer-liverpool",
                        "dealershipTown": "Liverpool",
                        "serviceTypeId": "brake-inspection",
                        "startsAt": (start + timedelta(days=index)).isoformat(),
                    }
                    for index in range(18)
                ]
            }

    result = await ApplicationToolExecutor(LongWorkshopHorizon()).execute(
        "refine_workshop_slots",
        {
            "dealershipId": "dealer-liverpool",
            "serviceTypeId": "brake-inspection",
            "schedulePreferenceMode": "clear",
        },
    )

    expected_ids = [f"ws-slot-{index:04d}" for index in range(4)]
    assert [item["id"] for item in result.view_payload["items"]] == expected_ids
    assert [item["id"] for item in result.facts["items"]] == expected_ids
    normalized = FactNormalizer().normalize(
        result_id="result-broad-workshop-slots",
        call_id="call-broad-workshop-slots",
        intent_id="intent-broad-workshop-slots",
        tool="refine_workshop_slots",
        result=result,
    )
    assert normalized.responseObligation is not None
    assert normalized.responseObligation.choiceMode == "choose_multiple"
    assert normalized.responseObligation.candidateReferences == [
        f"appointment:{slot_id}" for slot_id in expected_ids
    ]


@pytest.mark.asyncio
async def test_broad_test_drive_availability_uses_the_same_bounded_finite_choice() -> None:
    class LongTestDriveHorizon(FakeDealership):
        async def list_test_drive_slots(self, filters):
            start = datetime.now(UTC) + timedelta(days=1)
            return {
                "items": [
                    {
                        "id": f"td-slot-{index:04d}",
                        "vehicleId": "veh-019",
                        "dealershipId": "dealer-stockport",
                        "dealershipTown": "Stockport",
                        "startsAt": (start + timedelta(days=index)).isoformat(),
                    }
                    for index in range(18)
                ]
            }

    result = await ApplicationToolExecutor(LongTestDriveHorizon()).execute(
        "list_test_drive_slots",
        {"vehicleId": "veh-019", "schedulePreferenceMode": "clear"},
    )

    expected_ids = [f"td-slot-{index:04d}" for index in range(4)]
    assert [item["id"] for item in result.view_payload["items"]] == expected_ids
    assert [item["id"] for item in result.facts["items"]] == expected_ids
    normalized = FactNormalizer().normalize(
        result_id="result-broad-test-drive-slots",
        call_id="call-broad-test-drive-slots",
        intent_id="intent-broad-test-drive-slots",
        tool="list_test_drive_slots",
        result=result,
    )
    assert normalized.responseObligation is not None
    assert normalized.responseObligation.choiceMode == "choose_multiple"
    assert normalized.responseObligation.candidateReferences == [
        f"appointment:{slot_id}" for slot_id in expected_ids
    ]


@pytest.mark.asyncio
async def test_service_price_question_returns_the_live_mot_price() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "get_service_information",
        {"serviceTypeId": "mot"},
    )

    assert result.view_type is None
    assert result.view_payload is None
    assert result.text == (
        "MOT:\n"
        "- Price: from £54.99\n"
        "- Duration: About 60 minutes\n"
        "- Description: Annual MOT inspection."
    )
    assert result.facts["service"]["pricingStatus"] == "published"
    normalized = FactNormalizer().normalize(
        result_id="result-mot-price",
        call_id="call-mot-price",
        intent_id="intent-mot-price",
        tool="get_service_information",
        result=result,
    )
    assert normalized.responseObligation is not None
    assert normalized.responseObligation.kind == "service_detail"
    price_fact = next(
        fact for fact in normalized.facts if fact.field == "service.priceFromPence"
    )
    assert normalized.responseObligation.requiredFactIds == [price_fact.factId]


@pytest.mark.asyncio
async def test_service_price_question_returns_plain_live_information() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "get_service_information",
        {"q": "What would I pay to have my tyres fitted?"},
    )

    assert result.view_type is None
    assert result.view_payload is None
    assert result.text == (
        "Tyre fitting:\n"
        "- Price: priced on request\n"
        "- Duration: About 90 minutes\n"
        "- Description: Tyre replacement and balancing."
    )
    assert result.facts["service"]["pricingStatus"] == "priced_on_request"
    normalized = FactNormalizer().normalize(
        result_id="result-tyre-price",
        call_id="call-tyre-price",
        intent_id="intent-tyre-price",
        tool="get_service_information",
        result=result,
    )
    pricing_status = next(
        fact for fact in normalized.facts if fact.field == "service.pricingStatus"
    )
    assert pricing_status.displayValue == "Priced on request"
    assert normalized.responseObligation is not None
    assert normalized.responseObligation.requiredFactIds == [pricing_status.factId]


@pytest.mark.asyncio
async def test_workshop_slots_can_never_be_requested_without_a_service() -> None:
    dealership = FakeDealership()
    result = await ApplicationToolExecutor(dealership).execute("list_workshop_slots", {})

    assert result.view_type == "service_list"
    assert result.view_payload["selectionOnly"] is True
    assert result.view_payload["collectionPresentation"]["purpose"] == "choice"
    assert [item["label"] for item in result.view_payload["collectionPresentation"]["items"]] == [
        "MOT",
        "Full service",
        "Tyre fitting",
    ]
    assert result.text == (
        "Which workshop service would you like to book? You can also tell me "
        "what your car needs if you’re unsure."
    )
    assert dealership.last_workshop_filters is None


@pytest.mark.asyncio
async def test_dealership_service_selection_only_lists_services_with_live_slots() -> None:
    class LocationServices(FakeDealership):
        async def list_workshop_slots(self, filters):
            self.last_workshop_filters = filters
            return {
                "items": [
                    {"id": "slot-1", "serviceTypeId": "mot"},
                    {"id": "slot-2", "serviceTypeId": "tyre-fitting"},
                ]
            }

    dealership = LocationServices()
    result = await ApplicationToolExecutor(dealership).execute(
        "list_workshop_slots", {"dealershipId": "dealer-stockport"}
    )

    assert result.view_type == "service_list"
    assert result.view_payload["selectionOnly"] is True
    assert [item["id"] for item in result.view_payload["items"]] == [
        "mot",
        "tyre-fitting",
    ]
    assert result.view_payload["dealershipId"] == "dealer-stockport"
    assert all(
        suggestion["action"]["dealershipId"] == "dealer-stockport"
        for suggestion in result.view_payload["suggestions"]
    )
    assert dealership.last_workshop_filters == {
        "dealershipId": "dealer-stockport",
        "dateFrom": datetime.now(UTC).date().isoformat(),
    }


@pytest.mark.asyncio
async def test_empty_workshop_availability_offers_live_alternative_appointments() -> None:
    class NoLocalWorkshopSlots(FakeDealership):
        async def list_workshop_slots(self, filters):
            self.last_workshop_filters = filters
            if filters.get("dealershipId") == "dealer-stockport":
                return {"items": []}
            future = (datetime.now(UTC) + timedelta(days=1)).isoformat().replace("+00:00", "Z")
            return {
                "items": [
                    {
                        "id": "ws-slot-0001",
                        "startsAt": future,
                        "dealershipTown": "Manchester",
                    }
                ]
            }

    result = await ApplicationToolExecutor(NoLocalWorkshopSlots()).execute(
        "list_workshop_slots",
        {
            "dealershipTown": "Stockport",
            "serviceTypeName": "MOT",
            "schedulePreferenceMode": "clear",
        },
    )

    assert [item["id"] for item in result.view_payload["items"]] == ["ws-slot-0001"]
    assert result.facts["resolution"]["continuation"] == "offer_location_alternatives"
    assert result.alternative_offer["changes"] == [
        {
            "dimension": "Workshop location",
            "requested": "Stockport",
            "offered": "Manchester",
        }
    ]


@pytest.mark.asyncio
async def test_workshop_location_alternative_discloses_requested_and_offered_outcomes() -> None:
    class NoLocalWorkshopSlots(FakeDealership):
        async def list_workshop_slots(self, filters):
            if filters.get("dealershipId") == "dealer-stockport":
                return {"items": []}
            return {
                "items": [
                    {
                        "id": "ws-slot-0001",
                        "startsAt": "2026-09-07T09:00:00Z",
                        "dealershipTown": "Manchester",
                    }
                ]
            }

    result = await ApplicationToolExecutor(NoLocalWorkshopSlots()).execute(
        "list_workshop_slots",
        {
            "dealershipTown": "Stockport",
            "serviceTypeName": "MOT",
            "dateFrom": "2026-09-07",
            "dateTo": "2026-09-07",
        },
    )

    assert [item["id"] for item in result.view_payload["items"]] == ["ws-slot-0001"]
    assert result.alternative_offer["reasonCode"] == ("requested_workshop_location_no_availability")
    assert result.alternative_offer["offeredOutcome"] == (
        "Here are appointments at Manchester matching that schedule."
    )
    assert result.alternative_offer["changes"] == [
        {
            "dimension": "Workshop location",
            "requested": "Stockport",
            "offered": "Manchester",
        }
    ]


@pytest.mark.asyncio
async def test_failed_workshop_preference_returns_concrete_same_location_options_first() -> None:
    class SameLocationAlternatives(FakeDealership):
        def __init__(self):
            super().__init__()
            self.workshop_calls = []

        async def list_dealerships(self):
            return {"items": [{"id": "dealer-bolton", "town": "Bolton"}]}

        async def list_workshop_slots(self, filters):
            self.workshop_calls.append(dict(filters))
            if filters.get("dateTo"):
                return {"items": []}
            starts_at = (datetime.now(UTC) + timedelta(days=8)).replace(
                hour=14, minute=0, second=0, microsecond=0
            )
            return {
                "items": [
                    {
                        "id": "ws-slot-0099",
                        "startsAt": starts_at.isoformat(),
                        "dealershipId": "dealer-bolton",
                        "dealershipTown": "Bolton",
                        "serviceTypeId": "full-service",
                        "serviceTypeName": "Full service",
                    }
                ]
            }

    dealership = SameLocationAlternatives()
    requested = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    result = await ApplicationToolExecutor(dealership).execute(
        "refine_workshop_slots",
        {
            "dealershipTown": "Bolton",
            "serviceTypeName": "Full service",
            "dateFrom": requested,
            "dateTo": requested,
            "timeOfDay": "morning",
        },
    )

    assert [item["id"] for item in result.view_payload["items"]] == ["ws-slot-0099"]
    assert result.view_payload["requestedPreferenceHadNoMatch"] is True
    assert result.facts["resolution"]["continuation"] == "offer_schedule_alternatives"
    assert result.alternative_offer["reasonCode"] == "requested_schedule_no_match"
    assert result.view_payload["collectionViewType"] == "choice_list"
    assert result.view_payload["choiceEntityType"] == "appointment"
    assert result.view_payload["choiceField"] == "slotId"
    assert all(call.get("dealershipId") == "dealer-bolton" for call in dealership.workshop_calls)


@pytest.mark.asyncio
async def test_failed_test_drive_preference_offers_next_equivalent_model_slot() -> None:
    class EquivalentModelAlternatives(FakeDealership):
        def __init__(self):
            super().__init__()
            self.slot_calls = []

        async def get_vehicle(self, vehicle_id):
            return {
                "id": vehicle_id,
                "year": 2025 if vehicle_id == "veh-034" else 2022,
                "make": "Volvo",
                "model": "XC40",
                "variant": "Plus",
                "availability": "available",
                "dealershipId": "dealer-stockport",
                "dealershipName": "Northstar Stockport",
                "dealershipTown": "Stockport",
            }

        async def search_vehicles(self, filters):
            assert filters == {
                "make": "Volvo",
                "model": "XC40",
                "availability": "available",
                "pageSize": 100,
            }
            return {"items": [await self.get_vehicle("veh-010")]}

        async def list_test_drive_slots(self, filters):
            self.slot_calls.append(dict(filters))
            if filters.get("vehicleId") or filters.get("dateTo"):
                return {"items": []}
            starts_at = (datetime.now(UTC) + timedelta(days=2)).replace(
                hour=14, minute=0, second=0, microsecond=0
            )
            return {
                "items": [
                    {
                        "id": "td-next-equivalent",
                        "vehicleId": "veh-010",
                        "dealershipId": "dealer-stockport",
                        "dealershipTown": "Stockport",
                        "startsAt": starts_at.isoformat(),
                    }
                ]
            }

    dealership = EquivalentModelAlternatives()
    requested = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    result = await ApplicationToolExecutor(dealership).execute(
        "list_test_drive_slots",
        {
            "vehicleId": "veh-034",
            "dateFrom": requested,
            "dateTo": requested,
            "timeOfDay": "morning",
        },
    )

    assert [item["id"] for item in result.view_payload["items"]] == ["td-next-equivalent"]
    assert result.facts["resolution"]["continuation"] == "offer_equivalent_vehicle_slots"
    assert result.view_payload["requestedPreferenceHadNoMatch"] is True
    assert result.alternative_offer["reasonCode"] == "selected_vehicle_and_schedule_no_match"
    assert [change["dimension"] for change in result.alternative_offer["changes"]] == [
        "Vehicle listing",
        "Appointment schedule",
    ]
    assert dealership.slot_calls[-1] == {
        "dateFrom": datetime.now(UTC).date().isoformat(),
        "dealershipId": "dealer-stockport",
    }


@pytest.mark.asyncio
async def test_failed_workshop_and_schedule_offer_next_concrete_slot() -> None:
    class OtherWorkshopAlternatives(FakeDealership):
        def __init__(self):
            super().__init__()
            self.workshop_calls = []

        async def list_dealerships(self):
            return {
                "items": [
                    {
                        "id": "dealer-liverpool",
                        "name": "Northstar Liverpool",
                        "town": "Liverpool",
                    }
                ]
            }

        async def list_workshop_slots(self, filters):
            self.workshop_calls.append(dict(filters))
            if filters.get("dealershipId") or filters.get("dateTo"):
                return {"items": []}
            starts_at = (datetime.now(UTC) + timedelta(days=2)).replace(
                hour=11, minute=0, second=0, microsecond=0
            )
            return {
                "items": [
                    {
                        "id": "ws-next-workshop",
                        "dealershipId": "dealer-manchester",
                        "dealershipTown": "Manchester",
                        "serviceTypeId": "mot",
                        "serviceTypeName": "MOT",
                        "startsAt": starts_at.isoformat(),
                    }
                ]
            }

    dealership = OtherWorkshopAlternatives()
    requested = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    result = await ApplicationToolExecutor(dealership).execute(
        "refine_workshop_slots",
        {
            "serviceTypeId": "mot",
            "dealershipId": "dealer-liverpool",
            "dateFrom": requested,
            "dateTo": requested,
            "timeOfDay": "morning",
        },
    )

    assert [item["id"] for item in result.view_payload["items"]] == ["ws-next-workshop"]
    assert result.facts["resolution"]["continuation"] == (
        "offer_location_and_schedule_alternatives"
    )
    assert [change["dimension"] for change in result.alternative_offer["changes"]] == [
        "Workshop location",
        "Appointment schedule",
    ]
    assert result.alternative_offer["changes"][0]["requested"] == "Liverpool"
    assert dealership.workshop_calls[-1] == {
        "serviceTypeId": "mot",
        "dateFrom": datetime.now(UTC).date().isoformat(),
    }
    normalized = FactNormalizer().normalize(
        result_id="result-workshop-location-schedule-fallback",
        call_id="call-workshop-location-schedule-fallback",
        intent_id="intent-workshop-location-schedule-fallback",
        tool="refine_workshop_slots",
        result=result,
    )
    assert normalized.responseObligation is not None
    assert normalized.responseObligation.workflowCode == (
        "offer_location_and_schedule_alternatives"
    )
    assert normalized.responseObligation.choiceMode == "confirm_single"


@pytest.mark.asyncio
async def test_failed_workshop_location_offers_matching_appointment_not_location_retry() -> None:
    class MatchingOtherWorkshop(FakeDealership):
        def __init__(self):
            super().__init__()
            self.workshop_calls = []

        async def list_dealerships(self):
            return {
                "items": [
                    {
                        "id": "dealer-liverpool",
                        "name": "Northstar Liverpool",
                        "town": "Liverpool",
                    },
                    {
                        "id": "dealer-manchester",
                        "name": "Northstar Manchester",
                        "town": "Manchester",
                    },
                ]
            }

        async def list_workshop_slots(self, filters):
            self.workshop_calls.append(dict(filters))
            if filters.get("dealershipId"):
                return {"items": []}
            starts_at = (datetime.now(UTC) + timedelta(days=1)).replace(
                hour=14, minute=0, second=0, microsecond=0
            )
            return {
                "items": [
                    {
                        "id": "ws-matching-other-location",
                        "dealershipId": "dealer-manchester",
                        "dealershipName": "Northstar Manchester",
                        "dealershipTown": "Manchester",
                        "serviceTypeId": "mot",
                        "serviceTypeName": "MOT",
                        "startsAt": starts_at.isoformat(),
                    }
                ]
            }

    dealership = MatchingOtherWorkshop()
    requested = (datetime.now(UTC) + timedelta(days=1)).date().isoformat()
    result = await ApplicationToolExecutor(dealership).execute(
        "refine_workshop_slots",
        {
            "serviceTypeId": "mot",
            "dealershipId": "dealer-liverpool",
            "dateFrom": requested,
            "dateTo": requested,
            "timeOfDay": "afternoon",
        },
    )

    assert [item["id"] for item in result.view_payload["items"]] == ["ws-matching-other-location"]
    assert result.facts["resolution"]["continuation"] == "offer_location_alternatives"
    assert result.alternative_offer["changes"] == [
        {
            "dimension": "Workshop location",
            "requested": "Liverpool",
            "offered": "Manchester",
        }
    ]
    normalized = FactNormalizer().normalize(
        result_id="result-workshop-location-fallback",
        call_id="call-workshop-location-fallback",
        intent_id="intent-workshop-location-fallback",
        tool="refine_workshop_slots",
        result=result,
    )
    assert normalized.responseObligation is not None
    assert normalized.responseObligation.workflowCode == "offer_location_alternatives"
    assert normalized.responseObligation.choiceMode == "confirm_single"


@pytest.mark.asyncio
async def test_named_vehicle_comparison_resolves_each_query_from_live_stock() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "compare_vehicle_models", {"queries": ["BMW 1 Series", "BMW 3 Series"]}
    )

    assert result.view_type == "vehicle_comparison"
    assert [item["id"] for item in result.view_payload["items"]] == [
        "veh-019",
        "veh-020",
    ]
    assert result.facts["comparison"]["selectionBasis"].startswith("Newest")


@pytest.mark.asyncio
async def test_model_resolution_never_substitutes_a_derivative_for_the_requested_model() -> None:
    class MixedMiniInventory(FakeDealership):
        async def search_vehicles(self, filters):
            if filters.get("q") == "BMW 3 Series":
                return {
                    "items": [
                        {
                            "id": "veh-014",
                            "make": "BMW",
                            "model": "3 Series",
                            "variant": "320d M Sport",
                            "year": 2026,
                            "mileage": 10000,
                            "pricePence": 2100000,
                        }
                    ]
                }
            if filters.get("q") == "MINI Cooper":
                return {
                    "items": [
                        {
                            "id": "veh-041",
                            "make": "MINI",
                            "model": "Cooper",
                            "variant": "Cooper S Exclusive",
                            "year": 2025,
                            "mileage": 3500,
                            "pricePence": 2950000,
                        },
                        {
                            "id": "veh-042",
                            "make": "MINI",
                            "model": "Countryman",
                            "variant": "Cooper Classic",
                            "year": 2026,
                            "mileage": 9750,
                            "pricePence": 3225000,
                        },
                    ]
                }
            return {"items": []}

    result = await ApplicationToolExecutor(MixedMiniInventory()).execute(
        "compare_vehicle_models",
        {"queries": ["BMW 3 Series", "MINI Cooper"]},
    )

    assert result.view_type == "vehicle_comparison"
    assert [item["id"] for item in result.view_payload["items"]] == [
        "veh-014",
        "veh-041",
    ]


@pytest.mark.asyncio
async def test_broad_make_resolution_returns_distinct_model_candidates_not_stock_duplicates() -> (
    None
):
    class BroadInventory(FakeDealership):
        async def search_vehicles(self, filters):
            if filters.get("q") == "BMW":
                return {
                    "items": [
                        {
                            "id": "veh-014",
                            "make": "BMW",
                            "model": "3 Series",
                            "year": 2026,
                            "mileage": 30000,
                            "pricePence": 2100000,
                        },
                        {
                            "id": "veh-026",
                            "make": "BMW",
                            "model": "3 Series",
                            "year": 2024,
                            "mileage": 9000,
                            "pricePence": 2100000,
                        },
                        {
                            "id": "veh-049",
                            "make": "BMW",
                            "model": "1 Series",
                            "year": 2026,
                            "mileage": 3500,
                            "pricePence": 1800000,
                        },
                    ]
                }
            if filters.get("q") == "MINI Cooper":
                return {
                    "items": [
                        {
                            "id": "veh-041",
                            "make": "MINI",
                            "model": "Cooper",
                            "year": 2025,
                            "mileage": 3500,
                            "pricePence": 2950000,
                        }
                    ]
                }
            return {"items": []}

    result = await ApplicationToolExecutor(BroadInventory()).execute(
        "compare_vehicle_models", {"queries": ["BMW", "MINI Cooper"]}
    )

    assert result.view_type == "vehicle_list"
    assert result.facts["resolution"]["status"] == "ambiguous"
    assert result.facts["resolution"]["resolvedVehicleIds"] == [None, "veh-041"]
    assert [(item["make"], item["model"]) for item in result.view_payload["items"]] == [
        ("BMW", "1 Series"),
        ("BMW", "3 Series"),
    ]


@pytest.mark.asyncio
async def test_duplicate_vehicle_ids_do_not_render_a_fake_comparison() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "compare_vehicles", {"vehicleIds": ["veh-019", "veh-019"]}
    )

    assert result.view_type == "suggestion_list"
    assert "two different" in result.text


@pytest.mark.asyncio
async def test_vehicle_facets_are_derived_from_platform_inventory() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute("get_vehicle_facets", {})

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
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "estimate_part_exchange",
        {"registration": "AB19 XYZ", "mileage": 45_000, "condition": "good"},
    )

    assert result.view_type == "part_exchange_estimate"
    assert result.view_payload["estimateLowPence"] == 1_000_000
    assert result.view_payload["estimateNotice"] == "Indicative only."


@pytest.mark.asyncio
async def test_part_exchange_estimate_form_only_collects_valuation_inputs() -> None:
    result = await ApplicationToolExecutor(FakeDealership()).execute(
        "request_part_exchange_estimate_form",
        {"registration": "AB19 XYZ"},
    )

    assert result.view_type == "part_exchange_estimate_form"
    assert result.view_payload == {
        "version": 1,
        "kind": "part_exchange_estimate",
        "values": {"registration": "AB19 XYZ"},
        "secureFields": ["mileage", "condition"],
        "secureInputReady": True,
        "inputGuidance": {
            "mileage": {
                "label": "current mileage",
                "prompt": "What’s the vehicle’s current mileage? Enter the dashboard reading.",
                "example": "24,000 miles",
            },
            "condition": {
                "label": "vehicle condition",
                "prompt": "How would you describe the vehicle’s condition? Choose Excellent, Good, or Fair.",
                "choices": ["Excellent", "Good", "Fair"],
            },
        },
    }
