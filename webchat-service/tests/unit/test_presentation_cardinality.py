import pytest

from webchat.orchestration.contracts.response import (
    FactSegment,
    GroundedMessageDraft,
    GroundedResponseDraft,
    ParagraphBlock,
    TextSegment,
)
from webchat.orchestration.fact_normalization import FactNormalizer, _number_result_items
from webchat.orchestration.grounding import (
    GroundingValidator,
    normalize_explicit_card_owned_messages,
)
from webchat.orchestration.tools.result import ToolResult


@pytest.mark.parametrize(
    "container",
    ["vehicles", "items", "offers", "dealerships", "services", "slots"],
)
def test_single_resolved_result_is_never_presented_as_option_one(container: str) -> None:
    payload = {container: [{"id": "result-1", "optionNumber": 99}]}

    normalized = _number_result_items(payload)

    assert normalized[container] == [{"id": "result-1"}]


@pytest.mark.parametrize(
    "container",
    ["vehicles", "items", "offers", "dealerships", "services", "slots"],
)
def test_multiple_results_receive_stable_application_owned_positions(container: str) -> None:
    payload = {
        container: [
            {"id": "result-a", "optionNumber": 99},
            {"id": "result-b", "optionNumber": 99},
        ]
    }

    normalized = _number_result_items(payload)

    assert [item["optionNumber"] for item in normalized[container]] == [1, 2]


def test_explicit_card_result_removes_duplicate_fact_paragraphs_without_failing() -> None:
    item = {
        "id": "dealer-stockport",
        "name": "Northstar Stockport",
        "addressLine": "24 Wellington Road",
        "postcode": "SK4 2BE",
        "phone": "0161 555 0124",
        "email": "stockport@northstarmotors.example",
    }
    payload = {"version": 1, "items": [item]}
    result = FactNormalizer().normalize(
        result_id="result-dealership",
        call_id="call-dealership",
        intent_id="intent-dealership",
        tool="list_dealerships",
        result=ToolResult("ignored", "dealership_list", payload, payload),
    )
    facts = {fact.field: fact.factId for fact in result.facts}
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                blocks=[
                    ParagraphBlock(
                        type="paragraph",
                        segments=[TextSegment(type="text", text="Here are the contact details.")],
                    ),
                    ParagraphBlock(
                        type="paragraph",
                        segments=[
                            TextSegment(type="text", text="Address: "),
                            FactSegment(type="fact", factId=facts["items.1.addressLine"]),
                        ],
                    ),
                    ParagraphBlock(
                        type="paragraph",
                        segments=[
                            TextSegment(type="text", text="Phone: "),
                            FactSegment(type="fact", factId=facts["items.1.phone"]),
                            TextSegment(type="text", text=". Email: "),
                            FactSegment(type="fact", factId=facts["items.1.email"]),
                        ],
                    ),
                ],
            )
        ],
        cardReferences=[result.availableCards[0].reference],
    )
    resolved = GroundingValidator().resolve(draft, [result])

    normalized = normalize_explicit_card_owned_messages(
        resolved.messages,
        resolved.cards,
        [result],
    )

    assert normalized[0].text == "Here are the contact details."
    assert normalized[0].blocks == (
        {
            "type": "paragraph",
            "segments": [{"type": "text", "text": "Here are the contact details."}],
        },
    )


def test_card_normalization_drops_an_empty_placeholder_when_other_prose_remains() -> None:
    payload = {
        "version": 1,
        "items": [
            {
                "id": "veh-007",
                "make": "Jaguar",
                "model": "F-PACE",
                "availability": "reserved",
            }
        ],
    }
    result = FactNormalizer().normalize(
        result_id="result-reserved-jaguar",
        call_id="call-reserved-jaguar",
        intent_id="intent-reserved-jaguar",
        tool="get_vehicle_availability",
        result=ToolResult("ignored", "vehicle_availability", payload, payload),
    )
    availability_fact = next(
        fact.factId for fact in result.facts if fact.field == "items.1.availability"
    )
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                blocks=[
                    ParagraphBlock(
                        type="paragraph",
                        segments=[FactSegment(type="fact", factId=availability_fact)],
                    )
                ],
            ),
            GroundedMessageDraft(
                purpose="answer",
                blocks=[
                    ParagraphBlock(
                        type="paragraph",
                        segments=[
                            TextSegment(
                                type="text",
                                text="You can still register your interest or send a sales enquiry.",
                            )
                        ],
                    )
                ],
            ),
        ],
        cardReferences=[result.availableCards[0].reference],
    )
    resolved = GroundingValidator().resolve(draft, [result])

    normalized = normalize_explicit_card_owned_messages(
        resolved.messages,
        resolved.cards,
        [result],
    )

    assert "Here are the requested details." not in [message.text for message in normalized]
    assert "reserved" not in [message.text for message in normalized]
    assert normalized[0].text == "You can still register your interest or send a sales enquiry."
