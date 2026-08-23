from typing import Any

import pytest

from webchat.integrations.contracts import TurnPlan
from webchat.integrations.fake_llm import FakeLlmProvider
from webchat.orchestration.planning.transitions import TransitionController


async def local_plan(
    text: str, extra_messages: list[dict[str, Any]] | None = None
) -> TurnPlan:
    messages = [{"role": "user", "content": text}, *(extra_messages or [])]
    reply = await FakeLlmProvider().generate_turn(messages)

    assert reply.text == ""
    assert reply.tool_calls == []
    assert reply.plan is not None
    return reply.plan


def resolved_tool(
    plan: TurnPlan,
    text: str,
    *,
    state: dict[str, Any] | None = None,
    displayed_vehicles: list[dict[str, object]] | None = None,
    displayed_offers: list[dict[str, object]] | None = None,
    search_state: dict[str, object] | None = None,
) -> tuple[str, dict[str, Any]]:
    transition = TransitionController().resolve(
        plan,
        state,
        user_text=text,
        displayed_vehicles=displayed_vehicles or [],
        displayed_offers=displayed_offers or [],
        vehicle_search_state=search_state,
        page_vehicles=[],
    )
    assert transition.tool_call is not None
    return transition.tool_call.name, transition.tool_call.arguments


def goal_key(plan: TurnPlan) -> str:
    return f"{plan.domain}.{plan.goal}"


@pytest.mark.parametrize(
    "wording",
    [
        "will you pick up the car?",
        "will you pick up my car?",
        "will you pick up car",
    ],
)
@pytest.mark.asyncio
async def test_fake_provider_cannot_turn_collection_wording_into_vehicle_results(
    wording: str,
) -> None:
    plan = await local_plan(wording)

    assert goal_key(plan) == "vehicle.search"
    assert resolved_tool(
        plan,
        wording,
        state={
            "domain": "part_exchange",
            "goal": "estimate",
            "stage": "collecting_vehicle_details",
            "entities": {},
            "constraints": {},
        },
    ) == (
        "get_business_information",
        {"topic": "part_exchange", "question": wording},
    )


