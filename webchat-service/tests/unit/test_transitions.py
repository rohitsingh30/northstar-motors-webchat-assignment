import pytest

from webchat.integrations.contracts import TurnPlan
from webchat.orchestration.planning.ontology import (
    ALL_GOALS,
    GoalKey,
    Goals,
    normalize_workflow_state,
)
from webchat.orchestration.planning.transitions import TransitionController


def semantic_plan(
    key: GoalKey,
    arguments: dict | None = None,
    response: str = "",
) -> TurnPlan:
    return TurnPlan(key.domain, key.goal, arguments or {}, response)


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
        semantic_plan(Goals.VEHICLE_SEARCH,
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
        semantic_plan(Goals.VEHICLE_SEARCH, {"referenceScope": "currentPage", "sort": "priceAsc"}),
        text="Show the cheapest car on this page",
    )

    assert transition.tool_call is None
    assert "can't identify any vehicle results" in transition.response


def test_named_workshop_booking_goes_straight_to_multi_location_slots() -> None:
    transition = resolve(
        semantic_plan(Goals.WORKSHOP_BOOK_SERVICE,
            {"serviceQuery": "whatever the customer called the service"},
            "Which town would you like?",
        )
    )

    assert transition.response == ""
    assert transition.tool_call.name == "list_workshop_slots"
    assert transition.tool_call.arguments == {
        "serviceTypeName": "whatever the customer called the service"
    }


def test_named_service_question_cannot_render_the_entire_service_catalogue() -> None:
    transition = resolve(
        semantic_plan(Goals.WORKSHOP_BROWSE_SERVICES),
        text="Do you do car cleaning?",
    )

    assert transition.tool_call.name == "get_service_information"
    assert transition.tool_call.arguments == {"q": "Do you do car cleaning?"}


@pytest.mark.parametrize(
    "wording",
    [
        "will you pick up the car?",
        "will you pick up my car?",
        "will you pick up car",
        "Can Northstar collect my car?",
        "Can you collect the vehicle from my home?",
        "Do you offer vehicle collection?",
        "Can you deliver or collect my car?",
        "Will someone come and collect it?",
    ],
)
def test_vehicle_noun_cannot_make_an_operational_question_a_stock_search(
    wording: str,
) -> None:
    transition = resolve(
        semantic_plan(Goals.VEHICLE_SEARCH, {"sort": "priceAsc"}),
        {
            "domain": "part_exchange",
            "goal": "estimate",
            "stage": "collecting_vehicle_details",
            "entities": {},
            "constraints": {},
        },
        text=wording,
    )

    assert transition.tool_call.name == "get_business_information"
    assert transition.tool_call.arguments == {
        "topic": "part_exchange",
        "question": wording,
    }
    assert transition.state["domain"] == "business"
    assert transition.state["goal"] == "part_exchange_information"


@pytest.mark.parametrize(
    ("wording", "arguments"),
    [
        ("show me cars", {"sort": "priceAsc"}),
        ("show me the cheapest available cars", {"sort": "priceAsc"}),
        ("cars below £35,000", {"maxPricePence": 3_500_000}),
        ("I need a petrol automatic SUV", {"fuelType": "Petrol"}),
        ("Find BMWs in Stockport", {"query": "BMW", "town": "Stockport"}),
        ("Find hybrid SUVs below £45,000", {"fuelType": "Hybrid"}),
        ("Actually, make that diesel and under £30,000", {"fuelType": "Diesel"}),
        ("Only show me 2024 or newer cars", {"minYear": 2024}),
        ("browse the latest vehicle stock", {"sort": "newest"}),
        ("list the lowest mileage cars", {"sort": "mileageAsc"}),
    ],
)
def test_legitimate_vehicle_discovery_still_reaches_stock_search(
    wording: str,
    arguments: dict,
) -> None:
    transition = resolve(
        semantic_plan(Goals.VEHICLE_SEARCH, arguments),
        text=wording,
    )

    assert transition.tool_call.name == "search_vehicles"


def test_concrete_filter_cannot_be_hidden_behind_broad_preference_picker() -> None:
    transition = resolve(
        semantic_plan(
            Goals.VEHICLE_CHOOSE_PREFERENCES,
            {"preferenceDimension": "startingPoint", "minYear": 2024},
            response="Choose a starting point.",
        ),
        text="Only show me 2024 or newer cars.",
    )

    assert transition.tool_call.name == "search_vehicles"
    assert transition.tool_call.arguments == {"minYear": 2024}


