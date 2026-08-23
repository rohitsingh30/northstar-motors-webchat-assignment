import json

import httpx
import pytest

from webchat.integrations.contracts import (
    ReviewerContext,
    ReviewUnavailableError,
    SemanticMessages,
)
from webchat.integrations.hosted_llm import HostedLlmProvider, response_input
from webchat.integrations.hosted_llm.candidates import _retrieval_query, _retrieve_candidates
from webchat.integrations.hosted_llm.provider import _provider_base_url
from webchat.integrations.hosted_llm.review import (
    _review_repair_tools,
    _reviewer_context_view,
)
from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.planning.review import (
    CORRECT_REVIEW_TOOL,
    TURN_REVIEW_TOOLS,
    ReviewValidationError,
)
from webchat.orchestration.retrieval import CandidateSet, KnowledgeEntry


class NeverExecute:
    async def execute(self, name, arguments, conversation_id=None):
        raise AssertionError


class FixedRetriever:
    def __init__(self, catalogue: UnifiedToolCatalog):
        names = {
            "search_vehicles",
            "refine_vehicle_search",
            "get_vehicle_availability",
            "get_service_information",
            "list_offers",
            "list_opening_hours",
            "list_workshop_slots",
            "prepare_dealership_message",
        }
        self.candidates = CandidateSet(
            tuple(tool for tool in catalogue.planner_tools() if tool.id in names),
            (
                KnowledgeEntry(
                    "customer.pch",
                    "PCH",
                    "PCH means Personal Contract Hire.",
                    "docs/CUSTOMER-KNOWLEDGE.md#PCH",
                    "customer",
                ),
                KnowledgeEntry(
                    "customer.pcp",
                    "PCP",
                    "PCP means Personal Contract Purchase.",
                    "docs/CUSTOMER-KNOWLEDGE.md#PCP",
                    "customer",
                ),
                KnowledgeEntry(
                    "customer.vehicle_pickup_and_collection_policy",
                    "Vehicle pickup and collection policy",
                    (
                        "I don't have confirmed Northstar information about vehicle collection "
                        "or home pickup. Would you like me to help you contact a dealership?"
                    ),
                    "docs/CUSTOMER-KNOWLEDGE.md#Vehicle pickup and collection policy",
                    "customer",
                    {"type": "show_dealership_contact_options"},
                ),
            ),
        )

    def retrieve(self, query, *, tool_limit=8, knowledge_limit=5):
        return self.candidates


def provider(http: httpx.AsyncClient) -> HostedLlmProvider:
    catalogue = UnifiedToolCatalog(NeverExecute())
    return HostedLlmProvider(
        provider_url="https://provider.example/v1",
        api_key="shared-key",
        model="hosted-model",
        catalogue=catalogue,
        retriever=FixedRetriever(catalogue),
        client=http,
    )


@pytest.mark.asyncio
async def test_default_hosted_client_uses_the_configured_request_budget() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    hosted = HostedLlmProvider(
        provider_url="https://provider.example/v1",
        api_key="shared-key",
        model="hosted-model",
        catalogue=catalogue,
        request_timeout_seconds=44,
    )

    try:
        assert hosted._client.timeout.read == 44
        assert hosted._client.timeout.connect == 5
    finally:
        await hosted.close()


def messages(text: str) -> SemanticMessages:
    return SemanticMessages(
        [{"role": "user", "content": text}],
        ReviewerContext(latest_customer_message=text),
    )


def test_retrieval_query_includes_bounded_active_operation_state() -> None:
    query = _retrieval_query(
        ReviewerContext(
            latest_customer_message="I want tyre",
            workflow_state={
                "version": 3,
                "activeWorkflow": "workshop_booking",
                "stage": "choosing_time",
                "entities": {"serviceTypeId": "interim-service"},
                "constraints": {"dealershipTown": "Stockport"},
                "lastTool": "list_workshop_slots",
                "lastRenderer": "slot_list",
            },
        )
    )

    assert "Customer request: I want tyre" in query
    assert "Active operation: workshop_booking" in query
    assert '"serviceTypeId":"interim-service"' in query