@pytest.mark.parametrize(
    ("text", "expected_goal", "tool_name"),
    [
        ("cars below 35000", "vehicle.search", "search_vehicles"),
        ("show me the cheapest available cars", "vehicle.search", "search_vehicles"),
        ("I need a petrol automatic SUV with low mileage", "vehicle.search", "search_vehicles"),
        ("show me cars in Stockport", "vehicle.search", "search_vehicles"),
        ("Find BMWs in Stockport", "vehicle.search", "search_vehicles"),
        ("Find hybrid SUVs below £45,000", "vehicle.search", "search_vehicles"),
        (
            "Show me cars below £25,000 with fewer than 20,000 miles",
            "vehicle.search",
            "search_vehicles",
        ),
        ("Actually, make that diesel and under £30,000", "vehicle.search", "search_vehicles"),
        ("Only show me 2024 or newer cars", "vehicle.search", "search_vehicles"),
        ("Is the BMW 3 Series available?", "vehicle.check_availability", "search_vehicles"),
        ("Can I test drive a Mazda CX-60?", "test_drive.book", "search_vehicles"),
        ("compare BMW 3 Series and Volvo XC40", "vehicle.compare", "compare_vehicle_models"),
        (
            "Compare the BMW 1 Series and BMW 3 Series",
            "vehicle.compare",
            "compare_vehicle_models",
        ),
        ("show me current offers", "offer.browse", "list_offers"),
        ("What new-car offers are currently published?", "offer.browse", "list_offers"),
        ("where is Manchester dealership", "dealership.find", "list_dealerships"),
        ("Manchester dealership phone number", "dealership.view_contact", "list_dealerships"),
        (
            "What departments does the dealership have?",
            "dealership.view_departments",
            "list_dealership_departments",
        ),
        ("What are your opening hours this Saturday?", "dealership.view_opening_hours", "list_opening_hours"),
        ("Where are your workshop locations?", "workshop.find_locations", "list_workshop_locations"),
        ("What workshop services are available?", "workshop.browse_services", "list_service_types"),
        ("What service types do you support?", "workshop.browse_services", "list_service_types"),
        ("Do you do car cleaning?", "workshop.check_service", "get_service_information"),
        (
            "What is the price of an interim service?",
            "workshop.check_service",
            "get_service_information",
        ),
        ("I want to book a workshop appointment.", "workshop.book_service", "list_service_types"),
        ("I want to fit tyres", "workshop.book_service", "list_workshop_slots"),
        ("I need an MOT in Liverpool", "workshop.book_service", "list_workshop_slots"),
        (
            "Find workshop availability for an annual service next week",
            "workshop.book_service",
            "list_workshop_slots",
        ),
        (
            "find my existing workshop booking",
            "workshop.find_booking",
            "request_workshop_booking_lookup_form",
        ),
        (
            "change my existing workshop booking",
            "workshop.change_booking",
            "request_workshop_booking_lookup_form",
        ),
        (
            "cancel my existing workshop booking",
            "workshop.cancel_booking",
            "request_workshop_booking_lookup_form",
        ),
        (
            "Give me an indicative part-exchange estimate",
            "part_exchange.estimate",
            "request_part_exchange_estimate_form",
        ),
        ("request a callback", "sales.request_callback", "prepare_callback"),
        ("Please have the dealership call me", "sales.request_callback", "prepare_callback"),
        ("send a message to the dealership", "dealership.send_message", "prepare_dealership_message"),
        (
            "Leave a message for the service department",
            "dealership.send_message",
            "prepare_dealership_message",
        ),
        ("I have a sales enquiry about buying a car", "sales.enquire", "prepare_sales_enquiry"),
        ("I want to make a part-exchange enquiry", "part_exchange.request_follow_up", "prepare_part_exchange"),
        ("Can I get finance information for this vehicle?", "business.finance_information", "get_business_information"),
        ("How does finance work?", "business.finance_information", "get_business_information"),
        ("How do you use my personal data?", "business.privacy_information", "get_business_information"),
        (
            "How is the part-exchange estimate calculated?",
            "business.part_exchange_information",
            "get_business_information",
        ),
        ("Are you open on the bank holiday?", "dealership.view_opening_hours", "list_opening_hours"),
        (
            "What are the holiday opening-hour exceptions?",
            "dealership.view_opening_hours",
            "list_opening_hours",
        ),
        (
            "How does your privacy policy protect my data?",
            "business.privacy_information",
            "get_business_information",
        ),
    ],
)
@pytest.mark.asyncio
async def test_fake_provider_uses_canonical_goal_to_tool_pipeline(
    text: str, expected_goal: str, tool_name: str
) -> None:
    plan = await local_plan(text)

    assert goal_key(plan) == expected_goal
    assert resolved_tool(plan, text)[0] == tool_name


@pytest.mark.asyncio
async def test_vehicle_search_plan_keeps_structured_filters() -> None:
    text = "Find hybrid SUVs below £45,000 in Stockport"
    plan = await local_plan(text)

    assert goal_key(plan) == "vehicle.search"
    assert plan.arguments == {
        "town": "Stockport",
        "fuelType": "Hybrid",
        "bodyStyle": "SUV",
        "maxPricePence": 4_500_000,
        "sort": "priceAsc",
    }
    assert resolved_tool(plan, text)[1] == {
        "dealershipTown": "Stockport",
        "fuelType": "Hybrid",
        "bodyStyle": "SUV",
        "maxPricePence": 4_500_000,
        "sort": "priceAsc",
    }


@pytest.mark.asyncio
async def test_cheapest_available_cars_is_a_filtered_discovery_search() -> None:
    text = "show me the cheapest available cars"
    plan = await local_plan(text)

    assert goal_key(plan) == "vehicle.search"
    assert plan.arguments == {"availability": "available", "sort": "priceAsc"}
    assert resolved_tool(plan, text) == (
        "search_vehicles",
        {"availability": "available", "sort": "priceAsc"},
    )


