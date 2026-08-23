import pytest

from webchat.orchestration.tools.business import BusinessInformationToolHandler
from webchat.orchestration.tools.business_information import BusinessInformationResolver
from webchat.orchestration.tools.dealerships import DealershipToolHandler

BUSINESS_INFORMATION = {
    "organisation": "Northstar Motors",
    "currency": "GBP",
    "market": "United Kingdom",
    "finance": {
        "notice": "Finance is subject to status. Northstar is a broker, not a lender.",
        "minimumAge": 18,
    },
    "partExchange": {"estimateNotice": "Estimates are indicative and subject to inspection."},
    "privacyContact": "privacy@northstarmotors.example",
}


class BusinessGateway:
    async def get_business_information(self):
        return BUSINESS_INFORMATION


@pytest.mark.parametrize(
    ("topic", "question", "expected_key"),
    [
        ("finance", "How does finance work?", "finance.notice"),
        ("finance", "What is the minimum age for finance?", "finance.minimum_age"),
        ("privacy", "How do you use my personal data?", "privacy.contact"),
        (
            "part_exchange",
            "How is the part-exchange estimate calculated?",
            "part_exchange.estimate_notice",
        ),
        ("general", "Which currency do you use?", "organisation.currency"),
    ],
)
def test_business_questions_resolve_to_one_authoritative_fact(
    topic: str, question: str, expected_key: str
) -> None:
    resolution = BusinessInformationResolver().resolve(
        BUSINESS_INFORMATION,
        topic=topic,
        question=question,
    )

    assert resolution.outcome == "matched"
    assert [fact.key for fact in resolution.facts] == [expected_key]


def test_part_exchange_context_does_not_make_an_unrelated_fact_answerable() -> None:
    resolution = BusinessInformationResolver().resolve(
        BUSINESS_INFORMATION,
        topic="part_exchange",
        question="Will you pick up my car?",
    )

    assert resolution.outcome == "unavailable"
    assert resolution.facts == ()


def test_wrong_business_goal_cannot_leak_another_topic() -> None:
    resolution = BusinessInformationResolver().resolve(
        BUSINESS_INFORMATION,
        topic="part_exchange",
        question="What is your privacy email?",
    )

    assert resolution.outcome == "unavailable"


@pytest.mark.asyncio
async def test_matched_business_tool_returns_only_the_selected_fact() -> None:
    result = await BusinessInformationToolHandler(BusinessGateway()).execute(
        "get_business_information",
        {
            "topic": "privacy",
            "question": "How do you use my personal data?",
        },
    )

    assert result.view_type == "business_information"
    assert result.view_payload == {
        "version": 2,
        "organisation": "Northstar Motors",
        "topic": "privacy",
        "facts": [
            {
                "key": "privacy.contact",
                "topic": "privacy",
                "label": "Privacy contact",
                "value": "privacy@northstarmotors.example",
            }
        ],
    }
    assert "finance" not in result.view_payload
    assert "partExchange" not in result.view_payload


@pytest.mark.asyncio
async def test_unanswerable_business_tool_fails_closed_without_a_card() -> None:
    result = await BusinessInformationToolHandler(BusinessGateway()).execute(
        "get_business_information",
        {
            "topic": "part_exchange",
            "question": "Will you pick up my car?",
        },
    )

    assert result.view_type is None
    assert result.view_payload is None
    assert result.facts == {
        "outcome": "unavailable",
        "topic": "part_exchange",
        "factKeys": [],
    }
    assert "don't have confirmed Northstar information" in result.text


@pytest.mark.asyncio
async def test_dealership_contact_options_are_an_application_owned_chooser() -> None:
    result = await DealershipToolHandler(BusinessGateway()).execute(
        "show_dealership_contact_options", {}
    )

    assert result.view_type == "suggestion_list"
    assert result.text == "Choose how you'd like to contact a Northstar dealership."
    assert len(result.view_payload["suggestions"]) == 4
    assert result.facts["options"] == [
        "start_callback",
        "start_dealership_message",
        "show_dealerships",
        "show_opening_hours",
    ]
