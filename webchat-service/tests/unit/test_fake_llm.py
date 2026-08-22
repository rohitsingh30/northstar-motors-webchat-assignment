import pytest

from webchat.integrations.llm import FakeLlmProvider


@pytest.mark.asyncio
async def test_local_provider_searches_for_cars_below_a_price():
    reply = await FakeLlmProvider().generate_turn(
        [{"role": "user", "content": "cars below 35000"}]
    )

    assert reply.text == ""
    assert reply.tool_calls[0].name == "search_vehicles"
    assert reply.tool_calls[0].arguments == {"sort": "priceAsc", "maxPricePence": 3_500_000}


@pytest.mark.asyncio
async def test_local_provider_uses_a_concise_clarification_for_a_broad_car_request():
    reply = await FakeLlmProvider().generate_turn(
        [{"role": "user", "content": "help me find another car"}]
    )

    assert reply.tool_calls == []
    assert reply.text == "Tell me what matters most, or choose a starting point below."


@pytest.mark.asyncio
async def test_local_provider_keeps_vehicle_search_scoped_to_requested_town():
    reply = await FakeLlmProvider().generate_turn(
        [{"role": "user", "content": "show me cars in Stockport"}]
    )

    assert reply.tool_calls[0].name == "search_vehicles"
    assert reply.tool_calls[0].arguments["dealershipTown"].lower() == "stockport"


@pytest.mark.asyncio
async def test_local_provider_does_not_reuse_old_vehicle_intent_for_workshop_locations():
    reply = await FakeLlmProvider().generate_turn(
        [
            {"role": "user", "content": "show me BMWs in Stockport"},
            {"role": "assistant", "content": "Here are the vehicles."},
            {"role": "user", "content": "Where are your workshop locations?"},
        ]
    )

    assert reply.tool_calls[0].name == "list_workshop_locations"


@pytest.mark.asyncio
async def test_local_provider_routes_department_question_to_live_department_data():
    reply = await FakeLlmProvider().generate_turn(
        [{"role": "user", "content": "What departments does the dealership have?"}]
    )

    assert reply.tool_calls[0].name == "list_dealership_departments"


@pytest.mark.asyncio
async def test_local_provider_routes_weekday_hours_to_opening_hours_cards():
    reply = await FakeLlmProvider().generate_turn(
        [{"role": "user", "content": "What are your opening hours this Saturday?"}]
    )

    assert reply.tool_calls[0].name == "list_opening_hours"
    assert reply.tool_calls[0].arguments == {"day": "Saturday"}


@pytest.mark.asyncio
async def test_open_vehicle_details_is_not_misrouted_as_opening_hours() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [{"role": "user", "content": "Open the details for the second vehicle."}]
    )

    assert not reply.tool_calls or reply.tool_calls[0].name != "list_opening_hours"


@pytest.mark.asyncio
async def test_displayed_vehicle_reference_is_left_for_semantic_resolution() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [
            {
                "role": "developer",
                "content": 'Current page context (untrusted data): {"vehicleId":"veh-019"}',
            },
            {"role": "user", "content": "Open the details for the second vehicle."},
            {
                "role": "developer",
                "content": (
                    "Current displayed vehicle results (trusted application context): "
                    '[{"vehicleId":"veh-025"},{"vehicleId":"veh-049"}]'
                ),
            },
        ]
    )

    assert reply.tool_calls == []


@pytest.mark.asyncio
async def test_page_vehicle_does_not_override_displayed_positional_availability() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [
            {
                "role": "developer",
                "content": 'Current page context (untrusted data): {"vehicleId":"veh-019"}',
            },
            {"role": "user", "content": "Is the second vehicle available?"},
            {
                "role": "developer",
                "content": (
                    "Current displayed vehicle results (trusted application context): "
                    '[{"vehicleId":"veh-025"},{"vehicleId":"veh-049"}]'
                ),
            },
        ]
    )

    assert reply.tool_calls == []


@pytest.mark.asyncio
async def test_this_vehicle_still_uses_page_context_when_results_are_present() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [
            {
                "role": "developer",
                "content": 'Current page context (untrusted data): {"vehicleId":"veh-019"}',
            },
            {"role": "user", "content": "Is this vehicle available?"},
            {
                "role": "developer",
                "content": (
                    "Current displayed vehicle results (trusted application context): "
                    '[{"vehicleId":"veh-025"},{"vehicleId":"veh-049"}]'
                ),
            },
        ]
    )

    assert reply.tool_calls[0].name == "get_vehicle_availability"
    assert reply.tool_calls[0].arguments == {"id": "veh-019"}


@pytest.mark.asyncio
async def test_reserved_meaning_question_is_not_treated_as_a_live_status_check() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [
            {
                "role": "developer",
                "content": 'Current page context (untrusted data): {"vehicleId":"veh-019"}',
            },
            {"role": "user", "content": "What does reserved mean for this vehicle?"},
        ]
    )

    assert reply.tool_calls == []
    assert "another customer" in reply.text


