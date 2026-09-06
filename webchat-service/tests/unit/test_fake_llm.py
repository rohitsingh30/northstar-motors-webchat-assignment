import json
from typing import Any

import pytest

from webchat.integrations.contracts import ProviderReply
from webchat.integrations.fake_llm import FakeLlmProvider


async def local_reply(
    text: str, extra_messages: list[dict[str, Any]] | None = None
) -> ProviderReply:
    return await FakeLlmProvider().generate_turn(
        [{"role": "user", "content": text}, *(extra_messages or [])]
    )


@pytest.mark.parametrize(
    ("text", "tool_name"),
    [
        ("cars below 35000", "search_vehicles"),
        ("show me the cheapest available cars", "search_vehicles"),
        ("show me all available cars", "reset_vehicle_search"),
        ("Find hybrid SUVs below £45,000", "search_vehicles"),
        ("compare BMW 3 Series and Volvo XC40", "compare_vehicle_models"),
        ("show me current offers", "list_offers"),
        ("where is Manchester dealership", "list_dealerships"),
        ("Manchester dealership phone number", "list_dealerships"),
        ("What departments does the dealership have?", "list_dealership_departments"),
        ("What are your opening hours this Saturday?", "list_opening_hours"),
        ("Are you open on the bank holiday?", "list_holiday_opening_hours"),
        ("Where are your workshop locations?", "list_workshop_locations"),
        ("What workshop services are available?", "list_service_types"),
        ("What service types do you support?", "list_service_types"),
        ("Do you do car cleaning?", "get_service_information"),
        ("What is the price of an interim service?", "get_service_information"),
        ("what is tyre cost", "get_service_information"),
        ("I want to book a workshop appointment", "list_workshop_slots"),
        ("I want to fit tyres", "list_workshop_slots"),
        ("I need an MOT in Liverpool", "list_workshop_slots"),
        ("find my existing workshop booking", "request_workshop_booking_lookup_form"),
        ("change my existing workshop booking", "request_workshop_booking_lookup_form"),
        ("cancel my existing workshop booking", "request_workshop_booking_lookup_form"),
        ("Give me an indicative part-exchange estimate", "request_part_exchange_estimate_form"),
        ("request a callback", "prepare_callback"),
        ("send a message to the dealership", "prepare_dealership_message"),
        ("I have a sales enquiry about buying a car", "prepare_sales_enquiry"),
        ("I want to make a part-exchange enquiry", "prepare_part_exchange"),
        ("How does finance work?", "get_business_information"),
        ("How do you use my personal data?", "get_business_information"),
        ("How is the part-exchange estimate calculated?", "get_business_information"),
    ],
)
@pytest.mark.asyncio
async def test_fake_provider_emits_direct_catalogue_tools(text: str, tool_name: str) -> None:
    reply = await local_reply(text)

    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0].name == tool_name


@pytest.mark.asyncio
async def test_vehicle_search_keeps_structured_filters() -> None:
    reply = await local_reply("Find hybrid SUVs below £45,000 in Stockport")

    assert reply.tool_calls[0].name == "search_vehicles"
    assert reply.tool_calls[0].arguments == {
        "dealershipTown": "Stockport",
        "fuelType": "Hybrid",
        "bodyStyle": "SUV",
        "maxPricePence": 4_500_000,
        "sort": "priceAsc",
    }


@pytest.mark.asyncio
async def test_generic_dealership_message_request_does_not_invent_form_content() -> None:
    reply = await local_reply("send a message to the dealership")

    assert reply.tool_calls[0].name == "prepare_dealership_message"
    assert reply.tool_calls[0].arguments == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("customer_text", "tool_name"),
    [
        ("request a callback", "prepare_callback"),
        ("I have a sales enquiry about buying a car", "prepare_sales_enquiry"),
    ],
)
async def test_generic_workflow_request_does_not_become_descriptive_content(
    customer_text: str, tool_name: str
) -> None:
    reply = await local_reply(customer_text)

    assert reply.tool_calls[0].name == tool_name
    assert reply.tool_calls[0].arguments == {}


