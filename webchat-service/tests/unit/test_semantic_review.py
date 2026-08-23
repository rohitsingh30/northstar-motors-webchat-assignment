import pytest
from pydantic import ValidationError

from webchat.integrations.contracts import ResponseProposal, ToolCall, TurnProposal
from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.planning.review import (
    ACCEPT_REVIEW_TOOL,
    CORRECT_REVIEW_TOOL,
    apply_review,
    review_payload,
    turn_review_definitions,
)


class NeverExecute:
    async def execute(self, name, arguments, conversation_id=None):
        raise AssertionError("review validation must not execute tools")


@pytest.fixture
def catalogue() -> UnifiedToolCatalog:
    return UnifiedToolCatalog(NeverExecute())


def candidate() -> TurnProposal:
    return TurnProposal([ToolCall("candidate", "search_vehicles", {"maxPricePence": 4_000_000})])


def test_provider_review_contract_uses_small_outcome_specific_functions() -> None:
    definitions = turn_review_definitions()

    assert {definition["name"] for definition in definitions} == {
        "accept_customer_turn_proposal",
        "correct_customer_turn_proposal",
        "clarify_customer_turn_proposal",
        "reject_customer_turn_proposal",
    }
    accept = next(item for item in definitions if item["name"] == ACCEPT_REVIEW_TOOL)
    assert accept["parameters"]["properties"] == {}
    assert review_payload(ACCEPT_REVIEW_TOOL, {}) == {
        "version": 3,
        "decision": "accept",
        "reasonCodes": ["CANDIDATE_VALID"],
    }


def test_review_function_rejects_fields_from_another_outcome() -> None:
    with pytest.raises(ValidationError):
        review_payload(
            CORRECT_REVIEW_TOOL,
            {
                "reasonCodes": ["WRONG_TOOL"],
                "clarification": "Which dealership?",
            },
        )


def test_reviewer_can_accept_concrete_tool_call_without_rewriting(
    catalogue: UnifiedToolCatalog,
) -> None:
    proposal, review = apply_review(
        {
            "version": 3,
            "decision": "accept",
            "reasonCodes": ["CANDIDATE_VALID"],
        },
        candidate(),
        catalogue,
        set(),
    )

    assert proposal == candidate()
    assert review.outcome == "accept"


def test_reviewer_accepts_a_semantic_decision_only_for_an_actionable_pending_interaction(
    catalogue: UnifiedToolCatalog,
) -> None:
    decision = TurnProposal(interaction_decision="accept")
    pending = {
        "version": 1,
        "kind": "single_action",
        "prompt": "Would you like help contacting a dealership?",
        "actions": [{"type": "show_dealership_contact_options"}],
    }

    proposal, review = apply_review(
        {
            "version": 3,
            "decision": "accept",
            "reasonCodes": ["CANDIDATE_VALID"],
        },
        decision,
        catalogue,
        set(),
        pending_interaction=pending,
    )

    assert proposal == decision
    assert review.outcome == "accept"


def test_reviewer_rejects_an_interaction_decision_without_matching_pending_metadata(
    catalogue: UnifiedToolCatalog,
) -> None:
    review_payload = {
        "version": 3,
        "decision": "accept",
        "reasonCodes": ["CANDIDATE_VALID"],
    }

    with pytest.raises(ValueError, match="requires a pending interaction"):
        apply_review(
            review_payload,
            TurnProposal(interaction_decision="accept"),
            catalogue,
            set(),
        )
    with pytest.raises(ValueError, match="input interactions"):
        apply_review(
            review_payload,
            TurnProposal(interaction_decision="accept"),
            catalogue,
            set(),
            pending_interaction={"kind": "input"},
        )


def test_reviewer_can_correct_a_candidate_to_a_pending_interaction_decision(
    catalogue: UnifiedToolCatalog,
) -> None:
    proposal, review = apply_review(
        {
            "version": 3,
            "decision": "correct",
            "reasonCodes": ["WRONG_TOOL"],
            "correctedProposal": {"interactionDecision": "decline"},
        },
        candidate(),
        catalogue,
        set(),
        pending_interaction={"kind": "choice"},
    )

    assert proposal.interaction_decision == "decline"
    assert review.outcome == "correct"