def test_omitted_year_filter_is_recovered_from_broad_preference_plan() -> None:
    transition = resolve(
        semantic_plan(
            Goals.VEHICLE_CHOOSE_PREFERENCES,
            {"preferenceDimension": "startingPoint"},
        ),
        text="Only show me 2024 or newer cars.",
    )

    assert transition.tool_call.name == "search_vehicles"
    assert transition.tool_call.arguments == {"minYear": 2024}


def test_omitted_year_filter_is_recovered_from_empty_apply_preference_plan() -> None:
    transition = resolve(
        semantic_plan(
            Goals.VEHICLE_APPLY_PREFERENCE,
            {"preferenceDimension": "startingPoint"},
        ),
        text="Only show me 2024 or newer cars.",
    )

    assert transition.tool_call.name == "search_vehicles"
    assert transition.tool_call.arguments == {"minYear": 2024}


def test_every_business_goal_has_a_transition_route() -> None:
    controller = TransitionController()
    transition_only = {
        Goals.VEHICLE_CHOOSE_PREFERENCES,
        Goals.CONVERSATION_RESPOND,
        Goals.CONVERSATION_CLARIFY,
    }

    assert ALL_GOALS - transition_only == set(controller.tool_router.routes)


def test_legacy_workflow_state_is_upgraded_at_the_persistence_boundary() -> None:
    state = normalize_workflow_state(
        {
            "version": 1,
            "domain": "workshop",
            "intent": "workshop_booking_change",
            "stage": "verified",
            "entities": {"serviceQuery": "MOT"},
        }
    )

    assert state == {
        "version": 2,
        "domain": "workshop",
        "goal": "change_booking",
        "stage": "verified",
        "entities": {"serviceQuery": "MOT"},
        "constraints": {},
    }


def test_model_misrouted_location_follow_up_filters_the_active_workshop_service() -> None:
    transition = resolve(
        semantic_plan(Goals.DEALERSHIP_FIND, {"town": "Bolton for it"}),
        {
            "domain": "workshop",
            "intent": "workshop_booking",
            "stage": "choosing_time",
            "entities": {"serviceQuery": "I want to fit tyres"},
            "constraints": {},
        },
        text="is there a location in bolton for it?",
    )

    assert transition.tool_call.name == "list_workshop_slots"
    assert transition.tool_call.arguments == {
        "dealershipTown": "Bolton",
        "serviceTypeName": "I want to fit tyres",
    }
    assert transition.state["entities"] == {"serviceQuery": "I want to fit tyres"}
    assert transition.state["constraints"] == {"town": "Bolton"}


def test_referential_suffix_is_removed_from_standalone_dealership_town() -> None:
    transition = resolve(
        semantic_plan(Goals.DEALERSHIP_FIND, {"town": "Bolton for it"}),
        text="is there a dealership in Bolton for it?",
    )

    assert transition.tool_call.name == "list_dealerships"
    assert transition.tool_call.arguments == {"town": "Bolton"}


def test_workshop_information_uses_selected_live_service_id_not_booking_slots() -> None:
    transition = resolve(
        semantic_plan(Goals.WORKSHOP_CHECK_SERVICE, {"reuseActiveEntity": True}),
        {
            "domain": "workshop",
            "entities": {"serviceTypeId": "live-service-id"},
        },
    )

    assert transition.tool_call.name == "get_service_information"
    assert transition.tool_call.arguments == {"serviceTypeId": "live-service-id"}


def test_switching_workshop_service_replaces_the_active_service() -> None:
    transition = resolve(
        semantic_plan(Goals.WORKSHOP_BOOK_SERVICE, {"serviceQuery": "the newly requested work"}),
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
        semantic_plan(Goals.WORKSHOP_BOOK_SERVICE),
        {
            "domain": "workshop",
            "entities": {"serviceTypeId": "previous-live-service-id"},
        },
    )

    assert transition.tool_call.name == "list_service_types"
    assert transition.tool_call.arguments == {}


def test_explicit_service_wording_overrides_a_previous_service_id_for_information() -> None:
    transition = resolve(
        semantic_plan(Goals.WORKSHOP_CHECK_SERVICE, {"serviceQuery": "the new work"}),
        {
            "domain": "workshop",
            "entities": {"serviceTypeId": "previous-live-service-id"},
        },
    )

    assert transition.tool_call.name == "get_service_information"
    assert transition.tool_call.arguments == {"q": "the new work"}


def test_generic_part_exchange_is_always_the_three_field_estimate_form() -> None:
    transition = resolve(semantic_plan(Goals.PART_EXCHANGE_ESTIMATE))

    assert transition.tool_call.name == "request_part_exchange_estimate_form"
    assert transition.tool_call.arguments == {}