@pytest.mark.asyncio
async def test_callback_carries_the_latest_relevant_topic_across_contact_chooser_turns() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [
            {"role": "user", "content": "Will you pick up my car?"},
            {
                "role": "assistant",
                "content": (
                    "I don't have confirmed Northstar information about vehicle collection or "
                    "home pickup. Would you like me to help you contact a dealership?"
                ),
            },
            {"role": "user", "content": "yes"},
            {
                "role": "assistant",
                "content": "Choose how you'd like to contact a Northstar dealership.",
            },
            {"role": "user", "content": "please have a dealership call me"},
        ]
    )

    assert reply.tool_calls[0].name == "prepare_callback"
    assert reply.tool_calls[0].arguments == {
        "department": "sales",
        "reason": "Vehicle collection or home pickup",
    }


@pytest.mark.parametrize(
    "wording",
    [
        "will you pick up the car?",
        "will you pick up my car?",
        "Can Northstar collect my car?",
    ],
)
@pytest.mark.asyncio
async def test_collection_question_never_becomes_vehicle_search(wording: str) -> None:
    reply = await local_reply(wording)

    assert [call.name for call in reply.tool_calls] == ["get_business_information"]
    assert reply.tool_calls[0].arguments == {"topic": "general", "question": wording}


@pytest.mark.parametrize(
    ("question", "meaning", "citation"),
    [
        ("what is pch", "Personal Contract Hire", "customer.pch"),
        ("what does pcp mean", "Personal Contract Purchase", "customer.pcp"),
    ],
)
@pytest.mark.asyncio
async def test_finance_terms_are_grounded_answers_not_offer_searches(
    question: str, meaning: str, citation: str
) -> None:
    reply = await local_reply(question)

    assert reply.tool_calls == []
    assert meaning in reply.text
    assert reply.citation_ids == (citation,)


@pytest.mark.asyncio
async def test_workshop_location_follow_up_reuses_executed_service_state() -> None:
    state = {
        "version": 3,
        "activeWorkflow": "workshop_booking",
        "stage": "choosing_time",
        "entities": {"serviceTypeId": "tyre-fitting"},
        "constraints": {},
    }
    reply = await local_reply(
        "is there a location in Bolton for it?",
        [
            {
                "role": "developer",
                "content": "Active application workflow state (trusted data): "
                + __import__("json").dumps(state, separators=(",", ":")),
            }
        ],
    )

    assert reply.tool_calls[0].name == "list_workshop_slots"
    assert reply.tool_calls[0].arguments == {
        "serviceTypeId": "tyre-fitting",
        "dealershipTown": "Bolton",
    }


@pytest.mark.asyncio
async def test_workshop_service_choice_is_resolved_by_the_live_catalogue() -> None:
    state = {
        "version": 3,
        "activeWorkflow": "workshop_booking",
        "stage": "choosing_service",
        "entities": {},
        "constraints": {},
        "lastTool": "list_workshop_slots",
        "lastRenderer": "service_list",
    }
    reply = await local_reply(
        "brakes",
        [
            {
                "role": "developer",
                "content": "Active application workflow state (trusted data): "
                + __import__("json").dumps(state, separators=(",", ":")),
            }
        ],
    )

    assert reply.tool_calls[0].name == "refine_workshop_slots"
    assert reply.tool_calls[0].arguments == {"serviceTypeName": "brakes"}


def displayed_dealership_context(*dealerships: tuple[str, str]) -> dict[str, str]:
    items = [
        {
            "position": position,
            "dealershipId": dealership_id,
            "town": town,
        }
        for position, (dealership_id, town) in enumerate(dealerships, start=1)
    ]
    return {
        "role": "developer",
        "content": "Current displayed dealership results (trusted application context): "
        + json.dumps(items, separators=(",", ":")),
    }


def displayed_vehicle_context(*vehicles: tuple[str, str]) -> dict[str, str]:
    items = [
        {
            "position": position,
            "vehicleId": vehicle_id,
            "label": label,
        }
        for position, (vehicle_id, label) in enumerate(vehicles, start=1)
    ]
    return {
        "role": "developer",
        "content": "Current displayed vehicle results (trusted application context): "
        + json.dumps(items, separators=(",", ":")),
    }


@pytest.mark.asyncio
async def test_open_displayed_vehicle_ordinal_proposes_protected_navigation() -> None:
    reply = await local_reply(
        "Open the second one",
        [
            displayed_vehicle_context(
                ("veh-041", "2025 MINI Cooper"),
                ("veh-005", "2024 MINI Cooper"),
                ("veh-053", "2023 MINI Cooper"),
            )
        ],
    )

    assert reply.tool_calls == []
    assert reply.interaction_proposal is not None
    assert reply.interaction_proposal.kind == "open_vehicle_detail"
    assert reply.interaction_proposal.entityReference == "vehicle:veh-005"