def test_reviewer_can_replace_wrong_response_with_named_service_tool(
    catalogue: UnifiedToolCatalog,
) -> None:
    proposal, review = apply_review(
        {
            "version": 3,
            "decision": "correct",
            "reasonCodes": ["WRONG_TOOL"],
            "correctedProposal": {
                "toolCalls": [
                    {
                        "name": "get_service_information",
                        "arguments": {"q": "What does tyre fitting cost?"},
                    }
                ]
            },
        },
        TurnProposal(response=ResponseProposal("conversation", "Try asking about offers.")),
        catalogue,
        set(),
    )

    assert proposal.tool_calls[0].name == "get_service_information"
    assert review.outcome == "correct"


def test_reviewer_knowledge_correction_is_materialized_from_retrieved_text(
    catalogue: UnifiedToolCatalog,
) -> None:
    proposal, review = apply_review(
        {
            "version": 3,
            "decision": "correct",
            "reasonCodes": ["WRONG_TOOL"],
            "correctedProposal": {
                "response": {
                    "mode": "answer",
                    "grounding": "knowledge",
                    "citationIds": ["customer.pch"],
                }
            },
        },
        candidate(),
        catalogue,
        {"customer.pch": "PCH means Personal Contract Hire."},
    )

    assert proposal.response == ResponseProposal(
        "answer",
        "PCH means Personal Contract Hire.",
        ("customer.pch",),
        "knowledge",
    )
    assert review.outcome == "correct"


def test_reviewer_clarification_never_executes_a_tool(
    catalogue: UnifiedToolCatalog,
) -> None:
    proposal, review = apply_review(
        {
            "version": 3,
            "decision": "clarify",
            "reasonCodes": ["ENTITY_AMBIGUOUS"],
            "clarification": "Which dealership do you mean?",
            "blockingTool": "get_dealership",
            "blockingFields": ["id"],
        },
        candidate(),
        catalogue,
        set(),
    )

    assert proposal.tool_calls == []
    assert proposal.response is not None
    assert proposal.response.text == "Which dealership do you mean?"
    assert proposal.response.interaction is not None
    assert proposal.response.interaction.model_dump(exclude_none=True) == {
        "version": 1,
        "kind": "input",
        "prompt": "Which dealership do you mean?",
        "actions": [],
        "blocking_tool": "get_dealership",
        "blocking_fields": ["id"],
        "blocking_preconditions": [],
    }
    assert review.outcome == "clarify"


def test_reviewer_preserves_finite_clarification_options_as_reply_suggestions(
    catalogue: UnifiedToolCatalog,
) -> None:
    proposal, review = apply_review(
        {
            "version": 3,
            "decision": "clarify",
            "reasonCodes": ["ENTITY_AMBIGUOUS"],
            "clarification": "Which BMW would you like to compare with the MINI Countryman?",
            "blockingTool": "compare_vehicles",
            "blockingFields": ["vehicleIds"],
            "options": [
                "BMW 3 Series 320d M Sport",
                "BMW 1 Series 118i M Sport",
            ],
        },
        candidate(),
        catalogue,
        set(),
    )

    assert proposal.response is not None
    assert proposal.response.suggestions == (
        "BMW 3 Series 320d M Sport",
        "BMW 1 Series 118i M Sport",
    )
    assert proposal.response.interaction is not None
    assert proposal.response.interaction.kind == "input"
    assert review.outcome == "clarify"


