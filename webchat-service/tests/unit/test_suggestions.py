import pytest

from webchat.orchestration.presentation.suggestions import (
    dealership_suggestions,
    no_vehicle_results_suggestions,
    offer_suggestions,
    opening_hours_suggestions,
    service_type_suggestions,
    vehicle_availability_suggestions,
    vehicle_clarification_suggestions,
    vehicle_facet_prompt,
    vehicle_facet_suggestions,
    vehicle_preference_dimension,
    vehicle_search_suggestions,
    workshop_location_suggestions,
    workshop_no_availability_suggestions,
)


def test_vehicle_suggestions_follow_result_state() -> None:
    first_page = vehicle_search_suggestions(
        query={"sort": "newest"}, item_count=3, page=1, page_size=3, total=17
    )
    final_cheapest_page = vehicle_search_suggestions(
        query={"sort": "priceAsc"}, item_count=2, page=5, page_size=3, total=14
    )

    assert [item["label"] for item in first_page] == [
        "Show me more",
        "Compare these vehicles",
        "Cheapest first",
    ]
    assert [item["label"] for item in final_cheapest_page] == ["Compare these vehicles"]
    assert first_page[0]["action"] == {"type": "next_vehicle_page"}
    assert first_page[1]["action"] == {"type": "compare_displayed_vehicles"}


def test_suggestions_cover_useful_cross_capability_next_steps() -> None:
    groups = [
        no_vehicle_results_suggestions(),
        dealership_suggestions(),
        opening_hours_suggestions(),
        workshop_location_suggestions(),
        offer_suggestions(),
    ]

    assert all(groups)
    assert all(
        set(suggestion) == {"label", "text"}
        for group in groups
        for suggestion in group
    )


def test_single_offer_has_a_typed_sales_enquiry_action() -> None:
    suggestions = offer_suggestions({"id": "offer-07"})

    assert suggestions[0] == {
        "label": "Enquire about this offer",
        "text": "I want to enquire about this offer",
        "action": {"type": "start_offer_enquiry", "offerId": "offer-07"},
    }


def test_workshop_location_suggestions_are_next_steps_not_a_repeat() -> None:
    labels = [item["label"] for item in workshop_location_suggestions()]

    assert labels == ["Book a service", "Find my booking"]
    assert "Workshop locations" not in labels


def test_empty_workshop_availability_offers_live_alternative_locations() -> None:
    suggestions = workshop_no_availability_suggestions(
        [
            {"dealershipTown": "Manchester", "dealershipId": "dealer-manchester"},
            {"dealershipTown": "Bolton", "dealershipId": "dealer-bolton"},
            {"dealershipTown": "Manchester", "dealershipId": "dealer-manchester"},
        ],
        "MOT",
        "mot",
    )

    assert suggestions == [
        {
            "label": "Try Manchester",
            "text": "Find an appointment for MOT in Manchester",
            "action": {
                "type": "try_workshop_location",
                "serviceTypeId": "mot",
                "dealershipId": "dealer-manchester",
            },
        },
        {
            "label": "Try Bolton",
            "text": "Find an appointment for MOT in Bolton",
            "action": {
                "type": "try_workshop_location",
                "serviceTypeId": "mot",
                "dealershipId": "dealer-bolton",
            },
        },
        {
            "label": "Choose another service",
            "text": "find a workshop appointment",
            "action": {"type": "show_workshop_services"},
        },
    ]


def test_live_service_types_become_bookable_choice_chips() -> None:
    suggestions = service_type_suggestions(
        [{"id": "mot", "name": "MOT"}, {"id": "full-service", "name": "Full service"}]
    )

    assert suggestions == [
        {
            "label": "MOT",
            "text": "Book service: MOT",
            "action": {"type": "select_workshop_service", "serviceTypeId": "mot"},
        },
        {
            "label": "Full service",
            "text": "Book service: Full service",
            "action": {
                "type": "select_workshop_service",
                "serviceTypeId": "full-service",
            },
        },
    ]