def test_retrieval_query_carries_the_previous_offer_into_short_follow_ups() -> None:
    query = _retrieval_query(
        ReviewerContext(
            latest_customer_message="yes",
            previous_turn=[
                {"role": "user", "text": "Will you pick up my car?"},
                {
                    "role": "assistant",
                    "text": (
                        "I don't have confirmed Northstar information about vehicle collection "
                        "or home pickup. Would you like me to help you contact a dealership?"
                    ),
                },
            ],
        )
    )

    assert "Customer request: yes" in query
    assert "Immediately preceding exchange" in query
    assert "contact a dealership" in query


def test_reviewer_context_exposes_recent_customer_authored_prefill_evidence() -> None:
    context = _reviewer_context_view(
        ReviewerContext(
            latest_customer_message="Send a message to the dealership",
            recent_customer_messages=[
                "Please ask whether my service plan covers tyres.",
                "Where is the Stockport dealership?",
            ],
        )
    )

    assert context["recentCustomerMessages"] == [
        "Please ask whether my service plan covers tyres.",
        "Where is the Stockport dealership?",
    ]


def test_reviewer_receives_the_same_page_vehicle_identity_as_the_planner() -> None:
    context = _reviewer_context_view(
        ReviewerContext(
            latest_customer_message="When will the sold BMW be available?",
            page_vehicles=[
                {
                    "position": 10,
                    "vehicleId": "veh-013",
                    "label": "BMW 1 Series",
                    "year": 2025,
                    "make": "BMW",
                    "model": "1 Series",
                    "variant": "118i M Sport",
                    "bodyStyle": "Hatchback",
                    "colour": "Fire Red",
                    "pricePence": 1_850_000,
                    "mileage": 28_500,
                    "fuelType": "Petrol",
                    "transmission": "Automatic",
                    "availability": "sold",
                    "dealershipTown": "Manchester",
                },
                {
                    "position": 7,
                    "vehicleId": "veh-049",
                    "label": "BMW 1 Series",
                    "year": 2026,
                    "make": "BMW",
                    "model": "1 Series",
                    "variant": "118i M Sport",
                    "bodyStyle": "Hatchback",
                    "availability": "available",
                    "dealershipTown": "Manchester",
                },
            ],
        )
    )

    assert context["pageVehicles"][0] == {
        "position": 10,
        "vehicleId": "veh-013",
        "label": "BMW 1 Series",
        "year": 2025,
        "make": "BMW",
        "model": "1 Series",
        "variant": "118i M Sport",
        "bodyStyle": "Hatchback",
        "colour": "Fire Red",
        "pricePence": 1_850_000,
        "mileage": 28_500,
        "fuelType": "Petrol",
        "transmission": "Automatic",
        "availability": "sold",
        "dealershipTown": "Manchester",
    }
    assert context["pageVehicles"][1]["year"] == 2026
    assert context["pageVehicles"][1]["availability"] == "available"


@pytest.mark.asyncio
async def test_reviewer_can_validate_a_uniquely_resolved_sold_page_vehicle() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["parallel_tool_calls"] is True:
            return httpx.Response(
                200,
                json=function_call("get_vehicle_availability", {"id": "veh-013"}),
            )
        envelope = json.loads(body["input"][0]["content"])
        assert envelope["context"]["pageVehicles"] == [
            {
                "position": 10,
                "vehicleId": "veh-013",
                "label": "BMW 1 Series",
                "year": 2025,
                "make": "BMW",
                "model": "1 Series",
                "variant": "118i M Sport",
                "bodyStyle": "Hatchback",
                "availability": "sold",
                "dealershipTown": "Manchester",
            },
            {
                "position": 7,
                "vehicleId": "veh-049",
                "label": "BMW 1 Series",
                "year": 2026,
                "make": "BMW",
                "model": "1 Series",
                "variant": "118i M Sport",
                "bodyStyle": "Hatchback",
                "availability": "available",
                "dealershipTown": "Manchester",
            },
        ]
        return httpx.Response(200, json=accepted_review())

    page_vehicles = [
        {
            "position": 10,
            "vehicleId": "veh-013",
            "label": "BMW 1 Series",
            "year": 2025,
            "make": "BMW",
            "model": "1 Series",
            "variant": "118i M Sport",
            "bodyStyle": "Hatchback",
            "availability": "sold",
            "dealershipTown": "Manchester",
        },
        {
            "position": 7,
            "vehicleId": "veh-049",
            "label": "BMW 1 Series",
            "year": 2026,
            "make": "BMW",
            "model": "1 Series",
            "variant": "118i M Sport",
            "bodyStyle": "Hatchback",
            "availability": "available",
            "dealershipTown": "Manchester",
        },
    ]
    semantic_messages = SemanticMessages(
        [{"role": "user", "content": "When will the sold BMW be available?"}],
        ReviewerContext(
            latest_customer_message="When will the sold BMW be available?",
            page_vehicles=page_vehicles,
        ),
    )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(semantic_messages)

    assert reply.tool_calls[0].name == "get_vehicle_availability"
    assert reply.tool_calls[0].arguments == {"id": "veh-013"}
    assert reply.review and reply.review.outcome == "accept"


