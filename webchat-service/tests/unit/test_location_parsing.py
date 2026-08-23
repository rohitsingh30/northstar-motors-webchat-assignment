import json
from types import SimpleNamespace

import pytest

from webchat.integrations.fake_llm import FakeLlmProvider
from webchat.integrations.fake_llm.routing.parsers import location_query
from webchat.orchestration.context import current_dealership_reference_context


@pytest.mark.parametrize(
    ("text", "town"),
    [
        ("Where is the Stockport dealership?", "Stockport"),
        ("What is the phone number and email for Manchester sales?", "Manchester"),
        ("Manchester dealership holiday opening hours", "Manchester"),
        ("I need an MOT in Liverpool", "Liverpool"),
    ],
)
def test_town_is_extracted_from_common_dealership_phrasing(text: str, town: str) -> None:
    assert location_query(text) == town


@pytest.mark.parametrize(
    "text",
    [
        "show me dealership contact details",
        "is the dealership open tomorrow?",
        "does the dealership have a service department?",
    ],
)
def test_generic_dealership_request_is_not_mistaken_for_a_town(text: str) -> None:
    assert location_query(text) is None


@pytest.mark.asyncio
async def test_named_dealership_contact_question_is_filtered_to_that_town() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [{"role": "user", "content": "What is the phone number and email for Manchester sales?"}]
    )

    assert reply.tool_calls[0].name == "list_dealerships"
    assert reply.tool_calls[0].arguments == {"town": "Manchester"}


@pytest.mark.asyncio
async def test_named_dealership_hours_are_filtered_to_that_town() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [{"role": "user", "content": "Manchester dealership holiday opening hours"}]
    )

    assert reply.tool_calls[0].name == "list_holiday_opening_hours"
    assert reply.tool_calls[0].arguments == {"town": "Manchester"}


@pytest.mark.asyncio
async def test_named_department_hours_keep_both_town_and_department() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [{"role": "user", "content": "Stockport parts opening hours"}]
    )

    assert reply.tool_calls[0].name == "list_opening_hours"
    assert reply.tool_calls[0].arguments == {
        "town": "Stockport",
        "department": "parts",
    }


def test_displayed_dealership_context_preserves_exact_ids_and_towns() -> None:
    messages = [
        SimpleNamespace(
            role="assistant",
            view_type="dealership_list",
            view_payload_json=json.dumps(
                {
                    "items": [
                        {
                            "id": "dealer-bolton",
                            "name": "Northstar Bolton",
                            "town": "Bolton",
                            "postcode": "BL3 2AW",
                        },
                        {
                            "id": "dealer-manchester",
                            "name": "Northstar Manchester",
                            "town": "Manchester",
                            "postcode": "M20 2YY",
                        },
                    ]
                }
            ),
        )
    ]

    assert current_dealership_reference_context(messages) == [
        {
            "position": 1,
            "dealershipId": "dealer-bolton",
            "name": "Northstar Bolton",
            "town": "Bolton",
            "postcode": "BL3 2AW",
        },
        {
            "position": 2,
            "dealershipId": "dealer-manchester",
            "name": "Northstar Manchester",
            "town": "Manchester",
            "postcode": "M20 2YY",
        },
    ]
