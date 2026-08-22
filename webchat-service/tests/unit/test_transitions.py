from webchat.integrations.contracts import TurnPlan
from webchat.orchestration.transitions import TransitionController


def resolve(
    plan: TurnPlan,
    state=None,
    *,
    search=None,
    vehicles=None,
    offers=None,
    page_vehicles=None,
    text="customer wording",
):
    return TransitionController().resolve(
        plan,
        state or {},
        user_text=text,
        displayed_vehicles=vehicles or [],
        vehicle_search_state=search,
        displayed_offers=offers or [],
        page_vehicles=page_vehicles or [],
    )


def test_page_scoped_superlative_never_becomes_a_global_vehicle_search() -> None:
    page_vehicles = [
        {"vehicleId": "veh-020", "mileage": 22_250},
        {"vehicleId": "veh-032", "mileage": 47_250},
        {"vehicleId": "veh-044", "mileage": 22_250},
    ]
    transition = resolve(
        TurnPlan(
            "vehicle_search",
            {"referenceScope": "currentPage", "sort": "mileageAsc", "resultLimit": 1},
        ),
        page_vehicles=page_vehicles,
        text="Which has the least mileage among these?",
    )

    assert transition.tool_call.name == "select_page_vehicles"
    assert transition.tool_call.arguments == {
        "vehicleIds": ["veh-020", "veh-032", "veh-044"],
        "sort": "mileageAsc",
        "limit": 1,
    }


def test_page_scope_without_structured_entities_fails_closed() -> None:
    transition = resolve(
        TurnPlan("vehicle_search", {"referenceScope": "currentPage", "sort": "priceAsc"}),
        text="Show the cheapest car on this page",
    )

    assert transition.tool_call is None
    assert "can't identify any vehicle results" in transition.response


def test_named_workshop_booking_goes_straight_to_multi_location_slots() -> None:
    transition = resolve(
        TurnPlan(
            "workshop_booking",
            {"serviceQuery": "whatever the customer called the service"},
            "Which town would you like?",
        )
    )

    assert transition.response == ""
    assert transition.tool_call.name == "list_workshop_slots"
    assert transition.tool_call.arguments == {
        "serviceTypeName": "whatever the customer called the service"
    }


def test_workshop_information_uses_selected_live_service_id_not_booking_slots() -> None:
    transition = resolve(
        TurnPlan("workshop_service_information", {"reuseActiveEntity": True}),
        {
            "domain": "workshop",
            "entities": {"serviceTypeId": "live-service-id"},
        },
    )

    assert transition.tool_call.name == "get_service_information"
    assert transition.tool_call.arguments == {"serviceTypeId": "live-service-id"}


def test_switching_workshop_service_replaces_the_active_service() -> None:
    transition = resolve(
        TurnPlan("workshop_booking", {"serviceQuery": "the newly requested work"}),
        {
            "domain": "workshop",
            "entities": {
                "serviceTypeId": "previous-live-service-id",
                "serviceQuery": "the old work",
            },
            "constraints": {},
        },
    )

    assert transition.state["entities"]["serviceQuery"] == "the newly requested work"
    assert "serviceTypeId" not in transition.state["entities"]
    assert transition.tool_call.arguments["serviceTypeName"] == "the newly requested work"


def test_unresolved_new_booking_never_silently_reuses_the_previous_service() -> None:
    transition = resolve(
        TurnPlan("workshop_booking"),
        {
            "domain": "workshop",
            "entities": {"serviceTypeId": "previous-live-service-id"},
        },
    )

    assert transition.tool_call.name == "list_service_types"
    assert transition.tool_call.arguments == {}


def test_explicit_service_wording_overrides_a_previous_service_id_for_information() -> None:
    transition = resolve(
        TurnPlan("workshop_service_information", {"serviceQuery": "the new work"}),
        {
            "domain": "workshop",
            "entities": {"serviceTypeId": "previous-live-service-id"},
        },
    )

    assert transition.tool_call.name == "get_service_information"
    assert transition.tool_call.arguments == {"q": "the new work"}


def test_generic_part_exchange_is_always_the_three_field_estimate_form() -> None:
    transition = resolve(TurnPlan("part_exchange_estimate"))

    assert transition.tool_call.name == "request_part_exchange_estimate_form"
    assert transition.tool_call.arguments == {}


def test_complete_part_exchange_estimate_skips_the_form() -> None:
    fields = {"registration": "AB19 XYZ", "mileage": 45_000, "condition": "good"}
    transition = resolve(TurnPlan("part_exchange_estimate", fields))

    assert transition.tool_call.name == "estimate_part_exchange"
    assert transition.tool_call.arguments == fields