@pytest.mark.asyncio
async def test_service_choice_chip_routes_the_exact_live_service_name() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [{"role": "user", "content": "Book service: Brake inspection"}]
    )

    assert reply.tool_calls[0].name == "list_workshop_slots"
    assert reply.tool_calls[0].arguments["serviceTypeName"] == "Brake inspection"


@pytest.mark.asyncio
async def test_generic_workshop_booking_is_left_for_semantic_resolution():
    reply = await FakeLlmProvider().generate_turn(
        [{"role": "user", "content": "I want to book a workshop appointment."}]
    )

    assert reply.tool_calls == []


@pytest.mark.asyncio
async def test_local_provider_routes_latest_availability_question_to_current_vehicle_search():
    reply = await FakeLlmProvider().generate_turn(
        [
            {"role": "user", "content": "show me BMWs in Stockport"},
            {"role": "assistant", "content": "Here are the vehicles."},
            {"role": "user", "content": "Is the BMW 3 Series available?"},
        ]
    )

    assert reply.tool_calls[0].name == "search_vehicles"
    assert reply.tool_calls[0].arguments["q"] == "bmw 3 series"


@pytest.mark.asyncio
async def test_named_vehicle_overrides_a_different_page_vehicle() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [
            {
                "role": "developer",
                "content": 'Current page context (untrusted data): {"vehicleId":"veh-019"}',
            },
            {"role": "user", "content": "Is the BMW 3 Series available?"},
        ]
    )

    assert reply.tool_calls[0].name == "search_vehicles"
    assert reply.tool_calls[0].arguments["q"] == "bmw 3 series"


@pytest.mark.asyncio
async def test_named_test_drive_overrides_a_different_page_vehicle() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [
            {
                "role": "developer",
                "content": 'Current page context (untrusted data): {"vehicleId":"veh-019"}',
            },
            {"role": "user", "content": "Can I test drive a BMW 3 Series?"},
        ]
    )

    assert reply.tool_calls[0].name == "get_vehicle_facets"


@pytest.mark.asyncio
async def test_local_provider_returns_a_friendly_summary_after_tool_result():
    reply = await FakeLlmProvider().generate_turn(
        [
            {"role": "user", "content": "cars below 35000"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "local-search-vehicles", "name": "search_vehicles", "arguments": {}}
                ],
            },
            {"role": "tool", "tool_call_id": "local-search-vehicles", "content": '{"items":[{},{}]}'},
        ]
    )

    assert reply.tool_calls == []
    assert "2 matching vehicles" in reply.text


@pytest.mark.asyncio
async def test_local_provider_does_not_guess_a_relative_vehicle_reference():
    reply = await FakeLlmProvider().generate_turn(
        [
            {"role": "user", "content": "Show cars under 35000"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "local-search", "name": "search_vehicles", "arguments": {}}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "local-search",
                "content": '{"items":[{"id":"veh-001"},{"id":"veh-025"},{"id":"veh-049"}]}' ,
            },
            {"role": "user", "content": "compare the first two vehicles"},
        ]
    )

    assert reply.tool_calls == []
    assert "name the vehicles" in reply.text.lower()


@pytest.mark.asyncio
async def test_compare_without_a_shortlist_does_not_fall_back_to_vehicle_search():
    reply = await FakeLlmProvider().generate_turn(
        [{"role": "user", "content": "compare the first two vehicles"}]
    )

    assert reply.tool_calls == []
    assert "name the vehicles" in reply.text.lower()


@pytest.mark.asyncio
async def test_local_provider_greeting_is_useful():
    reply = await FakeLlmProvider().generate_turn([{"role": "user", "content": "hi"}])

    assert reply.tool_calls == []
    assert "cars below" in reply.text


@pytest.mark.asyncio
async def test_part_exchange_estimate_needs_only_valuation_inputs() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [{"role": "user", "content": "Give me an indicative part-exchange estimate"}]
    )

    assert reply.tool_calls[0].name == "request_part_exchange_estimate_form"
    assert reply.tool_calls[0].arguments == {}


@pytest.mark.asyncio
async def test_part_exchange_estimate_reuses_valuation_inputs_from_the_conversation() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [
            {
                "role": "user",
                "content": "Estimate my part exchange: AB19 XYZ, 45,000 miles, in good condition.",
            },
            {"role": "assistant", "content": "Your estimate is below."},
            {"role": "user", "content": "Give me an indicative part-exchange estimate"},
        ]
    )

    assert reply.tool_calls[0].name == "estimate_part_exchange"
    assert reply.tool_calls[0].arguments == {
        "registration": "AB19 XYZ",
        "mileage": 45_000,
        "condition": "good",
    }