def test_candidate_retrieval_unions_latest_request_and_follow_up_context() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())

    class QuerySensitiveRetriever:
        def retrieve(self, query, *, tool_limit=8, knowledge_limit=5):
            del tool_limit, knowledge_limit
            tool_name = (
                "list_offers"
                if query == "show me current offers"
                else "show_dealership_contact_options"
            )
            return CandidateSet((catalogue.get(tool_name),), ())

    candidates = _retrieve_candidates(
        QuerySensitiveRetriever(),
        ReviewerContext(
            latest_customer_message="show me current offers",
            previous_turn=[
                {"role": "user", "text": "I need an MOT"},
                {"role": "assistant", "text": "Workshop times are shown below."},
            ],
        ),
    )

    assert [tool.id for tool in candidates.tools] == [
        "list_offers",
        "show_dealership_contact_options",
    ]


def function_call(name: str, arguments: dict, call_id: str = "call-1") -> dict:
    return {
        "output": [
            {
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(arguments),
            }
        ]
    }


def accepted_review() -> dict:
    return review_result({"version": 3, "decision": "accept", "reasonCodes": ["CANDIDATE_VALID"]})


def review_result(payload: dict, call_id: str = "review-1") -> dict:
    decision = payload["decision"]
    names = {
        "accept": "accept_customer_turn_proposal",
        "correct": "correct_customer_turn_proposal",
        "clarify": "clarify_customer_turn_proposal",
        "reject": "reject_customer_turn_proposal",
    }
    arguments = {key: value for key, value in payload.items() if key not in {"version", "decision"}}
    if decision == "accept":
        arguments = {}
    return function_call(names[decision], arguments, call_id)


def test_review_repair_outcomes_come_from_typed_validation_failure() -> None:
    invalid_optional_blocker = ReviewValidationError(
        "OPTIONAL_FIELD_IS_NOT_A_BLOCKER",
        "safe message",
        frozenset({CORRECT_REVIEW_TOOL}),
    )

    assert _review_repair_tools(invalid_optional_blocker) == frozenset({CORRECT_REVIEW_TOOL})
    assert _review_repair_tools(ValueError("malformed clarification")) == TURN_REVIEW_TOOLS


@pytest.mark.asyncio
async def test_planner_emits_native_tool_call_then_independent_review_accepts_it() -> None:
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        assert body["store"] is False
        assert request.headers["Authorization"] == "Bearer shared-key"
        assert "api-key" not in request.headers
        if body["parallel_tool_calls"] is True:
            assert body["parallel_tool_calls"] is True
            names = {tool["name"] for tool in body["tools"]}
            assert {
                "search_vehicles",
                "answer_from_knowledge",
                "respond_socially",
            }.issubset(names)
            return httpx.Response(200, json=function_call("search_vehicles", {"make": "Volvo"}))
        envelope = json.loads(body["input"][0]["content"])
        assert envelope["candidateProposal"]["toolCalls"] == [
            {"name": "search_vehicles", "arguments": {"make": "Volvo"}}
        ]
        assert any(
            tool["id"] == "get_service_information" for tool in envelope["executableToolCatalogue"]
        )
        return httpx.Response(200, json=accepted_review())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(messages("Show me Volvos"))

    assert reply.tool_calls[0].name == "search_vehicles"
    assert reply.tool_calls[0].arguments == {"make": "Volvo"}
    assert reply.review and reply.review.outcome == "accept"
    assert len(calls) == 2
    assert calls[0]["instructions"] != calls[1]["instructions"]