def test_existing_booking_changes_always_open_private_lookup() -> None:
    transition = resolve(TurnPlan("workshop_booking_change"))

    assert transition.tool_call.name == "request_workshop_booking_lookup_form"
    assert transition.tool_call.arguments == {"mode": "amend"}


def test_existing_booking_lookup_modes_are_preserved_by_the_application() -> None:
    lookup = resolve(TurnPlan("workshop_booking_lookup"))
    cancel = resolve(TurnPlan("workshop_booking_cancel"))

    assert lookup.tool_call.arguments == {"mode": "lookup"}
    assert cancel.tool_call.arguments == {"mode": "cancel"}


def test_more_vehicles_uses_the_exact_next_page_and_existing_filters() -> None:
    transition = resolve(
        TurnPlan("vehicle_more"),
        search={"filters": {"fuelType": "Hybrid", "sort": "priceAsc"}, "page": 2},
    )

    assert transition.tool_call.name == "search_vehicles"
    assert transition.tool_call.arguments == {
        "fuelType": "Hybrid",
        "sort": "priceAsc",
        "page": 3,
    }


def test_comparison_uses_the_semantically_resolved_displayed_ids() -> None:
    vehicles = [
        {"vehicleId": "veh-001"},
        {"vehicleId": "veh-002"},
        {"vehicleId": "veh-003"},
    ]
    transition = resolve(
        TurnPlan("vehicle_compare", {"vehicleIds": ["veh-002", "veh-003"]}),
        vehicles=vehicles,
    )

    assert transition.tool_call.name == "compare_vehicles"
    assert transition.tool_call.arguments == {"vehicleIds": ["veh-002", "veh-003"]}


def test_budget_preference_is_a_single_typed_choice_group() -> None:
    transition = resolve(
        TurnPlan("vehicle_preferences", {"preferenceDimension": "budgets"})
    )

    assert transition.suggestion_dimension == "budgets"
    assert transition.state["stage"] == "choosing_preference"


def test_selected_live_preference_becomes_a_search_instead_of_reopening_choices() -> None:
    transition = resolve(
        TurnPlan(
            "vehicle_preference_selection",
            {"preferenceDimension": "fuelTypes", "preferenceValue": "Electric"},
        ),
        {
            "domain": "vehicle",
            "intent": "vehicle_preferences",
            "stage": "choosing_preference",
            "entities": {"preferenceDimension": "fuelTypes"},
            "constraints": {"bodyStyle": "SUV"},
        },
    )

    assert transition.tool_call.name == "search_vehicles"
    assert transition.tool_call.arguments == {
        "bodyStyle": "SUV",
        "fuelType": "Electric",
    }


def test_budget_search_drops_conversational_query_covered_by_typed_filter() -> None:
    transition = resolve(
        TurnPlan(
            "vehicle_preference_selection",
            {
                "query": "cars under £35,000",
                "preferenceDimension": "budgets",
                "preferenceValue": 3_500_000,
                "maxPricePence": 3_500_000,
                "sort": "newest",
            },
        )
    )

    assert transition.tool_call.arguments == {
        "maxPricePence": 3_500_000,
        "sort": "newest",
    }


def test_fuel_search_drops_conversational_query_covered_by_typed_filter() -> None:
    transition = resolve(
        TurnPlan(
            "vehicle_preference_selection",
            {
                "query": "I prefer Hybrid",
                "preferenceDimension": "fuelTypes",
                "preferenceValue": "Hybrid",
                "fuelType": "Hybrid",
            },
        )
    )

    assert transition.tool_call.arguments == {"fuelType": "Hybrid"}


def test_vehicle_identity_query_is_retained_alongside_typed_filters() -> None:
    transition = resolve(
        TurnPlan(
            "vehicle_search",
            {
                "query": "BMW 3 Series cars under £35,000",
                "maxPricePence": 3_500_000,
            },
        )
    )

    assert transition.tool_call.arguments == {
        "q": "bmw 3 series",
        "maxPricePence": 3_500_000,
    }


def test_find_another_car_resets_old_filters_before_a_new_preference_choice() -> None:
    old_search = {
        "domain": "vehicle",
        "intent": "vehicle_search",
        "stage": "viewing_results",
        "entities": {},
        "constraints": {
            "fuelType": "Electric",
            "bodyStyle": "Hatchback",
            "maxPricePence": 3_500_000,
        },
    }

    unmarked_picker = resolve(
        TurnPlan("vehicle_preferences", {"preferenceDimension": "bodyStyles"}),
        old_search,
    )
    assert unmarked_picker.state["constraints"] == {}

    refining_picker = resolve(
        TurnPlan(
            "vehicle_preferences",
            {"preferenceDimension": "bodyStyles", "refineCurrentSearch": True},
        ),
        old_search,
    )
    assert refining_picker.state["constraints"] == old_search["constraints"]

    starting_point = resolve(TurnPlan("vehicle_preferences"), old_search)
    assert starting_point.suggestion_dimension == "startingPoint"
    assert starting_point.state["constraints"] == {}

    body_picker = resolve(
        TurnPlan("vehicle_preferences", {"preferenceDimension": "bodyStyles"}),
        starting_point.state,
    )
    estate = resolve(
        TurnPlan(
            "vehicle_preference_selection",
            {"preferenceDimension": "bodyStyles", "preferenceValue": "Estate"},
        ),
        body_picker.state,
    )

    assert estate.tool_call.name == "search_vehicles"
    assert estate.tool_call.arguments == {"bodyStyle": "Estate"}