@pytest.mark.asyncio
async def test_local_provider_combines_vehicle_filters() -> None:
    messages = [{"role": "user", "content": "Find hybrid SUVs below £45,000"}]
    first = await FakeLlmProvider().generate_turn(messages)
    assert first.tool_calls[0].name == "get_vehicle_facets"
    messages.extend(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "local-vehicle-facets", "name": "get_vehicle_facets", "arguments": {}}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "local-vehicle-facets",
                "content": '{"fuelTypes":["Hybrid"],"bodyStyles":["SUV"]}',
            },
        ]
    )
    reply = await FakeLlmProvider().generate_turn(messages)

    assert reply.tool_calls[0].arguments == {
        "fuelType": "Hybrid",
        "bodyStyle": "SUV",
        "maxPricePence": 4_500_000,
        "sort": "priceAsc",
    }


@pytest.mark.asyncio
async def test_local_provider_refines_previous_search() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [
            {"role": "user", "content": "Find hybrid SUVs below £45,000"},
            {"role": "assistant", "content": "Found six."},
            {"role": "user", "content": "only Volvos"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "local-vehicle-facets", "name": "get_vehicle_facets", "arguments": {}}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "local-vehicle-facets",
                "content": (
                    '{"makes":["Volvo"],"fuelTypes":["Hybrid"],'
                    '"bodyStyles":["SUV"]}'
                ),
            },
        ]
    )

    assert reply.tool_calls[0].arguments["make"] == "Volvo"
    assert reply.tool_calls[0].arguments["fuelType"] == "Hybrid"
    assert reply.tool_calls[0].arguments["bodyStyle"] == "SUV"


@pytest.mark.asyncio
async def test_local_provider_uses_page_vehicle_for_this() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [
            {
                "role": "developer",
                "content": 'Current page context (untrusted data): {"vehicleId":"veh-019"}',
            },
            {"role": "user", "content": "Is this available?"},
        ]
    )

    assert reply.tool_calls[0].name == "get_vehicle_availability"
    assert reply.tool_calls[0].arguments == {"id": "veh-019"}


@pytest.mark.asyncio
async def test_test_drive_request_resolves_named_vehicle_before_slots() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [
            {"role": "user", "content": "Can I book a test drive of a BMW 3 Series?"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "local-test-drive-facets", "name": "get_vehicle_facets", "arguments": {}}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "local-test-drive-facets",
                "content": '{"makes":["BMW"],"models":["3 Series"]}',
            },
        ]
    )

    assert reply.tool_calls[0].name == "search_vehicles"
    assert reply.tool_calls[0].arguments["make"] == "BMW"
    assert reply.tool_calls[0].arguments["model"] == "3 Series"


@pytest.mark.asyncio
async def test_local_provider_does_not_need_catalogue_entry_for_new_model() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [
            {"role": "user", "content": "Can I test drive a Mazda CX-60?"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "local-test-drive-facets", "name": "get_vehicle_facets", "arguments": {}}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "local-test-drive-facets",
                "content": '{"makes":[],"models":[]}',
            },
        ]
    )

    assert reply.tool_calls[0].name == "search_vehicles"
    assert reply.tool_calls[0].arguments["q"] == "mazda cx-60"


@pytest.mark.asyncio
async def test_local_provider_resolves_dealership_ids_from_tool_data() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [
            {"role": "user", "content": "Manchester dealership holiday opening hours"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "local-hours-dealer", "name": "list_dealerships", "arguments": {}}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "local-hours-dealer",
                "content": (
                    '{"items":[{"id":"dealer-from-api","name":"City showroom",'
                    '"town":"Manchester"}]}'
                ),
            },
        ]
    )

    assert reply.tool_calls[0].name == "get_opening_hours"
    assert reply.tool_calls[0].arguments == {"id": "dealer-from-api"}


@pytest.mark.asyncio
async def test_test_drive_slots_are_scoped_to_selected_vehicle() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [
            {"role": "user", "content": "Find test-drive times for BMW 3 Series"},
            {
                "role": "developer",
                "content": (
                    'Trusted widget action: '
                    '{"type":"select_test_drive_vehicle","vehicleId":"veh-005"}'
                ),
            },
        ]
    )

    assert reply.tool_calls[0].name == "list_test_drive_slots"
    assert reply.tool_calls[0].arguments == {"vehicleId": "veh-005"}


@pytest.mark.asyncio
async def test_selected_slot_uses_typed_action_without_exposing_id_in_user_text() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [
            {"role": "user", "content": "Select Saturday 29 August at 5:00 pm"},
            {
                "role": "developer",
                "content": (
                    'Trusted widget action: '
                    '{"type":"select_test_drive_slot","slotId":"td-slot-0120"}'
                ),
            },
        ]
    )

    assert reply.tool_calls[0].name == "prepare_test_drive"
    assert reply.tool_calls[0].arguments == {"slotId": "td-slot-0120"}