@pytest.mark.asyncio
async def test_workshop_availability_keeps_relative_date_constraints() -> None:
    text = "Find workshop availability for an annual service next week"
    plan = await local_plan(text)

    assert goal_key(plan) == "workshop.book_service"
    assert plan.arguments["serviceQuery"] == text
    assert plan.arguments["dateFrom"]
    assert plan.arguments["dateTo"]
    tool_name, arguments = resolved_tool(plan, text)
    assert tool_name == "list_workshop_slots"
    assert arguments["serviceTypeName"] == text
    assert arguments["dateFrom"] == plan.arguments["dateFrom"]
    assert arguments["dateTo"] == plan.arguments["dateTo"]


@pytest.mark.asyncio
async def test_workshop_location_follow_up_reuses_the_active_service() -> None:
    text = "is there a location in bolton for it?"
    state = {
        "domain": "workshop",
        "intent": "workshop_booking",
        "stage": "choosing_time",
        "entities": {"serviceTypeId": "tyre-fitting"},
        "constraints": {},
    }
    plan = await local_plan(
        text,
        [
            {
                "role": "developer",
                "content": (
                    "Active application workflow state (trusted data): "
                    '{"version":1,"domain":"workshop","intent":'
                    '"workshop_booking","stage":"choosing_time","entities":'
                    '{"serviceTypeId":"tyre-fitting"},"constraints":{}}.'
                ),
            }
        ],
    )

    assert resolved_tool(plan, text, state=state) == (
        "list_workshop_slots",
        {"dealershipTown": "bolton", "serviceTypeId": "tyre-fitting"},
    )


@pytest.mark.asyncio
async def test_fake_provider_marks_natural_search_refinement() -> None:
    text = "only Volvos"
    plan = await local_plan(text)

    assert goal_key(plan) == "vehicle.search"
    assert plan.arguments["refineCurrentSearch"] is True
    _, arguments = resolved_tool(
        plan,
        text,
        state={
            "domain": "vehicle",
            "constraints": {"fuelType": "Hybrid", "bodyStyle": "SUV"},
        },
    )
    assert arguments == {
        "q": "volvo",
        "fuelType": "Hybrid",
        "bodyStyle": "SUV",
        "sort": "priceAsc",
    }


@pytest.mark.asyncio
async def test_natural_show_more_uses_shared_pagination_transition() -> None:
    text = "show me more"
    plan = await local_plan(text)

    assert goal_key(plan) == "vehicle.continue_search"
    assert resolved_tool(
        plan,
        text,
        search_state={"filters": {"make": "BMW"}, "page": 2},
    ) == ("search_vehicles", {"make": "BMW", "page": 3})


@pytest.mark.asyncio
async def test_broad_vehicle_request_uses_application_preference_flow() -> None:
    plan = await local_plan("help me find another car")

    assert goal_key(plan) == "vehicle.choose_preferences"
    assert plan.arguments == {"preferenceDimension": "startingPoint"}


@pytest.mark.asyncio
async def test_natural_single_preference_uses_the_same_apply_goal_as_hosted_planning() -> None:
    text = "I prefer Hybrid cars"
    plan = await local_plan(text)

    assert goal_key(plan) == "vehicle.apply_preference"
    assert plan.arguments["preferenceDimension"] == "fuelTypes"
    assert plan.arguments["preferenceValue"] == "Hybrid"
    assert resolved_tool(plan, text) == (
        "search_vehicles",
        {"fuelType": "Hybrid", "sort": "priceAsc"},
    )


@pytest.mark.asyncio
async def test_positional_displayed_vehicle_reference_is_resolved_to_typed_plan() -> None:
    text = "Open the details for the second vehicle."
    displayed = [
        {"position": 1, "vehicleId": "veh-025", "make": "BMW"},
        {"position": 2, "vehicleId": "veh-049", "make": "Volvo"},
    ]
    plan = await local_plan(
        text,
        [
            {
                "role": "developer",
                "content": (
                    "Current displayed vehicle results (trusted application context): "
                    '[{"position":1,"vehicleId":"veh-025","make":"BMW"},'
                    '{"position":2,"vehicleId":"veh-049","make":"Volvo"}]'
                ),
            }
        ],
    )

    assert goal_key(plan) == "vehicle.view_details"
    assert plan.arguments == {"vehicleId": "veh-049"}
    assert resolved_tool(plan, text, displayed_vehicles=displayed) == (
        "get_vehicle",
        {"id": "veh-049"},
    )