@pytest.mark.asyncio
async def test_show_the_recommended_vehicle_returns_its_read_only_card() -> None:
    reply = await local_reply(
        "Show me the vehicle",
        [
            displayed_vehicle_context(
                ("veh-041", "2025 BMW 1 Series"),
                ("veh-005", "2025 BMW X3"),
                ("veh-053", "2025 MINI Countryman"),
            ),
            {
                "role": "developer",
                "content": (
                    "Current conversationally focused vehicle from the immediately preceding "
                    "grounded answer (trusted displayed candidate): "
                    '{"vehicleId":"veh-041","make":"BMW","model":"1 Series"}'
                ),
            },
        ],
    )

    assert reply.interaction_proposal is None
    assert reply.tool_calls[0].name == "get_vehicle"
    assert reply.tool_calls[0].arguments == {"id": "veh-041"}


@pytest.mark.asyncio
async def test_show_the_vehicle_without_singular_focus_clarifies() -> None:
    reply = await local_reply(
        "Show me the vehicle",
        [
            displayed_vehicle_context(
                ("veh-041", "2025 BMW 1 Series"),
                ("veh-005", "2025 BMW X3"),
            )
        ],
    )

    assert reply.interaction_proposal is None
    assert reply.response_mode == "clarify"
    assert reply.interaction is not None
    assert reply.interaction.blocking_tool == "get_vehicle"


@pytest.mark.asyncio
async def test_open_dealership_question_remains_opening_hours_with_vehicle_context() -> None:
    reply = await local_reply(
        "Is the Manchester dealership open Saturday?",
        [displayed_vehicle_context(("veh-041", "2025 MINI Cooper in Manchester"))],
    )

    assert reply.interaction_proposal is None
    assert reply.tool_calls[0].name == "list_opening_hours"


@pytest.mark.asyncio
async def test_singular_department_follow_up_reuses_only_displayed_dealership() -> None:
    reply = await local_reply(
        "What departments does the dealership have?",
        [displayed_dealership_context(("dealer-stockport", "Stockport"))],
    )

    assert reply.tool_calls[0].name == "find_dealership_departments"
    assert reply.tool_calls[0].arguments == {"dealershipId": "dealer-stockport"}


@pytest.mark.asyncio
async def test_plural_department_request_does_not_collapse_to_one_location() -> None:
    reply = await local_reply(
        "What departments do the dealerships have?",
        [displayed_dealership_context(("dealer-stockport", "Stockport"))],
    )

    assert reply.tool_calls[0].name == "list_dealership_departments"


@pytest.mark.asyncio
async def test_generic_hours_follow_up_preserves_multi_dealership_scope() -> None:
    reply = await local_reply(
        "Show me the opening hours",
        [
            displayed_dealership_context(
                ("dealer-bolton", "Bolton"),
                ("dealer-stockport", "Stockport"),
            )
        ],
    )

    assert reply.tool_calls[0].name == "list_opening_hours"
    assert reply.tool_calls[0].arguments == {}


@pytest.mark.asyncio
async def test_singular_hours_follow_up_over_multiple_locations_clarifies() -> None:
    reply = await local_reply(
        "Is the dealership open tomorrow?",
        [
            displayed_dealership_context(
                ("dealer-bolton", "Bolton"),
                ("dealer-stockport", "Stockport"),
            )
        ],
    )

    assert reply.tool_calls == []
    assert reply.response_mode == "clarify"
    assert reply.text == "Which dealership do you mean?"


@pytest.mark.asyncio
async def test_trusted_vehicle_follow_up_uses_live_availability_tool() -> None:
    reply = await local_reply(
        "is this car available?",
        [
            {
                "role": "developer",
                "content": 'Active application workflow state (trusted data): {"version":3,'
                '"activeWorkflow":"vehicle","entities":{"vehicleId":"veh-019"},'
                '"constraints":{},"stage":"viewing"}',
            }
        ],
    )

    assert reply.tool_calls[0].name == "get_vehicle_availability"
    assert reply.tool_calls[0].arguments == {"id": "veh-019"}


@pytest.mark.asyncio
async def test_greeting_is_a_direct_conversation_response() -> None:
    reply = await local_reply("hello")

    assert reply.tool_calls == []
    assert reply.response_mode == "conversation"
    assert "cars below" in reply.text
