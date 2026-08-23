import pytest

from webchat.integrations.fake_llm import FakeLlmProvider
from webchat.orchestration.routing.parsers import location_query


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


def test_generic_dealership_request_is_not_mistaken_for_a_town() -> None:
    assert location_query("show me dealership contact details") is None


@pytest.mark.asyncio
async def test_named_dealership_contact_question_is_filtered_to_that_town() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [{"role": "user", "content": "What is the phone number and email for Manchester sales?"}]
    )

    assert reply.plan is not None
    assert (reply.plan.domain, reply.plan.goal) == ("dealership", "view_contact")
    assert reply.plan.arguments == {"town": "Manchester"}


@pytest.mark.asyncio
async def test_named_dealership_hours_are_filtered_to_that_town() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [{"role": "user", "content": "Manchester dealership holiday opening hours"}]
    )

    assert reply.plan is not None
    assert (reply.plan.domain, reply.plan.goal) == (
        "dealership",
        "view_opening_hours",
    )
    assert reply.plan.arguments == {"town": "Manchester"}


@pytest.mark.asyncio
async def test_named_department_hours_keep_both_town_and_department() -> None:
    reply = await FakeLlmProvider().generate_turn(
        [{"role": "user", "content": "Stockport parts opening hours"}]
    )

    assert reply.plan is not None
    assert reply.plan.arguments == {
        "town": "Stockport",
        "department": "parts",
    }