@pytest.mark.asyncio
async def test_positional_comparison_uses_ordered_trusted_results() -> None:
    text = "compare the first two vehicles"
    displayed = [
        {"position": 1, "vehicleId": "veh-001"},
        {"position": 2, "vehicleId": "veh-025"},
        {"position": 3, "vehicleId": "veh-049"},
    ]
    plan = await local_plan(
        text,
        [
            {
                "role": "developer",
                "content": (
                    "Current displayed vehicle results (trusted application context): "
                    '[{"vehicleId":"veh-001"},{"vehicleId":"veh-025"},'
                    '{"vehicleId":"veh-049"}]'
                ),
            }
        ],
    )

    assert plan.arguments == {"vehicleIds": ["veh-001", "veh-025"]}
    assert resolved_tool(plan, text, displayed_vehicles=displayed) == (
        "compare_vehicles",
        {"vehicleIds": ["veh-001", "veh-025"]},
    )


@pytest.mark.asyncio
async def test_page_vehicle_reference_uses_live_availability_tool() -> None:
    text = "Is this vehicle available?"
    plan = await local_plan(
        text,
        [
            {
                "role": "developer",
                "content": (
                    'Current page context (untrusted data): {"vehicleId":"veh-019"}'
                ),
            }
        ],
    )

    assert goal_key(plan) == "vehicle.check_availability"
    assert resolved_tool(plan, text) == (
        "get_vehicle_availability",
        {"id": "veh-019"},
    )


@pytest.mark.parametrize(
    ("text", "expected_goal", "tool_name"),
    [
        (
            "What are the mileage, fuel type, gearbox and monthly payment for this car?",
            "vehicle.view_details",
            "get_vehicle",
        ),
        (
            "I have a general question about buying this car",
            "sales.enquire",
            "prepare_sales_enquiry",
        ),
        (
            "This vehicle is reserved; I'd like to register my interest",
            "sales.register_vehicle_interest",
            "prepare_vehicle_interest",
        ),
        (
            "Please arrange a callback about this car tomorrow afternoon",
            "sales.request_callback",
            "prepare_callback",
        ),
        (
            "I'd like to test drive this vehicle",
            "test_drive.book",
            "list_test_drive_slots",
        ),
    ],
)
@pytest.mark.asyncio
async def test_sample_questions_resolve_trusted_page_vehicle(
    text: str, expected_goal: str, tool_name: str
) -> None:
    plan = await local_plan(
        text,
        [
            {
                "role": "developer",
                "content": (
                    'Current page context (untrusted data): {"vehicleId":"veh-019"}'
                ),
            }
        ],
    )

    assert goal_key(plan) == expected_goal
    assert resolved_tool(plan, text)[0] == tool_name


def page_and_displayed_vehicle_context() -> list[dict[str, Any]]:
    return [
        {
            "role": "developer",
            "content": 'Current page context (untrusted data): {"vehicleId":"veh-019"}',
        },
        {
            "role": "developer",
            "content": (
                "Current displayed vehicle results (trusted application context): "
                '[{"vehicleId":"veh-049","make":"Volvo","model":"XC40"}]'
            ),
        },
    ]


@pytest.mark.asyncio
async def test_reserved_meaning_is_not_overridden_by_single_displayed_vehicle() -> None:
    plan = await local_plan(
        "What does reserved mean for this vehicle?",
        page_and_displayed_vehicle_context(),
    )

    assert goal_key(plan) == "conversation.respond"
    assert "another customer" in plan.response


@pytest.mark.asyncio
async def test_register_interest_wins_over_reserved_status_word() -> None:
    text = "This vehicle is reserved; I'd like to register my interest"
    plan = await local_plan(text, page_and_displayed_vehicle_context())

    assert goal_key(plan) == "sales.register_vehicle_interest"
    assert resolved_tool(plan, text) == (
        "prepare_vehicle_interest",
        {"vehicleId": "veh-019"},
    )