@pytest.mark.asyncio
async def test_reviewer_finite_options_reach_the_provider_reply_as_suggestions() -> None:
    options = (
        "BMW 3 Series 320d M Sport",
        "BMW 1 Series 118i M Sport",
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["parallel_tool_calls"] is True:
            return httpx.Response(200, json=function_call("search_vehicles", {}))
        clarification_tool = next(
            tool
            for tool in body["tools"]
            if tool["name"] == "clarify_customer_turn_proposal"
        )
        assert "options" in clarification_tool["parameters"]["properties"]
        return httpx.Response(
            200,
            json=review_result(
                {
                    "version": 3,
                    "decision": "clarify",
                    "reasonCodes": ["ENTITY_AMBIGUOUS"],
                    "clarification": (
                        "Which BMW would you like to compare with the MINI Countryman?"
                    ),
                    "blockingTool": "compare_vehicles",
                    "blockingFields": ["vehicleIds"],
                    "options": list(options),
                }
            ),
        )

    semantic_messages = SemanticMessages(
        [{"role": "user", "content": "Compare the MINI with a BMW"}],
        ReviewerContext(latest_customer_message="Compare the MINI with a BMW"),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(semantic_messages)

    assert reply.text == "Which BMW would you like to compare with the MINI Countryman?"
    assert reply.suggestions == options
    assert reply.interaction is not None and reply.interaction.kind == "input"
    assert reply.review and reply.review.outcome == "clarify"


@pytest.mark.asyncio
async def test_hosted_refinement_can_express_a_negative_vehicle_preference() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["parallel_tool_calls"] is True:
            refinement = next(
                tool for tool in body["tools"] if tool["name"] == "refine_vehicle_search"
            )
            assert "excludedMakes" in refinement["parameters"]["properties"]
            return httpx.Response(
                200,
                json=function_call(
                    "refine_vehicle_search",
                    {"excludedMakes": ["Land Rover"]},
                ),
            )
        envelope = json.loads(body["input"][0]["content"])
        assert envelope["latestCustomerMessage"] == "Anything other than Land Rover"
        assert envelope["context"]["vehicleSearchState"]["filters"] == {
            "bodyStyle": "SUV",
            "fuelType": "Hybrid",
            "maxPricePence": 4_500_000,
        }
        return httpx.Response(200, json=accepted_review())

    context = ReviewerContext(
        latest_customer_message="Anything other than Land Rover",
        workflow_state={
            "version": 3,
            "activeWorkflow": "vehicle_search",
            "stage": "viewing",
            "constraints": {
                "bodyStyle": "SUV",
                "fuelType": "Hybrid",
                "maxPricePence": 4_500_000,
            },
        },
        vehicle_search_state={
            "filters": {
                "bodyStyle": "SUV",
                "fuelType": "Hybrid",
                "maxPricePence": 4_500_000,
            },
            "page": 1,
        },
    )
    semantic_messages = SemanticMessages(
        [{"role": "user", "content": "Anything other than Land Rover"}],
        context,
    )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(semantic_messages)

    assert reply.tool_calls[0].name == "refine_vehicle_search"
    assert reply.tool_calls[0].arguments == {"excludedMakes": ["Land Rover"]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("recent_messages", "candidate_arguments", "review_decision", "expected_arguments"),
    [
        (
            [],
            {"message": "Send a message to the dealership"},
            {
                "version": 3,
                "decision": "correct",
                "reasonCodes": ["UNSUPPORTED_CONSTRAINT"],
                "correctedProposal": {
                    "toolCalls": [{"name": "prepare_dealership_message", "arguments": {}}]
                },
            },
            {},
        ),
        (
            ["Please ask whether my service plan covers tyres."],
            {"message": "Please ask whether my service plan covers tyres."},
            {"version": 3, "decision": "accept", "reasonCodes": ["CANDIDATE_VALID"]},
            {"message": "Please ask whether my service plan covers tyres."},
        ),
    ],
)
async def test_reviewer_enforces_contextual_draft_prefill_provenance(
    recent_messages, candidate_arguments, review_decision, expected_arguments
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["parallel_tool_calls"] is True:
            return httpx.Response(
                200,
                json=function_call("prepare_dealership_message", candidate_arguments),
            )
        envelope = json.loads(body["input"][0]["content"])
        assert envelope["context"]["recentCustomerMessages"] == recent_messages
        return httpx.Response(
            200,
            json=review_result(review_decision, "review-prefill"),
        )

    semantic_messages = SemanticMessages(
        [{"role": "user", "content": "Send a message to the dealership"}],
        ReviewerContext(
            latest_customer_message="Send a message to the dealership",
            recent_customer_messages=recent_messages,
        ),
    )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(semantic_messages)

    assert reply.tool_calls[0].name == "prepare_dealership_message"
    assert reply.tool_calls[0].arguments == expected_arguments


@pytest.mark.asyncio
async def test_reviewer_can_replace_generic_answer_with_named_service_tool() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["parallel_tool_calls"] is True:
            return httpx.Response(
                200,
                json=function_call(
                    "respond_socially",
                    {"act": "capabilities"},
                ),
            )
        return httpx.Response(
            200,
            json=review_result(
                {
                    "version": 3,
                    "decision": "correct",
                    "reasonCodes": ["WRONG_TOOL"],
                    "correctedProposal": {
                        "toolCalls": [
                            {
                                "name": "get_service_information",
                                "arguments": {"q": "What is the cost of tyre fitting?"},
                            }
                        ]
                    },
                },
                "review-2",
            ),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(messages("What is the cost of tyre fitting?"))

    assert reply.tool_calls[0].name == "get_service_information"
    assert reply.review and reply.review.outcome == "correct"


@pytest.mark.asyncio
async def test_customer_answer_must_use_retrieved_citation() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["parallel_tool_calls"] is True:
            return httpx.Response(
                200,
                json=function_call(
                    "answer_from_knowledge",
                    {"citationIds": ["customer.pch"]},
                ),
            )
        return httpx.Response(200, json=accepted_review())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(messages("What is PCH?"))

    assert reply.tool_calls == []
    assert reply.response_mode == "answer"
    assert reply.text == "PCH means Personal Contract Hire."
    assert reply.citation_ids == ("customer.pch",)


@pytest.mark.asyncio
async def test_multiple_knowledge_entries_keep_distinct_titled_sections() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["parallel_tool_calls"] is True:
            return httpx.Response(
                200,
                json=function_call(
                    "answer_from_knowledge",
                    {"citationIds": ["customer.pcp", "customer.pch"]},
                ),
            )
        return httpx.Response(200, json=accepted_review())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(messages("How does vehicle finance work?"))

    assert reply.text == (
        "- PCP: PCP means Personal Contract Purchase.\n- PCH: PCH means Personal Contract Hire."
    )
    assert reply.citation_ids == ("customer.pcp", "customer.pch")


@pytest.mark.asyncio
async def test_knowledge_answer_carries_its_reviewed_follow_up_action() -> None:
    citation = "customer.vehicle_pickup_and_collection_policy"

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["parallel_tool_calls"] is True:
            return httpx.Response(
                200,
                json=function_call(
                    "answer_from_knowledge",
                    {"citationIds": [citation]},
                ),
            )
        return httpx.Response(200, json=accepted_review())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(messages("Will you pick up my car?"))

    assert reply.citation_ids == (citation,)
    assert reply.interaction is not None
    assert reply.interaction.kind == "single_action"
    assert reply.interaction.actions == [{"type": "show_dealership_contact_options"}]


@pytest.mark.asyncio
async def test_reviewer_narrows_a_vague_finance_follow_up_to_the_single_offer_type() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["parallel_tool_calls"] is True:
            assert "smallest evidence set" in body["instructions"]
            return httpx.Response(
                200,
                json=function_call(
                    "answer_from_knowledge",
                    {"citationIds": ["customer.pcp", "customer.pch"]},
                ),
            )
        envelope = json.loads(body["input"][0]["content"])
        assert envelope["context"]["displayedOffers"] == [
            {
                "offerId": "offer-evoque",
                "make": "Land Rover",
                "model": "Range Rover Evoque",
                "productType": "PCH",
            }
        ]
        return httpx.Response(
            200,
            json=review_result(
                {
                    "version": 3,
                    "decision": "correct",
                    "reasonCodes": ["UNSUPPORTED_CONSTRAINT"],
                    "correctedProposal": {
                        "response": {
                            "mode": "answer",
                            "grounding": "knowledge",
                            "citationIds": ["customer.pch"],
                        }
                    },
                },
                "review-specificity",
            ),
        )

    context = ReviewerContext(
        latest_customer_message="How does vehicle finance work?",
        displayed_offers=[
            {
                "offerId": "offer-evoque",
                "make": "Land Rover",
                "model": "Range Rover Evoque",
                "productType": "PCH",
            }
        ],
    )
    semantic_messages = SemanticMessages(
        [{"role": "user", "content": "How does vehicle finance work?"}], context
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(semantic_messages)

    assert reply.text == "PCH means Personal Contract Hire."
    assert reply.citation_ids == ("customer.pch",)
    assert reply.review and reply.review.outcome == "correct"


@pytest.mark.asyncio
async def test_hosted_models_semantically_accept_a_persisted_interaction_without_action_arguments() -> (
    None
):
    pending = {
        "version": 1,
        "kind": "single_action",
        "prompt": "Would you like me to help you contact a dealership?",
        "actions": [{"type": "show_dealership_contact_options"}],
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["parallel_tool_calls"] is True:
            names = {tool["name"] for tool in body["tools"]}
            assert "accept_pending_interaction" in names
            assert "decline_pending_interaction" in names
            return httpx.Response(
                200,
                json=function_call("accept_pending_interaction", {}),
            )
        envelope = json.loads(body["input"][0]["content"])
        assert envelope["context"]["pendingInteraction"] == pending
        assert envelope["candidateProposal"]["interactionDecision"] == "accept"
        return httpx.Response(200, json=accepted_review())

    semantic_messages = SemanticMessages(
        [{"role": "user", "content": "That sounds good, please continue."}],
        ReviewerContext(
            latest_customer_message="That sounds good, please continue.",
            pending_interaction=pending,
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(semantic_messages)

    assert reply.interaction_decision == "accept"
    assert reply.text == ""
    assert reply.tool_calls == []


@pytest.mark.asyncio
async def test_planner_cannot_generate_uncited_vehicle_prose() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["parallel_tool_calls"] is True
        return httpx.Response(
            200,
            json=function_call(
                "answer_from_knowledge",
                {"citationIds": []},
            ),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        with pytest.raises(ValueError, match="invalid customer knowledge citations"):
            await provider(http).generate_turn(messages("Show me cars under £35,000."))


@pytest.mark.asyncio
async def test_reviewer_failure_closes_before_any_business_tool() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        if body["parallel_tool_calls"] is True:
            return httpx.Response(200, json=function_call("search_vehicles", {}))
        return httpx.Response(503, json={"error": "review unavailable"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        with pytest.raises(ReviewUnavailableError, match="review unavailable"):
            await provider(http).generate_turn(messages("show me cars"))

    assert calls == 4


@pytest.mark.asyncio
async def test_reviewer_recovers_on_the_third_bounded_attempt() -> None:
    calls = 0
    reviewer_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls, reviewer_calls
        calls += 1
        body = json.loads(request.content)
        if body["parallel_tool_calls"] is True:
            return httpx.Response(200, json=function_call("search_vehicles", {}))
        reviewer_calls += 1
        if reviewer_calls < 3:
            return httpx.Response(503, json={"error": "review unavailable"})
        return httpx.Response(200, json=accepted_review())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(messages("show me cars"))

    assert reply.tool_calls[0].name == "search_vehicles"
    assert calls == 4


@pytest.mark.asyncio
async def test_planner_recovers_once_from_a_malformed_response() -> None:
    planner_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal planner_calls
        body = json.loads(request.content)
        if body["parallel_tool_calls"] is True:
            planner_calls += 1
            if planner_calls == 1:
                return httpx.Response(200, json={"output": []})
            return httpx.Response(200, json=function_call("search_vehicles", {}))
        return httpx.Response(200, json=accepted_review())

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(messages("show me cars"))

    assert reply.tool_calls[0].name == "search_vehicles"
    assert planner_calls == 2


@pytest.mark.asyncio
async def test_optional_workshop_choice_is_repaired_to_safe_chooser() -> None:
    reviewer_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal reviewer_calls
        body = json.loads(request.content)
        if body["parallel_tool_calls"] is True:
            return httpx.Response(200, json=function_call("list_workshop_slots", {}))

        reviewer_calls += 1
        names = {tool["name"] for tool in body["tools"]}
        envelope = json.loads(body["input"][0]["content"])
        if reviewer_calls == 1:
            assert names == {
                "accept_customer_turn_proposal",
                "correct_customer_turn_proposal",
                "clarify_customer_turn_proposal",
                "reject_customer_turn_proposal",
            }
            return httpx.Response(
                200,
                json=review_result(
                    {
                        "version": 3,
                        "decision": "clarify",
                        "reasonCodes": ["CLARIFICATION_REQUIRED"],
                        "clarification": "Which workshop service would you like?",
                        "blockingTool": "refine_workshop_slots",
                        "blockingFields": ["serviceTypeName"],
                    }
                ),
            )
        feedback = envelope["deterministicValidationFeedback"]
        assert "optional" in feedback
        assert "earliest safe chooser" in feedback
        assert names == {"correct_customer_turn_proposal"}
        return httpx.Response(
            200,
            json=review_result(
                {
                    "version": 3,
                    "decision": "correct",
                    "reasonCodes": ["UNNECESSARY_CLARIFICATION"],
                    "correctedProposal": {
                        "toolCalls": [
                            {
                                "name": "refine_workshop_slots",
                                "arguments": {"serviceTypeName": "tyre"},
                            }
                        ]
                    },
                }
            ),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(
            SemanticMessages(
                [{"role": "user", "content": "I want tyre"}],
                ReviewerContext(
                    latest_customer_message="I want tyre",
                    workflow_state={
                        "version": 3,
                        "activeWorkflow": "workshop_booking",
                        "stage": "choosing_time",
                        "entities": {"serviceTypeId": "full-service"},
                        "constraints": {"dealershipTown": "Manchester"},
                    },
                ),
            )
        )

    assert reviewer_calls == 2
    assert [(call.name, call.arguments) for call in reply.tool_calls] == [
        ("refine_workshop_slots", {"serviceTypeName": "tyre"})
    ]
    assert reply.review and reply.review.outcome == "correct"


@pytest.mark.asyncio
async def test_unknown_planner_function_is_rejected() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=function_call("delete_everything", {}))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        with pytest.raises(ValueError, match="unknown function"):
            await provider(http).generate_turn(messages("hello"))


@pytest.mark.asyncio
async def test_hosted_connect_timeout_is_reported_as_a_turn_timeout() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("provider unavailable", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        with pytest.raises(TimeoutError, match="planner request timed out"):
            await provider(http).generate_turn(messages("Show me current offers"))


def test_internal_tool_messages_convert_to_responses_items() -> None:
    items = response_input(
        [
            {
                "role": "assistant",
                "tool_calls": [{"id": "one", "name": "list_offers", "arguments": {}}],
            },
            {"role": "tool", "tool_call_id": "one", "content": '{"items":[]}'},
        ]
    )

    assert items == [
        {"type": "function_call", "call_id": "one", "name": "list_offers", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "one", "output": '{"items":[]}'},
    ]


def test_provider_url_is_normalized_without_vendor_rewriting() -> None:
    assert _provider_base_url("https://gateway.example/custom/v1/") == (
        "https://gateway.example/custom/v1/"
    )


@pytest.mark.parametrize("value", ["", "relative", "ftp://provider.example", "https://x/y?q=1"])
def test_provider_url_must_be_absolute_http_base(value: str) -> None:
    with pytest.raises(ValueError):
        _provider_base_url(value)
