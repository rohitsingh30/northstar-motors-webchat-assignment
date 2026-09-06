import pytest

from webchat.orchestration.presentation.suggestions import (
    current_page_vehicle_suggestions,
    dealership_contact_suggestions,
    dealership_suggestions,
    no_vehicle_results_suggestions,
    offer_selection_suggestions,
    offer_suggestions,
    opening_hours_suggestions,
    service_type_suggestions,
    vehicle_availability_suggestions,
    vehicle_comparison_suggestions,
    vehicle_facet_prompt,
    vehicle_facet_suggestions,
    vehicle_filter_summary,
    vehicle_search_suggestions,
    workshop_booking_status_prompt,
    workshop_booking_status_suggestions,
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
        "Book a test drive",
        "Choose fuel",
    ]
    assert [item["label"] for item in final_cheapest_page] == [
        "Compare these vehicles",
        "Book a test drive",
        "Choose fuel",
        "Set budget",
    ]
    assert first_page[0]["action"] == {"type": "next_vehicle_page"}
    assert first_page[1]["action"] == {"type": "compare_displayed_vehicles"}


def test_filtered_vehicle_results_offer_change_and_clear_controls() -> None:
    suggestions = vehicle_search_suggestions(
        query={"fuelType": "Hybrid", "sort": "newest"},
        item_count=3,
        page=1,
        page_size=3,
        total=15,
    )

    assert [item["label"] for item in suggestions] == [
        "Show me more",
        "Compare these vehicles",
        "Book a test drive",
        "Change fuel",
    ]
    assert suggestions[3]["action"] == {
        "type": "choose_vehicle_filter",
        "vehicleFilter": "fuelType",
    }


def test_vehicle_filter_summary_names_every_active_constraint() -> None:
    assert vehicle_filter_summary(
        {
            "make": "BMW",
            "fuelType": "Hybrid",
            "bodyStyle": "SUV",
            "maxPricePence": 3_500_000,
            "dealershipTown": "Stockport",
            "sort": "priceAsc",
        }
    ) == (
        "Make: BMW; Fuel: Hybrid; Body style: SUV; Location: Stockport; "
        "Maximum price: £35,000; Order: lowest price first"
    )


def test_page_scoped_vehicle_results_keep_typed_follow_up_chips() -> None:
    suggestions = current_page_vehicle_suggestions(3)

    assert suggestions == [
        {
            "label": "Compare these vehicles",
            "text": "compare the vehicles currently shown",
            "action": {"type": "compare_displayed_vehicles"},
        },
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
    ]


def test_suggestions_cover_useful_cross_capability_next_steps() -> None:
    groups = [
        no_vehicle_results_suggestions(),
        dealership_suggestions(),
        opening_hours_suggestions(),
        workshop_location_suggestions(),
        offer_selection_suggestions(
            [{"id": "offer-01", "make": "BMW", "model": "1 Series"}]
        ),
    ]

    assert all(groups)
    assert all({"label", "text"}.issubset(suggestion) for group in groups for suggestion in group)


@pytest.mark.parametrize(
    ("status", "labels", "prompt"),
    [
        (
            "confirmed",
            ["Edit booking", "Cancel booking"],
            "Would you like to edit or cancel this booking?",
        ),
        (
            "cancelled",
            ["Book an appointment", "Find another booking"],
            "Would you like to book another appointment or find another booking?",
        ),
    ],
)
def test_workshop_booking_suggestions_follow_authoritative_status(
    status: str, labels: list[str], prompt: str
) -> None:
    suggestions = workshop_booking_status_suggestions(status)

    assert [item["label"] for item in suggestions] == labels
    assert workshop_booking_status_prompt(status) == prompt
    assert workshop_booking_status_suggestions("completed") == []
    assert workshop_booking_status_prompt("completed") is None


def test_dealership_contact_choices_are_four_typed_application_actions() -> None:
    suggestions = dealership_contact_suggestions()

    assert [suggestion["label"] for suggestion in suggestions] == [
        "Request a callback",
        "Send a message",
        "Contact details",
        "Opening hours",
    ]
    assert [suggestion["action"]["type"] for suggestion in suggestions] == [
        "start_callback",
        "start_dealership_message",
        "show_dealerships",
        "show_opening_hours",
    ]