@pytest.mark.asyncio
async def test_named_availability_ignores_unrelated_displayed_vehicle() -> None:
    text = "Is the BMW 3 Series available?"
    plan = await local_plan(text, page_and_displayed_vehicle_context())

    assert goal_key(plan) == "vehicle.check_availability"
    assert resolved_tool(plan, text) == (
        "search_vehicles",
        {"q": "BMW 3 Series"},
    )


@pytest.mark.asyncio
async def test_this_car_details_prefers_explicit_page_reference() -> None:
    text = "What are the mileage, fuel type and gearbox for this car?"
    plan = await local_plan(text, page_and_displayed_vehicle_context())

    assert goal_key(plan) == "vehicle.view_details"
    assert resolved_tool(plan, text) == ("get_vehicle", {"id": "veh-019"})


@pytest.mark.asyncio
async def test_this_car_reference_prefers_the_active_workflow_vehicle_over_page_context() -> None:
    text = "Is this vehicle available?"
    plan = await local_plan(
        text,
        [
            {
                "role": "developer",
                "content": (
                    "Current page context (untrusted data): "
                    '{"vehicleId":"veh-019"}'
                ),
            },
            {
                "role": "developer",
                "content": (
                    "Active application workflow state (trusted data): "
                    '{"version":1,"domain":"vehicle","entities":'
                    '{"vehicleId":"veh-049"}}. Use it to resolve follow-ups.'
                ),
            },
        ],
    )

    assert goal_key(plan) == "vehicle.check_availability"
    assert resolved_tool(plan, text) == (
        "get_vehicle_availability",
        {"id": "veh-049"},
    )


@pytest.mark.asyncio
async def test_offer_reference_uses_canonical_offer_details_route() -> None:
    text = "show me more details about this offer"
    plan = await local_plan(
        text,
        [
            {
                "role": "developer",
                "content": (
                    "Current displayed offer results (trusted application context): "
                    '[{"offerId":"offer-7","title":"XC40 PCP"}]'
                ),
            }
        ],
    )

    assert goal_key(plan) == "offer.view_details"
    assert resolved_tool(
        plan,
        text,
        displayed_offers=[{"position": 1, "offerId": "offer-7"}],
    ) == ("get_offer", {"id": "offer-7"})


@pytest.mark.asyncio
async def test_offer_enquiry_uses_the_offer_goal_instead_of_browsing_again() -> None:
    text = "I am interested in this offer"
    displayed_offers = [
        {
            "position": 1,
            "offerId": "offer-7",
            "make": "Volvo",
            "model": "XC40",
            "productType": "PCH",
        }
    ]
    plan = await local_plan(
        text,
        [
            {
                "role": "developer",
                "content": (
                    "Current displayed offer results (trusted application context): "
                    '[{"offerId":"offer-7","make":"Volvo","model":"XC40",'
                    '"productType":"PCH"}]'
                ),
            }
        ],
    )

    assert goal_key(plan) == "offer.enquire"
    assert plan.arguments == {"offerId": "offer-7", "message": text}
    assert resolved_tool(
        plan,
        text,
        displayed_offers=displayed_offers,
    ) == (
        "prepare_sales_enquiry",
        {"enquiryType": "finance", "message": text},
    )


@pytest.mark.asyncio
async def test_greeting_is_a_validated_conversation_response_plan() -> None:
    plan = await local_plan("hi")

    assert goal_key(plan) == "conversation.respond"
    assert "cars below" in plan.response


@pytest.mark.asyncio
async def test_tool_fact_follow_up_still_uses_a_typed_plan() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [
            {"role": "user", "content": "cars below 35000"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "search", "name": "search_vehicles", "arguments": {}}
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "search",
                "content": '{"items":[{},{}]}',
            },
        ]
    )

    assert reply.plan is not None
    assert goal_key(reply.plan) == "conversation.respond"
    assert "2 matching vehicles" in reply.plan.response