def test_vehicle_clarification_suggestions_are_only_for_discovery_clarification() -> None:
    suggestions = vehicle_clarification_suggestions(
        "help me find another car",
        "Tell me what matters most, or choose a starting point below.",
    )

    assert [item["label"] for item in suggestions] == [
        "Set a budget",
        "Choose fuel",
        "Choose body style",
        "Choose gearbox",
    ]
    assert vehicle_clarification_suggestions("hello", "How can I help?") == []
    follow_up = vehicle_clarification_suggestions(
        "I prefer petrol, hybrid, or electric",
        "What sort of budget and body style are you considering?",
    )
    assert follow_up == []
    assert vehicle_clarification_suggestions(
        "Give me an indicative part-exchange estimate",
        "To give an indicative estimate, I need the registration, mileage, and condition.",
    ) == []


@pytest.mark.parametrize(
    ("question", "dimension"),
    [
        ("Would you prefer an automatic or a manual gearbox?", "transmissions"),
        ("Which fuel type would you prefer?", "fuelTypes"),
        ("What body style suits you?", "bodyStyles"),
        ("Do you have a preferred manufacturer?", "makes"),
    ],
)
def test_direct_vehicle_questions_select_a_live_facet(question: str, dimension: str) -> None:
    assert vehicle_preference_dimension(question) == dimension


def test_vehicle_facet_suggestions_use_only_live_values() -> None:
    suggestions = vehicle_facet_suggestions("transmissions", ["Automatic", "Direct"])

    assert suggestions == [
        {
            "label": "Automatic",
            "text": "I prefer Automatic transmission",
            "action": {
                "type": "apply_vehicle_preference",
                "vehicleFilter": "transmission",
                "vehicleFilterValue": "Automatic",
            },
        },
        {
            "label": "Direct",
            "text": "I prefer Direct transmission",
            "action": {
                "type": "apply_vehicle_preference",
                "vehicleFilter": "transmission",
                "vehicleFilterValue": "Direct",
            },
        },
    ]


def test_budget_suggestions_use_only_live_inventory_thresholds() -> None:
    suggestions = vehicle_facet_suggestions("budgets", [3_000_000, 4_500_000])

    assert suggestions == [
        {
            "label": "Under £30,000",
            "text": "Show me cars under £30,000",
            "action": {
                "type": "apply_vehicle_preference",
                "vehicleFilter": "maxPricePence",
                "vehicleFilterValue": 3_000_000,
            },
        },
        {
            "label": "Under £45,000",
            "text": "Show me cars under £45,000",
            "action": {
                "type": "apply_vehicle_preference",
                "vehicleFilter": "maxPricePence",
                "vehicleFilterValue": 4_500_000,
            },
        },
    ]
    assert vehicle_facet_prompt("budgets", suggestions) == "Choose a maximum budget below."


def test_vehicle_status_suggestions_follow_platform_permissions() -> None:
    reserved = vehicle_availability_suggestions(
        {
            "vehicleId": "veh-007",
            "canBookTestDrive": False,
            "canRegisterInterest": True,
            "canEnquire": True,
        }
    )
    sold = vehicle_availability_suggestions(
        {
            "vehicleId": "veh-013",
            "canBookTestDrive": False,
            "canRegisterInterest": False,
            "canEnquire": True,
        }
    )

    assert [item["label"] for item in reserved] == [
        "Register interest",
        "Send a sales enquiry",
    ]
    assert reserved[0]["action"]["type"] == "start_vehicle_interest"
    assert [item["label"] for item in sold] == ["Send a sales enquiry"]


def test_vehicle_facet_prompt_does_not_offer_unavailable_options() -> None:
    assert vehicle_facet_prompt(
        "transmissions", [{"label": "Automatic", "text": "I prefer Automatic transmission"}]
    ) == "Northstar currently has Automatic gearbox options. Choose it below to see matching cars."