def test_complete_part_exchange_estimate_skips_the_form() -> None:
    fields = {"registration": "AB19 XYZ", "mileage": 45_000, "condition": "good"}
    transition = resolve(semantic_plan(Goals.PART_EXCHANGE_ESTIMATE, fields))

    assert transition.tool_call.name == "estimate_part_exchange"
    assert transition.tool_call.arguments == fields


def test_existing_booking_changes_always_open_private_lookup() -> None:
    transition = resolve(semantic_plan(Goals.WORKSHOP_CHANGE_BOOKING))

    assert transition.tool_call.name == "request_workshop_booking_lookup_form"
    assert transition.tool_call.arguments == {"mode": "amend"}


def test_existing_booking_lookup_modes_are_preserved_by_the_application() -> None:
    lookup = resolve(semantic_plan(Goals.WORKSHOP_FIND_BOOKING))
    cancel = resolve(semantic_plan(Goals.WORKSHOP_CANCEL_BOOKING))

    assert lookup.tool_call.arguments == {"mode": "lookup"}
    assert cancel.tool_call.arguments == {"mode": "cancel"}


def test_more_vehicles_uses_the_exact_next_page_and_existing_filters() -> None:
    transition = resolve(
        semantic_plan(Goals.VEHICLE_CONTINUE_SEARCH),
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
        semantic_plan(Goals.VEHICLE_COMPARE, {"vehicleIds": ["veh-002", "veh-003"]}),
        vehicles=vehicles,
    )

    assert transition.tool_call.name == "compare_vehicles"
    assert transition.tool_call.arguments == {"vehicleIds": ["veh-002", "veh-003"]}


def test_budget_preference_is_a_single_typed_choice_group() -> None:
    transition = resolve(
        semantic_plan(Goals.VEHICLE_CHOOSE_PREFERENCES, {"preferenceDimension": "budgets"})
    )

    assert transition.suggestion_dimension == "budgets"
    assert transition.state["stage"] == "choosing_preference"