def test_standalone_preference_value_cannot_inherit_an_old_vehicle_search() -> None:
    transition = resolve(
        TurnPlan(
            "vehicle_preference_selection",
            {"preferenceDimension": "bodyStyles", "preferenceValue": "Estate"},
        ),
        {
            "domain": "vehicle",
            "intent": "vehicle_search",
            "stage": "viewing_results",
            "entities": {},
            "constraints": {"fuelType": "Electric", "maxPricePence": 3_500_000},
        },
    )

    assert transition.tool_call.arguments == {"bodyStyle": "Estate"}


def test_preference_action_inherits_only_from_its_own_active_screen() -> None:
    action = {
        "type": "apply_vehicle_preference",
        "vehicleFilter": "bodyStyle",
        "vehicleFilterValue": "Estate",
    }
    stale_state = {
        "domain": "vehicle",
        "intent": "vehicle_search",
        "stage": "viewing_results",
        "entities": {},
        "constraints": {"fuelType": "Electric"},
    }
    owned_state = {
        "domain": "vehicle",
        "intent": "vehicle_preferences",
        "stage": "choosing_preference",
        "entities": {"preferenceDimension": "bodyStyles"},
        "constraints": {"fuelType": "Hybrid"},
    }

    assert TransitionController.preference_action_filters(stale_state, action) == {
        "bodyStyle": "Estate"
    }
    assert TransitionController.preference_action_filters(owned_state, action) == {
        "fuelType": "Hybrid",
        "bodyStyle": "Estate",
    }


def test_fresh_vehicle_search_cannot_inherit_old_filters() -> None:
    transition = resolve(
        TurnPlan("vehicle_search", {"make": "BMW"}),
        {
            "domain": "vehicle",
            "constraints": {"fuelType": "Hybrid", "maxPricePence": 4_500_000},
        },
    )

    assert transition.tool_call.arguments == {"make": "BMW"}


def test_self_contained_search_cannot_be_scoped_to_page_or_stale_results() -> None:
    transition = resolve(
        TurnPlan(
            "vehicle_search",
            {
                "referenceScope": "currentPage",
                "refineCurrentSearch": True,
                "maxPricePence": 3_500_000,
            },
        ),
        {
            "domain": "vehicle",
            "constraints": {"make": "Land Rover", "fuelType": "Hybrid"},
        },
        page_vehicles=[{"vehicleId": "veh-001"}, {"vehicleId": "veh-002"}],
        text="Show me cars under £35,000.",
    )

    assert transition.tool_call.name == "search_vehicles"
    assert transition.tool_call.arguments == {"maxPricePence": 3_500_000}


def test_explicit_vehicle_refinement_reuses_and_can_clear_filters() -> None:
    transition = resolve(
        TurnPlan(
            "vehicle_search",
            {
                "make": "Volvo",
                "refineCurrentSearch": True,
                "clearVehicleFilters": ["maxPricePence"],
            },
        ),
        {
            "domain": "vehicle",
            "constraints": {"fuelType": "Hybrid", "maxPricePence": 4_500_000},
        },
        text="Actually, keep the hybrid filter, remove the budget and make it Volvo.",
    )

    assert transition.tool_call.arguments == {"fuelType": "Hybrid", "make": "Volvo"}


def test_new_vehicle_query_overrides_old_active_vehicle() -> None:
    state = {"domain": "vehicle", "entities": {"vehicleId": "veh-001"}}

    availability = resolve(
        TurnPlan("vehicle_availability", {"query": "BMW 3 Series"}), state
    )
    test_drive = resolve(TurnPlan("test_drive", {"query": "Volvo XC40"}), state)

    assert availability.tool_call.name == "search_vehicles"
    assert availability.tool_call.arguments == {"q": "BMW 3 Series"}
    assert test_drive.tool_call.name == "search_vehicles"
    assert test_drive.tool_call.arguments == {"q": "Volvo XC40"}