def test_dealership_information_next_steps_keep_executable_contact_actions() -> None:
    dealership = dealership_suggestions()
    hours = opening_hours_suggestions()

    assert next(item for item in dealership if item["label"] == "Request a callback")[
        "action"
    ] == {"type": "start_callback"}
    assert next(item for item in dealership if item["label"] == "Leave a message")[
        "action"
    ] == {"type": "start_dealership_message"}
    assert next(item for item in hours if item["label"] == "Dealership details")[
        "action"
    ] == {"type": "show_dealerships"}
    assert next(item for item in hours if item["label"] == "Leave a message")[
        "action"
    ] == {"type": "start_dealership_message"}


def test_single_offer_has_a_typed_sales_enquiry_action() -> None:
    suggestions = offer_suggestions({"id": "offer-07"})

    assert suggestions[0] == {
        "label": "Enquire about this offer",
        "text": "I want to enquire about this offer",
        "action": {"type": "start_offer_enquiry", "offerId": "offer-07"},
    }


def test_offer_list_choices_are_bound_to_the_offers_being_shown() -> None:
    suggestions = offer_selection_suggestions(
        [
            {"id": "offer-01", "make": "BMW", "model": "1 Series"},
            {"id": "offer-02", "make": "BMW", "model": "3 Series"},
        ]
    )

    assert [item["label"] for item in suggestions] == ["BMW 1 Series", "BMW 3 Series"]
    assert [item["action"] for item in suggestions] == [
        {"type": "view_offer", "offerId": "offer-01"},
        {"type": "view_offer", "offerId": "offer-02"},
    ]


def test_comparison_actions_book_the_compared_vehicles_instead_of_cross_selling() -> None:
    suggestions = vehicle_comparison_suggestions(
        [
            {"id": "veh-014", "make": "BMW", "model": "3 Series"},
            {"id": "veh-041", "make": "MINI", "model": "Countryman"},
        ]
    )

    assert [item["label"] for item in suggestions] == [
        "Book BMW 3 Series",
        "Book MINI Countryman",
    ]
    assert [item["action"] for item in suggestions] == [
        {"type": "select_test_drive_vehicle", "vehicleId": "veh-014"},
        {"type": "select_test_drive_vehicle", "vehicleId": "veh-041"},
    ]


def test_three_compared_vehicles_keep_every_booking_action_and_add_an_exit() -> None:
    suggestions = vehicle_comparison_suggestions(
        [
            {"id": "veh-014", "make": "BMW", "model": "3 Series"},
            {"id": "veh-042", "make": "MINI", "model": "Countryman"},
            {"id": "veh-049", "make": "BMW", "model": "1 Series"},
        ]
    )

    assert [item["label"] for item in suggestions] == [
        "Book BMW 3 Series",
        "Book MINI Countryman",
        "Book BMW 1 Series",
        "Find another car",
    ]
    assert suggestions[2]["action"] == {
        "type": "select_test_drive_vehicle",
        "vehicleId": "veh-049",
    }


def test_workshop_location_suggestions_are_next_steps_not_a_repeat() -> None:
    labels = [item["label"] for item in workshop_location_suggestions()]

    assert labels == [
        "Book a service",
        "Find my booking",
        "Service types",
        "Request a callback",
    ]
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
        {
            "label": "Request a callback",
            "text": "please have the service department call me",
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
        "Find another car",
        "Current offers",
    ]
    assert reserved[0]["action"]["type"] == "start_vehicle_interest"
    assert [item["label"] for item in sold] == [
        "Send a sales enquiry",
        "Find another car",
    ]


def test_vehicle_facet_prompt_does_not_offer_unavailable_options() -> None:
    assert (
        vehicle_facet_prompt(
            "transmissions", [{"label": "Automatic", "text": "I prefer Automatic transmission"}]
        )
        == "Northstar currently has Automatic gearbox options. Choose it below to see matching cars."
    )