@pytest.mark.parametrize(
    "options",
    [
        ["Only one option"],
        ["BMW 3 Series", "bmw 3 series"],
        ["", "BMW 1 Series"],
    ],
)
def test_reviewer_rejects_invalid_clarification_options(
    catalogue: UnifiedToolCatalog, options: list[str]
) -> None:
    with pytest.raises(ValidationError):
        apply_review(
            {
                "version": 3,
                "decision": "clarify",
                "reasonCodes": ["ENTITY_AMBIGUOUS"],
                "clarification": "Which BMW?",
                "blockingTool": "compare_vehicles",
                "blockingFields": ["vehicleIds"],
                "options": options,
            },
            candidate(),
            catalogue,
            set(),
        )


def test_reviewer_cannot_clarify_for_optional_workshop_chooser_fields(
    catalogue: UnifiedToolCatalog,
) -> None:
    with pytest.raises(ValueError, match="not required"):
        apply_review(
            {
                "version": 3,
                "decision": "clarify",
                "reasonCodes": ["CLARIFICATION_REQUIRED"],
                "clarification": "Which service would you like?",
                "blockingTool": "list_workshop_slots",
                "blockingFields": ["serviceTypeId"],
            },
            candidate(),
            catalogue,
            set(),
        )


def test_invalid_corrected_arguments_are_rejected(catalogue: UnifiedToolCatalog) -> None:
    with pytest.raises(ValidationError):
        apply_review(
            {
                "version": 3,
                "decision": "correct",
                "reasonCodes": ["WRONG_TOOL"],
                "correctedProposal": {
                    "toolCalls": [{"name": "search_vehicles", "arguments": {"unknown": "unsafe"}}]
                },
            },
            candidate(),
            catalogue,
            set(),
        )


def test_response_citations_must_come_from_retrieved_customer_evidence(
    catalogue: UnifiedToolCatalog,
) -> None:
    with pytest.raises(ValueError, match="invalid citation"):
        apply_review(
            {
                "version": 3,
                "decision": "accept",
                "reasonCodes": ["CANDIDATE_VALID"],
            },
            TurnProposal(
                response=ResponseProposal(
                    "answer", "PCH means...", ("invented.source",), "knowledge"
                )
            ),
            catalogue,
            {"customer.pch"},
        )


@pytest.mark.parametrize(
    "proposal",
    [
        TurnProposal(
            [ToolCall("offers", "list_offers", {})],
            ResponseProposal("conversation", "Here you go."),
        ),
        TurnProposal(
            [
                ToolCall("offers", "list_offers", {}),
                ToolCall("locations", "list_dealerships", {}),
            ]
        ),
    ],
)
def test_review_rejects_proposals_that_cannot_produce_one_coherent_response(
    catalogue: UnifiedToolCatalog, proposal: TurnProposal
) -> None:
    with pytest.raises(ValueError):
        apply_review(
            {
                "version": 3,
                "decision": "accept",
                "reasonCodes": ["CANDIDATE_VALID"],
            },
            proposal,
            catalogue,
            set(),
        )


def test_uncited_factual_answer_is_rejected_even_when_reviewer_accepts(
    catalogue: UnifiedToolCatalog,
) -> None:
    with pytest.raises(ValueError, match="requires retrieved knowledge"):
        apply_review(
            {
                "version": 3,
                "decision": "accept",
                "reasonCodes": ["CANDIDATE_VALID"],
            },
            TurnProposal(
                response=ResponseProposal(
                    "answer", "Here are seven vehicles from the page.", grounding="none"
                )
            ),
            catalogue,
            set(),
        )


def test_tool_fact_answer_requires_a_tool_result_from_this_turn(
    catalogue: UnifiedToolCatalog,
) -> None:
    proposal = TurnProposal(
        response=ResponseProposal(
            "answer", "Tyre fitting is price on request.", grounding="tool_facts"
        )
    )
    review_payload = {
        "version": 3,
        "decision": "accept",
        "reasonCodes": ["CANDIDATE_VALID"],
    }

    with pytest.raises(ValueError, match="tool executed in this turn"):
        apply_review(review_payload, proposal, catalogue, set())

    reviewed, _ = apply_review(
        review_payload,
        proposal,
        catalogue,
        set(),
        has_trusted_tool_facts=True,
    )
    assert reviewed == proposal