def test_active_vehicle_is_reused_only_for_an_explicit_reference() -> None:
    state = {"domain": "vehicle", "entities": {"vehicleId": "veh-001"}}

    explicit = resolve(
        TurnPlan("vehicle_availability", {"reuseActiveEntity": True}), state
    )
    unrelated = resolve(TurnPlan("vehicle_availability"), state)

    assert explicit.tool_call.name == "get_vehicle_availability"
    assert explicit.tool_call.arguments == {"id": "veh-001"}
    assert unrelated.tool_call.name == "search_vehicles"
    assert unrelated.tool_call.arguments == {"q": "customer wording"}


def test_show_more_without_results_asks_for_a_search_instead_of_restarting_page_one() -> None:
    transition = resolve(TurnPlan("vehicle_more"))

    assert transition.tool_call is None
    assert transition.suggestion_dimension == "startingPoint"
    assert "isn't an active" in transition.response


def test_show_more_keeps_only_the_actual_search_filters_for_later_refinement() -> None:
    transition = resolve(
        TurnPlan("vehicle_more"),
        {
            "domain": "vehicle",
            "intent": "vehicle_search",
            "constraints": {"fuelType": "Petrol"},
        },
        search={
            "filters": {"bodyStyle": "Estate", "sort": "priceAsc"},
            "page": 2,
        },
    )

    assert transition.tool_call.arguments == {
        "bodyStyle": "Estate",
        "sort": "priceAsc",
        "page": 3,
    }
    assert transition.state["constraints"] == {
        "bodyStyle": "Estate",
        "sort": "priceAsc",
    }


def test_workshop_actions_do_not_inherit_old_service_or_location_state() -> None:
    controller = TransitionController()
    stale = {
        "domain": "workshop",
        "intent": "workshop_booking",
        "stage": "choosing_time",
        "entities": {"serviceTypeId": "diagnostic"},
        "constraints": {"dealershipId": "northstar-manchester"},
    }

    services = controller.advance_action(
        stale, {"type": "show_workshop_services"}, "service_list"
    )
    selected = controller.advance_action(
        stale,
        {"type": "select_workshop_service", "serviceTypeId": "tyre-fitting"},
        "slot_list",
    )

    assert services["entities"] == {}
    assert services["constraints"] == {}
    assert selected["entities"] == {"serviceTypeId": "tyre-fitting"}
    assert selected["constraints"] == {}


def test_unresolved_comparison_is_a_recoverable_clarification() -> None:
    transition = resolve(TurnPlan("vehicle_compare"))

    assert transition.tool_call is None
    assert transition.state["stage"] == "clarifying"


def test_sales_enquiry_uses_a_platform_valid_default_type() -> None:
    transition = resolve(TurnPlan("sales_enquiry"))

    assert transition.tool_call.name == "prepare_sales_enquiry"
    assert transition.tool_call.arguments == {"enquiryType": "general"}


def test_offer_purchase_cannot_fall_into_reserved_vehicle_interest() -> None:
    offer = {
        "offerId": "offer-07",
        "make": "Jaguar",
        "model": "F-PACE",
        "productType": "PCP",
    }
    transition = resolve(
        TurnPlan("vehicle_interest"),
        {
            "domain": "offers",
            "intent": "offers_list",
            "stage": "viewing_offers",
            "entities": {},
            "constraints": {},
        },
        offers=[offer],
    )

    assert transition.tool_call.name == "prepare_sales_enquiry"
    assert transition.tool_call.arguments == {
        "enquiryType": "finance",
        "message": "I am interested in the currently published Jaguar F-PACE PCP offer.",
    }
    assert transition.state["entities"] == {"offerId": "offer-07"}


def test_reserved_vehicle_interest_without_vehicle_context_still_clarifies() -> None:
    transition = resolve(TurnPlan("vehicle_interest"))

    assert transition.tool_call is None
    assert "reserved vehicle" in transition.response


def test_non_workflow_answer_preserves_active_state() -> None:
    state = {
        "domain": "vehicle",
        "intent": "vehicle_search",
        "stage": "viewing_results",
        "entities": {"vehicleId": "veh-001"},
        "constraints": {"fuelType": "Hybrid"},
    }
    transition = resolve(TurnPlan("general_response", response="You're welcome."), state)

    assert transition.state["entities"] == state["entities"]
    assert transition.state["constraints"] == state["constraints"]


def test_verified_booking_change_and_cancel_do_not_reopen_private_lookup() -> None:
    state = {
        "domain": "workshop",
        "stage": "verified",
        "entities": {"serviceQuery": "MOT"},
    }

    change = resolve(TurnPlan("workshop_booking_change"), state)
    cancel = resolve(TurnPlan("workshop_booking_cancel"), state)

    assert change.tool_call.name == "prepare_workshop_amendment"
    assert cancel.tool_call.name == "prepare_workshop_cancellation"