def test_selected_live_preference_becomes_a_search_instead_of_reopening_choices() -> None:
    transition = resolve(
        semantic_plan(Goals.VEHICLE_APPLY_PREFERENCE,
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
        semantic_plan(Goals.VEHICLE_APPLY_PREFERENCE,
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
        semantic_plan(Goals.VEHICLE_APPLY_PREFERENCE,
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
        semantic_plan(Goals.VEHICLE_SEARCH,
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
        semantic_plan(Goals.VEHICLE_CHOOSE_PREFERENCES, {"preferenceDimension": "bodyStyles"}),
        old_search,
    )
    assert unmarked_picker.state["constraints"] == {}

    refining_picker = resolve(
        semantic_plan(Goals.VEHICLE_CHOOSE_PREFERENCES,
            {"preferenceDimension": "bodyStyles", "refineCurrentSearch": True},
        ),
        old_search,
    )
    assert refining_picker.state["constraints"] == old_search["constraints"]

    starting_point = resolve(semantic_plan(Goals.VEHICLE_CHOOSE_PREFERENCES), old_search)
    assert starting_point.suggestion_dimension == "startingPoint"
    assert starting_point.state["constraints"] == {}

    body_picker = resolve(
        semantic_plan(Goals.VEHICLE_CHOOSE_PREFERENCES, {"preferenceDimension": "bodyStyles"}),
        starting_point.state,
    )
    estate = resolve(
        semantic_plan(Goals.VEHICLE_APPLY_PREFERENCE,
            {"preferenceDimension": "bodyStyles", "preferenceValue": "Estate"},
        ),
        body_picker.state,
    )

    assert estate.tool_call.name == "search_vehicles"
    assert estate.tool_call.arguments == {"bodyStyle": "Estate"}


def test_standalone_preference_value_cannot_inherit_an_old_vehicle_search() -> None:
    transition = resolve(
        semantic_plan(Goals.VEHICLE_APPLY_PREFERENCE,
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
        semantic_plan(Goals.VEHICLE_SEARCH, {"make": "BMW"}),
        {
            "domain": "vehicle",
            "constraints": {"fuelType": "Hybrid", "maxPricePence": 4_500_000},
        },
    )

    assert transition.tool_call.arguments == {"make": "BMW"}


def test_self_contained_search_cannot_be_scoped_to_page_or_stale_results() -> None:
    transition = resolve(
        semantic_plan(Goals.VEHICLE_SEARCH,
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
        semantic_plan(Goals.VEHICLE_SEARCH,
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
        semantic_plan(Goals.VEHICLE_CHECK_AVAILABILITY, {"query": "BMW 3 Series"}), state
    )
    test_drive = resolve(semantic_plan(Goals.TEST_DRIVE_BOOK, {"query": "Volvo XC40"}), state)

    assert availability.tool_call.name == "search_vehicles"
    assert availability.tool_call.arguments == {"q": "BMW 3 Series"}
    assert test_drive.tool_call.name == "search_vehicles"
    assert test_drive.tool_call.arguments == {"q": "Volvo XC40"}


def test_active_vehicle_is_reused_only_for_an_explicit_reference() -> None:
    state = {"domain": "vehicle", "entities": {"vehicleId": "veh-001"}}

    explicit = resolve(
        semantic_plan(Goals.VEHICLE_CHECK_AVAILABILITY, {"reuseActiveEntity": True}), state
    )
    unrelated = resolve(semantic_plan(Goals.VEHICLE_CHECK_AVAILABILITY), state)

    assert explicit.tool_call.name == "get_vehicle_availability"
    assert explicit.tool_call.arguments == {"id": "veh-001"}
    assert unrelated.tool_call.name == "search_vehicles"
    assert unrelated.tool_call.arguments == {"q": "customer wording"}


def test_show_more_without_results_asks_for_a_search_instead_of_restarting_page_one() -> None:
    transition = resolve(semantic_plan(Goals.VEHICLE_CONTINUE_SEARCH))

    assert transition.tool_call is None
    assert transition.suggestion_dimension == "startingPoint"
    assert "isn't an active" in transition.response


def test_show_more_keeps_only_the_actual_search_filters_for_later_refinement() -> None:
    transition = resolve(
        semantic_plan(Goals.VEHICLE_CONTINUE_SEARCH),
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
    transition = resolve(semantic_plan(Goals.VEHICLE_COMPARE))

    assert transition.tool_call is None
    assert transition.state["stage"] == "clarifying"


def test_sales_enquiry_uses_a_platform_valid_default_type() -> None:
    transition = resolve(semantic_plan(Goals.SALES_ENQUIRE))

    assert transition.tool_call.name == "prepare_sales_enquiry"
    assert transition.tool_call.arguments == {"enquiryType": "general"}


def test_offer_enquiry_is_a_first_class_goal_with_offer_owned_state() -> None:
    offer = {
        "offerId": "offer-07",
        "make": "Jaguar",
        "model": "F-PACE",
        "productType": "PCP",
    }
    transition = resolve(
        semantic_plan(
            Goals.OFFER_ENQUIRE,
            {"offerId": "offer-07", "message": "I am interested in this offer."},
        ),
        offers=[offer],
    )

    assert transition.state["domain"] == "offer"
    assert transition.state["goal"] == "enquire"
    assert transition.state["entities"] == {"offerId": "offer-07"}
    assert transition.tool_call.name == "prepare_sales_enquiry"
    assert transition.tool_call.arguments == {
        "enquiryType": "finance",
        "message": "I am interested in this offer.",
    }


def test_general_business_information_goal_has_an_authoritative_route() -> None:
    transition = resolve(
        semantic_plan(Goals.BUSINESS_GENERAL_INFORMATION),
        text="Which currency does Northstar use?",
    )

    assert transition.tool_call.name == "get_business_information"
    assert transition.tool_call.arguments == {
        "topic": "general",
        "question": "Which currency does Northstar use?",
    }


def test_offer_purchase_cannot_fall_into_reserved_vehicle_interest() -> None:
    offer = {
        "offerId": "offer-07",
        "make": "Jaguar",
        "model": "F-PACE",
        "productType": "PCP",
    }
    transition = resolve(
        semantic_plan(Goals.SALES_REGISTER_INTEREST),
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
    transition = resolve(semantic_plan(Goals.SALES_REGISTER_INTEREST))

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
    transition = resolve(semantic_plan(Goals.CONVERSATION_RESPOND, response="You're welcome."), state)

    assert transition.state["entities"] == state["entities"]
    assert transition.state["constraints"] == state["constraints"]


def test_verified_booking_change_and_cancel_do_not_reopen_private_lookup() -> None:
    state = {
        "domain": "workshop",
        "stage": "verified",
        "entities": {"serviceQuery": "MOT"},
    }

    change = resolve(semantic_plan(Goals.WORKSHOP_CHANGE_BOOKING), state)
    cancel = resolve(semantic_plan(Goals.WORKSHOP_CANCEL_BOOKING), state)

    assert change.tool_call.name == "prepare_workshop_amendment"
    assert cancel.tool_call.name == "prepare_workshop_cancellation"
