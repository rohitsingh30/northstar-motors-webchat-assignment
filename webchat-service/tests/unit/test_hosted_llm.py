import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, get_args

import httpx
import pytest

from webchat.domain.capabilities import CapabilityRegistry
from webchat.domain.interactions import ActionHandoff
from webchat.integrations.contracts import (
    PlanningContext,
    PlanningValidationError,
    ProviderUnavailableError,
    SemanticMessages,
    ToolCall,
    TurnProposal,
)
from webchat.integrations.hosted_llm import HostedLlmProvider, response_input
from webchat.integrations.hosted_llm.candidates import _retrieval_query, _retrieve_candidates
from webchat.integrations.hosted_llm.provider import (
    _bind_resolved_reference_arguments,
    _compiled_conversation_control,
    _continued_open_question_reply,
    _outcome_disambiguation_confirms_selection,
    _protected_edit_field,
    _provider_base_url,
    _remaining_compound_candidates,
    _semantic_candidates,
    _strict_function_schema,
    _unique_affordance_fallback,
    _validate_plan_against_understanding,
)
from webchat.integrations.hosted_llm.turn_resolution import (
    _normalise_resolution_arguments,
    input_resolution_catalogue,
    input_resolution_contracts,
    intent_resolution_catalogue,
    requires_explicit_outcome_disambiguation,
    resolution_request,
    resolution_tool_definition,
    trusted_context_references,
    validate_turn_understanding,
)
from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.contracts.plan import IntentKind
from webchat.orchestration.contracts.response import GroundedResponseDraft
from webchat.orchestration.contracts.semantics import (
    RequestedInputChange,
    ResolvedInput,
    TurnUnderstanding,
)
from webchat.orchestration.intent_contracts import primary_intent, supported_intents
from webchat.orchestration.planning.affordances import PlannerAffordanceEvaluator
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
                    ActionHandoff(topic="part_exchange"),
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
        semantic_resolution=False,
    )


def test_grounded_response_provider_schema_meets_strict_function_rules() -> None:
    schema = _strict_function_schema(GroundedResponseDraft.model_json_schema())

    def assert_strict(value: Any) -> None:
        if isinstance(value, dict):
            assert "default" not in value
            assert "oneOf" not in value
            assert "discriminator" not in value
            if value.get("type") == "object" and isinstance(value.get("properties"), dict):
                assert value["additionalProperties"] is False
                assert set(value["required"]) == set(value["properties"])
            for child in value.values():
                assert_strict(child)
        elif isinstance(value, list):
            for child in value:
                assert_strict(child)

    assert_strict(schema)


def test_resolution_schema_cannot_answer_a_question_that_is_not_open() -> None:
    schema = resolution_tool_definition([], [], [], [])
    properties = schema["parameters"]["properties"]

    assert "answer_open_question" not in properties["dialogueAct"]["enum"]
    assert "open_question" not in properties["goalRelation"]["enum"]
    assert properties["resultPresentation"]["enum"] == ["default", "explicit_request"]
    assert "resultPresentation" in schema["parameters"]["required"]
    assert properties["answeredQuestionId"]["enum"] == [None]
    assert "requestedInputChanges" in schema["parameters"]["required"]


def test_explicit_outcome_disambiguation_cannot_repeat_an_open_question_answer() -> None:
    context = PlanningContext(
        latest_customer_message="What does the MOT cost?",
        pending_interaction={
            "kind": "reference_choice",
            "question_id": "question-service",
            "goal_intent": "workshop_booking",
            "candidate_references": ["service:mot"],
            "expected_fields": ["serviceTypeId"],
        },
        displayed_choices=[
            {"entityReference": "service:mot", "label": "MOT", "position": 1}
        ],
    )

    request = resolution_request(
        model="hosted-model",
        context=context,
        input_fields={},
        input_catalogue=[],
        intent_catalogue=[],
        outcome_disambiguation=True,
    )
    schema = request["tools"][0]["parameters"]["properties"]

    assert "answer_open_question" not in schema["dialogueAct"]["enum"]
    assert "open_question" not in schema["goalRelation"]["enum"]
    assert schema["answeredQuestionId"]["enum"] == [None]
    assert "mere presence of a candidate label" in request["instructions"]


def test_outcome_disambiguation_confirms_only_the_same_goal_and_reference() -> None:
    initial = TurnUnderstanding(
        dialogueAct="answer_open_question",
        goalRelation="open_question",
        intentKinds=["workshop_booking"],
        answeredQuestionId="question-service",
        resolvedReferences=["service:mot"],
        confidence="high",
    )
    same_selection = initial.model_copy(
        update={
            "dialogueAct": "continue_goal",
            "goalRelation": "active",
            "answeredQuestionId": None,
        }
    )
    corrected_selection = same_selection.model_copy(
        update={"resolvedReferences": ["service:full-service"]}
    )
    information_request = same_selection.model_copy(
        update={
            "dialogueAct": "interrupt_with_information_request",
            "goalRelation": "unrelated",
            "intentKinds": ["service_detail"],
        }
    )

    assert _outcome_disambiguation_confirms_selection(
        initial,
        same_selection,
        "workshop_booking",
    )
    assert not _outcome_disambiguation_confirms_selection(
        initial,
        corrected_selection,
        "workshop_booking",
    )
    assert not _outcome_disambiguation_confirms_selection(
        initial,
        information_request,
        "workshop_booking",
    )


@pytest.mark.parametrize(
    (
        "active_workflow",
        "goal_intent",
        "expected_field",
        "reference",
        "label",
        "natural_message",
    ),
    [
        (
            "workshop_booking",
            "workshop_booking",
            "serviceTypeId",
            "service:mot",
            "MOT",
            "What does the MOT cost?",
        ),
        (
            "callback",
            "callback",
            "dealershipId",
            "dealership:northstar-bolton",
            "Bolton",
            "When does Bolton close?",
        ),
        (
            "test_drive",
            "test_drive",
            "vehicleId",
            "vehicle:veh-001",
            "BMW 1 Series",
            "What does the BMW 1 Series cost?",
        ),
        (
            "workshop_amend",
            "booking_amendment",
            "slotId",
            "appointment:slot-1",
            "Tuesday at 10:00",
            "Where is Tuesday at 10:00?",
        ),
        (
            "dealership_message",
            "dealership_message",
            "department",
            "workflow_option:parts",
            "Parts",
            "What does the Parts team handle?",
        ),
    ],
)
def test_natural_sentence_consuming_any_finite_choice_requires_outcome_disambiguation(
    active_workflow: str,
    goal_intent: str,
    expected_field: str,
    reference: str,
    label: str,
    natural_message: str,
) -> None:
    understanding = TurnUnderstanding(
        dialogueAct="answer_open_question",
        goalRelation="open_question",
        intentStructure="single_outcome",
        intentKinds=[goal_intent],
        answeredQuestionId="question-choice",
        resolvedReferences=[reference],
        ambiguity="none",
        confidence="high",
    )

    def context(message: str) -> PlanningContext:
        return PlanningContext(
            latest_customer_message=message,
            workflow_state={"activeWorkflow": active_workflow},
            pending_interaction={
                "kind": "reference_choice",
                "question_id": "question-choice",
                "goal_intent": goal_intent,
                "candidate_references": [reference],
                "expected_fields": [expected_field],
            },
            displayed_choices=[
                {"entityReference": reference, "label": label, "position": 1}
            ],
        )

    assert (
        requires_explicit_outcome_disambiguation(understanding, context(natural_message)) is True
    )
    assert requires_explicit_outcome_disambiguation(understanding, context(label)) is False
    assert requires_explicit_outcome_disambiguation(understanding, context("option 1")) is False


def test_semantic_review_change_accepts_natural_typo_with_customer_evidence() -> None:
    context = PlanningContext(
        latest_customer_message="change me email",
        pending_interaction={
            "version": 1,
            "kind": "protected_confirmation",
            "draft_id": "draft-review-1",
            "workflow_kind": "test_drive",
        },
        context_evidence=[
            {
                "contextId": "message:correction",
                "role": "user",
                "text": "change me email",
            }
        ],
    )
    understanding = validate_turn_understanding(
        {
            "schemaVersion": 1,
            "dialogueAct": "modify_goal",
            "goalRelation": "active",
            "intentStructure": "single_outcome",
            "intentKinds": ["test_drive"],
            "resultPresentation": "default",
            "answeredQuestionId": None,
            "resolvedReferences": [],
            "referenceCandidates": [],
            "resolvedInputs": [],
            "requestedInputChanges": [
                {
                    "field": "email",
                    "sourceContextId": "message:correction",
                    "sourceText": "email",
                }
            ],
            "ambiguity": "none",
            "confidence": "high",
            "supportingContextIds": [],
        },
        context,
        {"email"},
    )

    assert understanding.requestedInputChanges == [
        RequestedInputChange(
            field="email",
            sourceContextId="message:correction",
            sourceText="email",
        )
    ]
    assert _protected_edit_field(context, understanding) == "email"


def test_fieldless_goal_reaffirmation_reuses_the_application_owned_question() -> None:
    context = PlanningContext(
        latest_customer_message="book the test drive",
        pending_interaction={
            "version": 1,
            "kind": "input",
            "question_id": "question-schedule",
            "prompt": "What day or date and approximate time would suit you?",
            "goal_intent": "test_drive",
            "candidate_references": [],
            "candidate_intents": [],
            "expected_fields": ["dateFrom", "dateTo", "timeOfDay"],
        },
    )
    understanding = TurnUnderstanding(
        dialogueAct="continue_goal",
        goalRelation="active",
        intentKinds=["test_drive"],
        confidence="medium",
    )

    reply = _continued_open_question_reply(context, understanding)

    assert reply is not None
    assert reply.text == "What day or date and approximate time would suit you?"
    assert reply.turn_understanding == understanding

    with_value = understanding.model_copy(
        update={
            "resolvedInputs": [
                ResolvedInput(
                    field="timeOfDay",
                    value="morning",
                    sourceContextId="message:customer-1",
                    sourceText="morning",
                )
            ]
        }
    )
    assert _continued_open_question_reply(context, with_value) is None


def test_resolution_schema_exposes_the_persisted_open_question() -> None:
    schema = resolution_tool_definition(
        [],
        ["question-service-scope"],
        [],
        [],
    )
    properties = schema["parameters"]["properties"]

    assert "answer_open_question" in properties["dialogueAct"]["enum"]
    assert "open_question" in properties["goalRelation"]["enum"]
    assert properties["answeredQuestionId"]["enum"] == [None, "question-service-scope"]


def test_resolution_input_values_use_executable_field_schemas() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    contracts = input_resolution_contracts(catalogue)
    schema = resolution_tool_definition([], [], contracts, ["message:customer-1"])
    variants = schema["parameters"]["properties"]["resolvedInputs"]["items"]["anyOf"]
    by_field = {variant["properties"]["field"]["enum"][0]: variant for variant in variants}

    assert by_field["make"]["properties"]["value"] == {"maxLength": 60, "type": "string"}
    assert by_field["makes"]["properties"]["value"]["type"] == "array"
    assert by_field["maxPricePence"]["properties"]["value"]["type"] == "integer"


def test_semantic_inputs_require_customer_authored_source_evidence() -> None:
    context = PlanningContext(
        latest_customer_message="tomorrow morning",
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "kind": "conversation_turn",
                "relation": "recent_supporting_evidence",
                "relevance": 0.8,
                "role": "user",
                "text": "tomorrow morning",
            }
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "continue_goal",
        "goalRelation": "active",
        "intentStructure": "single_outcome",
        "intentKinds": ["test_drive"],
        "answeredQuestionId": None,
        "resolvedReferences": [],
        "referenceCandidates": [],
        "resolvedInputs": [
            {
                "field": "timeOfDay",
                "value": "morning",
                "sourceContextId": "message:customer-1",
                "sourceText": "morning",
            }
        ],
        "ambiguity": "none",
        "confidence": "high",
        "supportingContextIds": ["message:customer-1"],
    }

    result = validate_turn_understanding(payload, context, {"timeOfDay"})
    assert result.resolvedInputs[0].value == "morning"

    payload["resolvedInputs"][0]["sourceText"] = "afternoon"
    with pytest.raises(ValueError, match="customer-authored evidence"):
        validate_turn_understanding(payload, context, {"timeOfDay"})


def test_repeated_vehicle_filter_inputs_are_normalised_to_one_multi_value_field() -> None:
    context = PlanningContext(
        latest_customer_message="what good in bmw and volvo",
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "kind": "conversation_turn",
                "relation": "recent_supporting_evidence",
                "relevance": 1.0,
                "role": "user",
                "text": "what good in bmw and volvo",
            }
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "start_goal",
        "goalRelation": "new",
        "intentStructure": "compound_outcomes",
        "intentKinds": ["vehicle_search", "vehicle_search"],
        "answeredQuestionId": None,
        "resolvedReferences": [],
        "referenceCandidates": [],
        "resolvedInputs": [
            {
                "field": "make",
                "value": "BMW",
                "sourceContextId": "message:customer-1",
                "sourceText": "bmw",
            },
            {
                "field": "make",
                "value": "Volvo",
                "sourceContextId": "message:customer-1",
                "sourceText": "volvo",
            },
        ],
        "ambiguity": "none",
        "confidence": "high",
        "supportingContextIds": ["message:customer-1", "message:customer-1"],
    }

    understanding = validate_turn_understanding(payload, context, {"make", "makes"})

    assert understanding.intentKinds == ["vehicle_search"]
    assert understanding.intentStructure == "single_outcome"
    assert understanding.resolvedInputs[0].field == "make"
    assert understanding.resolvedInputs[0].value == ["BMW", "Volvo"]
    assert understanding.resolvedInputs[0].sourceText == "what good in bmw and volvo"


def test_open_schedule_answer_is_validated_against_executable_input_fields() -> None:
    context = PlanningContext(
        latest_customer_message="tomorrow morning",
        workflow_state={
            "entities": {
                "vehicleId": "veh-027",
                "dealershipId": "northstar-liverpool",
            }
        },
        pending_interaction={
            "kind": "input",
            "question_id": "question-schedule",
            "goal_intent": "test_drive",
            "candidate_references": [],
            "expected_fields": ["dateFrom", "dateTo", "timeOfDay"],
        },
        context_evidence=[
            {
                "contextId": "question-schedule",
                "kind": "open_question",
                "relation": "expects_latest_answer",
                "relevance": 1.0,
            },
            {
                "contextId": "message:customer-1",
                "kind": "conversation_turn",
                "relation": "recent_supporting_evidence",
                "relevance": 0.8,
                "role": "user",
                "text": "tomorrow morning",
            },
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "answer_open_question",
        "goalRelation": "open_question",
        "intentStructure": "single_outcome",
        "intentKinds": ["test_drive"],
        "answeredQuestionId": "question-schedule",
        "resolvedReferences": [],
        "referenceCandidates": [],
        "resolvedInputs": [
            {
                "field": "dateFrom",
                "value": "2026-09-06",
                "sourceContextId": "message:customer-1",
                "sourceText": "tomorrow",
            },
            {
                "field": "timeOfDay",
                "value": "morning",
                "sourceContextId": "message:customer-1",
                "sourceText": "morning",
            },
        ],
        "ambiguity": "none",
        "confidence": "high",
        "supportingContextIds": ["question-schedule", "message:customer-1"],
    }

    result = validate_turn_understanding(
        payload,
        context,
        {"dateFrom", "dateTo", "timeOfDay"},
    )
    assert {item.field for item in result.resolvedInputs} == {"dateFrom", "timeOfDay"}

    payload["resolvedInputs"] = []
    payload["resolvedReferences"] = [
        "vehicle:veh-027",
        "dealership:northstar-liverpool",
    ]
    payload["ambiguity"] = "required_input"
    payload["confidence"] = "medium"
    reaffirmed = validate_turn_understanding(
        payload,
        context,
        {"dateFrom", "dateTo", "timeOfDay"},
    )
    assert reaffirmed.dialogueAct == "continue_goal"
    assert reaffirmed.goalRelation == "active"
    assert reaffirmed.answeredQuestionId is None
    assert reaffirmed.resolvedReferences == []
    assert reaffirmed.ambiguity == "none"


def test_goal_reaffirmation_does_not_discard_a_different_entity_reference() -> None:
    context = PlanningContext(
        latest_customer_message="book the other one",
        workflow_state={"entities": {"vehicleId": "veh-027"}},
        pending_interaction={
            "kind": "input",
            "question_id": "question-schedule",
            "goal_intent": "test_drive",
            "candidate_references": [],
            "expected_fields": ["dateFrom", "dateTo", "timeOfDay"],
        },
    )
    payload = {
        "dialogueAct": "answer_open_question",
        "goalRelation": "open_question",
        "intentKinds": ["test_drive"],
        "answeredQuestionId": "question-schedule",
        "resolvedReferences": ["vehicle:veh-999"],
        "resolvedInputs": [],
        "ambiguity": "required_input",
    }

    normalised = _normalise_resolution_arguments(payload, context)

    assert normalised["dialogueAct"] == "answer_open_question"
    assert normalised["resolvedReferences"] == ["vehicle:veh-999"]


@pytest.mark.parametrize(
    "intent_kind",
    sorted(set(CapabilityRegistry.SPECS).intersection(get_args(IntentKind))),
)
def test_missing_workflow_fields_do_not_make_a_resolved_entry_goal_ambiguous(
    intent_kind: str,
) -> None:
    payload = {
        "dialogueAct": "start_goal",
        "goalRelation": "new",
        "intentKinds": [intent_kind],
        "resolvedInputs": [],
        "ambiguity": "required_input",
        "confidence": "medium",
    }

    normalised = _normalise_resolution_arguments(
        payload,
        PlanningContext(latest_customer_message="leave a message for stockport"),
    )

    assert normalised["ambiguity"] == "none"


def test_resolved_dealership_town_keeps_message_workflow_executable() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    context = PlanningContext(
        latest_customer_message="leave a message for stockport",
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "kind": "conversation_turn",
                "relation": "latest_customer_message",
                "relevance": 1.0,
                "role": "user",
                "text": "leave a message for stockport",
            }
        ],
    )
    understanding = validate_turn_understanding(
        {
            "schemaVersion": 1,
            "dialogueAct": "start_goal",
            "goalRelation": "new",
            "intentStructure": "single_outcome",
            "intentKinds": ["dealership_message"],
            "resultPresentation": "default",
            "answeredQuestionId": None,
            "resolvedReferences": [],
            "referenceCandidates": [],
            "resolvedInputs": [
                {
                    "field": "dealershipTown",
                    "value": "Stockport",
                    "sourceContextId": "message:customer-1",
                    "sourceText": "stockport",
                }
            ],
            "ambiguity": "required_input",
            "confidence": "medium",
            "supportingContextIds": ["message:customer-1"],
        },
        context,
        input_resolution_contracts(catalogue),
    )
    context = replace(context, turn_understanding=understanding)

    affordances = PlannerAffordanceEvaluator(catalogue).evaluate(
        CandidateSet((catalogue.get("prepare_dealership_message"),), ()),
        context,
    )

    assert understanding.ambiguity == "none"
    assert affordances.executable_tool_ids == {"prepare_dealership_message"}
    assert affordances.blocked_tool_ids == set()
    assert affordances.allow_generic_clarification is False


def test_missing_read_reference_remains_required_input_ambiguity() -> None:
    payload = {
        "dialogueAct": "start_goal",
        "goalRelation": "new",
        "intentKinds": ["vehicle_detail"],
        "resolvedInputs": [],
        "ambiguity": "required_input",
        "confidence": "medium",
    }

    normalised = _normalise_resolution_arguments(
        payload,
        PlanningContext(latest_customer_message="tell me about a vehicle"),
    )

    assert normalised["ambiguity"] == "required_input"


@pytest.mark.parametrize(
    "intent_kind",
    sorted(set(CapabilityRegistry.SPECS).intersection(get_args(IntentKind))),
)
def test_every_workflow_entry_has_an_operation_or_clarification(
    intent_kind: str,
) -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    spec = CapabilityRegistry.get(intent_kind)
    arguments = _normalise_resolution_arguments(
        {
            "schemaVersion": 1,
            "dialogueAct": "start_goal",
            "goalRelation": "new",
            "intentStructure": "single_outcome",
            "intentKinds": [intent_kind],
            "resultPresentation": "default",
            "answeredQuestionId": None,
            "resolvedReferences": [],
            "referenceCandidates": [],
            "resolvedInputs": [],
            "ambiguity": "required_input",
            "confidence": "medium",
            "supportingContextIds": [],
        },
        PlanningContext(latest_customer_message="start this request"),
    )
    understanding = TurnUnderstanding.model_validate(arguments)

    affordances = PlannerAffordanceEvaluator(catalogue).evaluate(
        CandidateSet((catalogue.get(spec.agent_tool),), ()),
        PlanningContext(
            latest_customer_message="start this request",
            turn_understanding=understanding,
        ),
    )

    assert affordances.executable_tool_ids or affordances.allow_generic_clarification


def test_semantic_intent_ontology_is_derived_from_capability_catalogue() -> None:
    entries = {
        item["intentKind"]: item
        for item in intent_resolution_catalogue(UnifiedToolCatalog(NeverExecute()))
    }

    test_drive = entries["test_drive"]["capabilities"]
    vehicle_interest = entries["vehicle_interest"]["capabilities"]
    business_information = entries["business_information"]["capabilities"]
    sales_enquiry = entries["sales_enquiry"]["capabilities"]
    assert any("test-drive" in item["title"].casefold() for item in test_drive)
    assert any("reserved vehicle" in item["title"].casefold() for item in vehicle_interest)
    assert any("remain read-only" in item["purpose"].casefold() for item in business_information)
    assert any("not permission" in item["purpose"].casefold() for item in sales_enquiry)
    assert test_drive != vehicle_interest
    assert business_information != sales_enquiry


def test_vehicle_query_and_ranking_meanings_are_distinct_in_the_semantic_catalogue() -> None:
    entries = {
        item["field"]: item
        for item in input_resolution_catalogue(UnifiedToolCatalog(NeverExecute()))
    }

    query_description = " ".join(entries["q"]["descriptions"]).casefold()
    sort_description = " ".join(entries["sort"]["descriptions"]).casefold()
    assert "vehicle identity" in query_description
    assert "do not use as a catch-all" in query_description
    assert "mileageasc by lowest mileage" in sort_description


def test_validated_intents_compile_every_declared_operation_without_relying_on_retrieval() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    planner_tools = catalogue.planner_tools()
    declared_intents = sorted(
        {
            intent
            for definition in planner_tools
            for intent in supported_intents(definition.id)
            if intent != "capability"
        }
    )

    for intent in declared_intents:
        context = PlanningContext(
            latest_customer_message="natural customer wording",
            turn_understanding=TurnUnderstanding(
                dialogueAct="start_goal",
                goalRelation="new",
                intentKinds=[intent],
                confidence="high",
            ),
        )
        compiled = _semantic_candidates(CandidateSet((), ()), context, catalogue)
        expected = {
            definition.id
            for definition in planner_tools
            if intent in supported_intents(definition.id)
        }

        assert {definition.id for definition in compiled.tools} == expected


def test_invalid_planner_output_can_fall_back_only_to_one_semantically_unique_operation() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    definition = catalogue.get("get_service_information")
    context = PlanningContext(
        latest_customer_message="Do you offer tyre fitting?",
        turn_understanding=TurnUnderstanding(
            dialogueAct="start_goal",
            goalRelation="new",
            intentKinds=["service_detail"],
            resolvedInputs=[
                ResolvedInput(
                    field="serviceTypeName",
                    value="tyre fitting",
                    sourceContextId="message:customer-1",
                    sourceText="tyre fitting",
                )
            ],
            confidence="high",
        ),
    )
    affordances = SimpleNamespace(
        candidates=CandidateSet((definition,), ()),
        executable_tool_ids={definition.id},
    )

    fallback = _unique_affordance_fallback(affordances, context)

    assert fallback is not None
    assert [(call.name, call.intent_kind, call.arguments) for call in fallback.tool_calls] == [
        ("get_service_information", "service_detail", {})
    ]

    ambiguous = SimpleNamespace(
        candidates=CandidateSet(
            (definition, catalogue.get("get_business_information")),
            (),
        ),
        executable_tool_ids={definition.id, "get_business_information"},
    )
    compound = context.turn_understanding.model_copy(
        update={"intentKinds": ["service_detail", "business_information"]}
    )
    assert (
        _unique_affordance_fallback(
            ambiguous,
            replace(context, turn_understanding=compound),
        )
        is not None
    )


def test_turn_understanding_distinguishes_compound_requests_from_alternatives() -> None:
    base = {
        "schemaVersion": 1,
        "dialogueAct": "start_goal",
        "goalRelation": "new",
        "intentKinds": ["vehicle_search", "opening_hours"],
        "answeredQuestionId": None,
        "resolvedReferences": [],
        "referenceCandidates": [],
        "resolvedInputs": [],
        "confidence": "high",
        "supportingContextIds": [],
    }

    compound = TurnUnderstanding.model_validate(
        {**base, "intentStructure": "compound_outcomes", "ambiguity": "none"}
    )
    assert compound.intentStructure == "compound_outcomes"
    with pytest.raises(ValueError, match="single intent structure"):
        TurnUnderstanding.model_validate(
            {**base, "intentStructure": "single_outcome", "ambiguity": "none"}
        )
    alternatives = TurnUnderstanding.model_validate(
        {
            **base,
            "intentStructure": "uncertain_intent",
            "ambiguity": "intent",
            "confidence": "medium",
        }
    )
    assert alternatives.ambiguity == "intent"


def test_entity_identifier_cannot_be_claimed_as_customer_authored_input() -> None:
    context = PlanningContext(
        latest_customer_message="book the BMW 1 Series",
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "kind": "conversation_turn",
                "relation": "recent_supporting_evidence",
                "relevance": 0.8,
                "role": "user",
                "text": "book the BMW 1 Series",
            }
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "start_goal",
        "goalRelation": "new",
        "intentStructure": "single_outcome",
        "intentKinds": ["test_drive"],
        "answeredQuestionId": None,
        "resolvedReferences": [],
        "referenceCandidates": [],
        "resolvedInputs": [
            {
                "field": "vehicleId",
                "value": "veh-049",
                "sourceContextId": "message:customer-1",
                "sourceText": "BMW 1 Series",
            }
        ],
        "ambiguity": "none",
        "confidence": "high",
        "supportingContextIds": ["message:customer-1"],
    }

    with pytest.raises(ValueError, match="trusted references"):
        validate_turn_understanding(payload, context, {"vehicleId"})


def test_reference_resolution_keeps_only_the_unique_descriptor_match() -> None:
    context = PlanningContext(
        latest_customer_message="book a test drive for MINI",
        displayed_vehicles=[
            {"position": 1, "vehicleId": "veh-001", "make": "MINI", "model": "Countryman"},
            {"position": 2, "vehicleId": "veh-002", "make": "BMW", "model": "1 Series"},
        ],
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "kind": "conversation_turn",
                "relation": "recent_supporting_evidence",
                "relevance": 0.8,
                "role": "user",
                "text": "book a test drive for MINI",
            }
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "start_goal",
        "goalRelation": "new",
        "intentStructure": "single_outcome",
        "intentKinds": ["test_drive"],
        "answeredQuestionId": None,
        "resolvedReferences": [],
        "referenceCandidates": ["vehicle:veh-001", "vehicle:veh-002"],
        "resolvedInputs": [
            {
                "field": "make",
                "value": "MINI",
                "sourceContextId": "message:customer-1",
                "sourceText": "MINI",
            }
        ],
        "ambiguity": "reference",
        "confidence": "medium",
        "supportingContextIds": ["message:customer-1"],
    }

    resolved = validate_turn_understanding(payload, context, {"make"})

    assert resolved.resolvedReferences == ["vehicle:veh-001"]
    assert resolved.referenceCandidates == []
    assert resolved.ambiguity == "none"


def test_reference_resolution_uses_numeric_ranges_to_disambiguate_entities() -> None:
    context = PlanningContext(
        latest_customer_message="Is the 2025 red BMW 1 Series available?",
        displayed_vehicles=[
            {
                "position": 1,
                "vehicleId": "veh-013",
                "make": "BMW",
                "model": "1 Series",
                "year": 2025,
                "colour": "Fire Red",
            },
            {
                "position": 2,
                "vehicleId": "veh-049",
                "make": "BMW",
                "model": "1 Series",
                "year": 2026,
                "colour": "Fire Red",
            },
        ],
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "kind": "conversation_turn",
                "relation": "recent_supporting_evidence",
                "relevance": 0.8,
                "role": "user",
                "text": "Is the 2025 red BMW 1 Series available?",
            }
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "start_goal",
        "goalRelation": "new",
        "intentStructure": "single_outcome",
        "intentKinds": ["vehicle_availability"],
        "answeredQuestionId": None,
        "resolvedReferences": [],
        "referenceCandidates": ["vehicle:veh-013", "vehicle:veh-049"],
        "resolvedInputs": [
            {
                "field": field,
                "value": value,
                "sourceContextId": "message:customer-1",
                "sourceText": source,
            }
            for field, value, source in (
                ("make", "BMW", "BMW"),
                ("model", "1 Series", "1 Series"),
                ("colour", "red", "red"),
                ("minYear", 2025, "2025"),
                ("maxYear", 2025, "2025"),
            )
        ],
        "ambiguity": "reference",
        "confidence": "medium",
        "supportingContextIds": ["message:customer-1"],
    }

    resolved = validate_turn_understanding(
        payload,
        context,
        {"make", "model", "colour", "minYear", "maxYear"},
    )

    assert resolved.resolvedReferences == ["vehicle:veh-013"]
    assert resolved.referenceCandidates == []
    assert resolved.ambiguity == "none"


def test_reference_resolution_repairs_a_contradictory_selection_from_trusted_context() -> None:
    context = PlanningContext(
        latest_customer_message="book a test drive for MINI",
        displayed_vehicles=[
            {"position": 1, "vehicleId": "veh-001", "make": "MINI", "model": "Countryman"},
            {"position": 2, "vehicleId": "veh-002", "make": "BMW", "model": "1 Series"},
        ],
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "kind": "conversation_turn",
                "relation": "recent_supporting_evidence",
                "relevance": 0.8,
                "role": "user",
                "text": "book a test drive for MINI",
            }
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "start_goal",
        "goalRelation": "new",
        "intentStructure": "single_outcome",
        "intentKinds": ["test_drive"],
        "answeredQuestionId": None,
        "resolvedReferences": ["vehicle:veh-002"],
        "referenceCandidates": [],
        "resolvedInputs": [
            {
                "field": "make",
                "value": "MINI",
                "sourceContextId": "message:customer-1",
                "sourceText": "MINI",
            }
        ],
        "ambiguity": "none",
        "confidence": "high",
        "supportingContextIds": ["message:customer-1"],
    }

    resolved = validate_turn_understanding(payload, context, {"make"})

    assert resolved.resolvedReferences == ["vehicle:veh-001"]
    assert resolved.referenceCandidates == []
    assert resolved.ambiguity == "none"


def test_descriptor_for_undisplayed_entity_drops_contradictory_display_reference() -> None:
    context = PlanningContext(
        latest_customer_message="book a test drive for MINI",
        displayed_vehicles=[
            {"position": 1, "vehicleId": "veh-001", "make": "BMW", "model": "1 Series"},
            {"position": 2, "vehicleId": "veh-002", "make": "BMW", "model": "3 Series"},
        ],
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "kind": "conversation_turn",
                "relation": "recent_supporting_evidence",
                "relevance": 0.8,
                "role": "user",
                "text": "book a test drive for MINI",
            }
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "start_goal",
        "goalRelation": "new",
        "intentStructure": "single_outcome",
        "intentKinds": ["test_drive"],
        "answeredQuestionId": None,
        "resolvedReferences": ["vehicle:veh-001"],
        "referenceCandidates": [],
        "resolvedInputs": [
            {
                "field": "make",
                "value": "MINI",
                "sourceContextId": "message:customer-1",
                "sourceText": "MINI",
            }
        ],
        "ambiguity": "none",
        "confidence": "high",
        "supportingContextIds": ["message:customer-1"],
    }

    resolved = validate_turn_understanding(payload, context, {"make"})

    assert resolved.resolvedReferences == []
    assert resolved.referenceCandidates == []
    assert resolved.resolvedInputs[0].value == "MINI"
    assert resolved.ambiguity == "none"


def test_reference_resolution_retains_all_matching_candidates_when_descriptor_is_ambiguous() -> (
    None
):
    context = PlanningContext(
        latest_customer_message="book a test drive for MINI",
        displayed_vehicles=[
            {"position": 1, "vehicleId": "veh-001", "make": "MINI", "model": "Countryman"},
            {"position": 2, "vehicleId": "veh-002", "make": "BMW", "model": "1 Series"},
            {"position": 3, "vehicleId": "veh-003", "make": "MINI", "model": "Cooper"},
        ],
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "kind": "conversation_turn",
                "relation": "recent_supporting_evidence",
                "relevance": 0.8,
                "role": "user",
                "text": "book a test drive for MINI",
            }
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "start_goal",
        "goalRelation": "new",
        "intentStructure": "single_outcome",
        "intentKinds": ["test_drive"],
        "answeredQuestionId": None,
        "resolvedReferences": ["vehicle:veh-001"],
        "referenceCandidates": ["vehicle:veh-001", "vehicle:veh-002"],
        "resolvedInputs": [
            {
                "field": "make",
                "value": "MINI",
                "sourceContextId": "message:customer-1",
                "sourceText": "MINI",
            }
        ],
        "ambiguity": "reference",
        "confidence": "medium",
        "supportingContextIds": ["message:customer-1"],
    }

    resolved = validate_turn_understanding(payload, context, {"make"})

    assert resolved.resolvedReferences == []
    assert resolved.referenceCandidates == ["vehicle:veh-001", "vehicle:veh-003"]
    assert resolved.ambiguity == "reference"


def test_reference_resolution_repairs_each_namespace_in_a_compound_turn() -> None:
    context = PlanningContext(
        latest_customer_message="Book the MINI and show me its PCH offer",
        displayed_vehicles=[
            {"vehicleId": "veh-001", "make": "MINI", "model": "Countryman"},
            {"vehicleId": "veh-002", "make": "BMW", "model": "1 Series"},
        ],
        displayed_offers=[
            {"offerId": "offer-01", "make": "BMW", "productType": "PCP"},
            {"offerId": "offer-02", "make": "MINI", "productType": "PCH"},
        ],
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "kind": "conversation_turn",
                "relation": "recent_supporting_evidence",
                "relevance": 1.0,
                "role": "user",
                "text": "Book the MINI and show me its PCH offer",
            }
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "start_goal",
        "goalRelation": "new",
        "intentStructure": "compound_outcomes",
        "intentKinds": ["test_drive", "offer_discovery"],
        "answeredQuestionId": None,
        "resolvedReferences": ["vehicle:veh-002", "offer:offer-01"],
        "referenceCandidates": [],
        "resolvedInputs": [
            {
                "field": "make",
                "value": "MINI",
                "sourceContextId": "message:customer-1",
                "sourceText": "MINI",
            },
            {
                "field": "productType",
                "value": "PCH",
                "sourceContextId": "message:customer-1",
                "sourceText": "PCH",
            },
        ],
        "ambiguity": "none",
        "confidence": "high",
        "supportingContextIds": ["message:customer-1"],
    }

    resolved = validate_turn_understanding(payload, context, {"make", "productType"})

    assert resolved.intentStructure == "compound_outcomes"
    assert resolved.intentKinds == ["test_drive", "offer_discovery"]
    assert resolved.resolvedReferences == ["vehicle:veh-001", "offer:offer-02"]
    assert resolved.referenceCandidates == []


def test_reference_resolution_repairs_a_service_answer_from_its_open_choice() -> None:
    context = PlanningContext(
        latest_customer_message="break",
        workflow_state={
            "activeWorkflow": "workshop_booking",
            "stage": "choosing_service",
            "entities": {},
            "constraints": {"missingPublicFields": ["serviceTypeId"]},
        },
        pending_interaction={
            "kind": "reference_choice",
            "question_id": "question-service",
            "goal_intent": "workshop_booking",
            "candidate_references": [
                "service:brake-inspection",
                "service:diagnostic",
            ],
            "expected_fields": ["serviceTypeId"],
        },
        displayed_choices=[
            {
                "entityReference": "service:brake-inspection",
                "label": "Brake inspection",
            },
            {
                "entityReference": "service:diagnostic",
                "label": "Diagnostic inspection",
            },
        ],
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "kind": "conversation_turn",
                "relation": "answers_open_question",
                "relevance": 1.0,
                "role": "user",
                "text": "break",
            },
            {
                "contextId": "question-service",
                "kind": "open_question",
                "relation": "expects_latest_answer",
                "relevance": 1.0,
            },
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "answer_open_question",
        "goalRelation": "open_question",
        "intentStructure": "single_outcome",
        "intentKinds": ["workshop_booking"],
        "answeredQuestionId": "question-service",
        "resolvedReferences": ["service:diagnostic"],
        "referenceCandidates": [],
        "resolvedInputs": [
            {
                "field": "serviceTypeName",
                "value": "Brake inspection",
                "sourceContextId": "message:customer-1",
                "sourceText": "break",
            }
        ],
        "ambiguity": "none",
        "confidence": "high",
        "supportingContextIds": ["question-service", "message:customer-1"],
    }

    resolved = validate_turn_understanding(payload, context, {"serviceTypeName"})

    assert resolved.resolvedReferences == ["service:brake-inspection"]
    assert resolved.answeredQuestionId == "question-service"


def test_finite_choice_precedence_repairs_a_one_word_typo_misclassified_as_cancel() -> None:
    context = PlanningContext(
        latest_customer_message="break",
        workflow_state={"activeWorkflow": "workshop_booking"},
        pending_interaction={
            "kind": "reference_choice",
            "question_id": "question-service",
            "goal_intent": "workshop_booking",
            "candidate_references": [
                "service:brake-inspection",
                "service:diagnostic",
            ],
            "expected_fields": ["serviceTypeId"],
        },
        displayed_choices=[
            {
                "entityReference": "service:brake-inspection",
                "label": "Brake inspection",
            },
            {"entityReference": "service:diagnostic", "label": "Diagnostic inspection"},
        ],
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "kind": "conversation_turn",
                "relation": "answers_open_question",
                "relevance": 1.0,
                "role": "user",
                "text": "break",
            },
            {
                "contextId": "question-service",
                "kind": "open_question",
                "relation": "expects_latest_answer",
                "relevance": 1.0,
            },
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "cancel_goal",
        "goalRelation": "active",
        "intentStructure": "single_outcome",
        "intentKinds": ["capability"],
        "answeredQuestionId": None,
        "resolvedReferences": [],
        "referenceCandidates": [],
        "resolvedInputs": [],
        "ambiguity": "none",
        "confidence": "high",
        "supportingContextIds": ["message:customer-1"],
    }

    resolved = validate_turn_understanding(payload, context, set())

    assert resolved.dialogueAct == "answer_open_question"
    assert resolved.goalRelation == "open_question"
    assert resolved.intentKinds == ["workshop_booking"]
    assert resolved.answeredQuestionId == "question-service"
    assert resolved.resolvedReferences == ["service:brake-inspection"]


@pytest.mark.parametrize(
    "customer_text",
    [
        "show me option 2",
        "option2",
        "the second one",
        "choice number two",
        "option 2nd please",
        "#2",
    ],
)
@pytest.mark.parametrize(
    "namespace",
    ["vehicle", "offer", "dealership", "service", "appointment", "workflow_option"],
)
def test_finite_choice_position_selects_the_persisted_reference(
    customer_text: str,
    namespace: str,
) -> None:
    references = [f"{namespace}:option-{position}" for position in range(1, 4)]
    context = PlanningContext(
        latest_customer_message=customer_text,
        workflow_state={"activeWorkflow": "workshop_booking", "stage": "choosing_option"},
        pending_interaction={
            "kind": "reference_choice",
            "question_id": "question-vehicle",
            "goal_intent": "workshop_booking",
            "candidate_references": references,
            "expected_fields": [],
        },
        displayed_choices=[
            {"position": position, "entityReference": reference, "label": "Same label"}
            for position, reference in enumerate(references, start=1)
        ],
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "kind": "conversation_turn",
                "relation": "answers_open_question",
                "relevance": 1.0,
                "role": "user",
                "text": customer_text,
            },
            {
                "contextId": "question-vehicle",
                "kind": "open_question",
                "relation": "expects_latest_answer",
                "relevance": 1.0,
            },
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "continue_goal",
        "goalRelation": "active",
        "intentStructure": "single_outcome",
        "intentKinds": ["workshop_booking"],
        "answeredQuestionId": None,
        "resolvedReferences": [],
        "referenceCandidates": [],
        "resolvedInputs": [],
        "ambiguity": "none",
        "confidence": "medium",
        "supportingContextIds": ["message:customer-1"],
    }

    resolved = validate_turn_understanding(payload, context, set())

    assert resolved.dialogueAct == "answer_open_question"
    assert resolved.answeredQuestionId == "question-vehicle"
    assert resolved.resolvedReferences == [f"{namespace}:option-2"]


def test_finite_choice_position_uses_the_rendered_sparse_option_number() -> None:
    context = PlanningContext(
        latest_customer_message="option 3",
        pending_interaction={
            "kind": "reference_choice",
            "question_id": "question-vehicle",
            "goal_intent": "test_drive",
            "candidate_references": ["vehicle:veh-041", "vehicle:veh-053"],
        },
        displayed_choices=[
            {
                "position": 1,
                "entityReference": "vehicle:veh-041",
                "label": "Option 1 — MINI Cooper",
            },
            {
                "position": 3,
                "entityReference": "vehicle:veh-053",
                "label": "Option 3 — MINI Cooper",
            },
        ],
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "role": "user",
                "text": "option 3",
            }
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "continue_goal",
        "goalRelation": "active",
        "intentStructure": "single_outcome",
        "intentKinds": ["test_drive"],
        "answeredQuestionId": None,
        "resolvedReferences": [],
        "referenceCandidates": [],
        "resolvedInputs": [],
        "ambiguity": "none",
        "confidence": "medium",
        "supportingContextIds": ["message:customer-1"],
    }

    resolved = validate_turn_understanding(payload, context, set())

    assert resolved.resolvedReferences == ["vehicle:veh-053"]


def test_finite_choice_compiles_one_multiword_descriptor_match_from_five_candidates() -> None:
    customer_text = "mini countryman"
    vehicles = [
        {
            "position": position,
            "vehicleId": f"veh-00{position}",
            "make": make,
            "model": model,
        }
        for position, (make, model) in enumerate(
            [
                ("BMW", "3 Series"),
                ("MINI", "Countryman"),
                ("BMW", "1 Series"),
                ("MINI", "Cooper"),
                ("Volvo", "XC40"),
            ],
            start=1,
        )
    ]
    references = [f"vehicle:{vehicle['vehicleId']}" for vehicle in vehicles]
    context = PlanningContext(
        latest_customer_message=customer_text,
        pending_interaction={
            "kind": "reference_choice",
            "question_id": "question-vehicle",
            "goal_intent": "test_drive",
            "candidate_references": references,
            "expected_fields": ["vehicleId"],
        },
        displayed_vehicles=vehicles,
        displayed_choices=[
            {
                "position": vehicle["position"],
                "entityReference": reference,
                "label": f"Option {vehicle['position']} — {vehicle['make']} {vehicle['model']}",
            }
            for vehicle, reference in zip(vehicles, references, strict=True)
        ],
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "kind": "conversation_turn",
                "relation": "answers_open_question",
                "relevance": 1.0,
                "role": "user",
                "text": customer_text,
            }
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "answer_open_question",
        "goalRelation": "open_question",
        "intentStructure": "single_outcome",
        "intentKinds": ["test_drive"],
        "answeredQuestionId": "question-vehicle",
        "resolvedReferences": [],
        "referenceCandidates": [],
        "resolvedInputs": [
            {
                "field": "make",
                "value": "MINI",
                "sourceContextId": "message:customer-1",
                "sourceText": "mini",
            },
            {
                "field": "model",
                "value": "Countryman",
                "sourceContextId": "message:customer-1",
                "sourceText": "countryman",
            },
        ],
        "ambiguity": "none",
        "confidence": "high",
        "supportingContextIds": ["message:customer-1"],
    }

    resolved = validate_turn_understanding(payload, context, {"make", "model"})

    assert resolved.dialogueAct == "answer_open_question"
    assert resolved.answeredQuestionId == "question-vehicle"
    assert resolved.resolvedReferences == ["vehicle:veh-002"]
    assert resolved.referenceCandidates == []
    assert resolved.resolvedInputs == []


def test_finite_choice_keeps_a_descriptor_ambiguous_across_five_candidates() -> None:
    customer_text = "mini"
    vehicles = [
        {"vehicleId": "veh-001", "make": "MINI", "model": "Countryman"},
        {"vehicleId": "veh-002", "make": "BMW", "model": "3 Series"},
        {"vehicleId": "veh-003", "make": "MINI", "model": "Cooper"},
        {"vehicleId": "veh-004", "make": "Volvo", "model": "XC40"},
        {"vehicleId": "veh-005", "make": "Kia", "model": "Sportage"},
    ]
    references = [f"vehicle:{vehicle['vehicleId']}" for vehicle in vehicles]
    context = PlanningContext(
        latest_customer_message=customer_text,
        pending_interaction={
            "kind": "reference_choice",
            "question_id": "question-vehicle",
            "goal_intent": "test_drive",
            "candidate_references": references,
            "expected_fields": ["vehicleId"],
        },
        displayed_vehicles=vehicles,
        displayed_choices=[
            {
                "position": position,
                "entityReference": reference,
                "label": f"Option {position}",
            }
            for position, reference in enumerate(references, start=1)
        ],
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "role": "user",
                "text": customer_text,
            }
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "continue_goal",
        "goalRelation": "active",
        "intentStructure": "single_outcome",
        "intentKinds": ["test_drive"],
        "answeredQuestionId": None,
        "resolvedReferences": [],
        "referenceCandidates": [],
        "resolvedInputs": [
            {
                "field": "make",
                "value": "MINI",
                "sourceContextId": "message:customer-1",
                "sourceText": "mini",
            }
        ],
        "ambiguity": "none",
        "confidence": "medium",
        "supportingContextIds": ["message:customer-1"],
    }

    resolved = validate_turn_understanding(payload, context, {"make"})

    assert resolved.dialogueAct == "continue_goal"
    assert resolved.resolvedReferences == []
    assert resolved.resolvedInputs[0].value == "MINI"


@pytest.mark.parametrize("customer_text", ["I need it for 2 days", "the second-hand one"])
def test_finite_choice_position_does_not_consume_unrelated_numbers(
    customer_text: str,
) -> None:
    context = PlanningContext(
        latest_customer_message=customer_text,
        pending_interaction={
            "kind": "reference_choice",
            "question_id": "question-vehicle",
            "goal_intent": "test_drive",
            "candidate_references": ["vehicle:veh-005", "vehicle:veh-029"],
        },
        displayed_choices=[
            {"position": 1, "entityReference": "vehicle:veh-005", "label": "MINI Cooper"},
            {"position": 2, "entityReference": "vehicle:veh-029", "label": "MINI Cooper"},
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "continue_goal",
        "goalRelation": "active",
        "intentStructure": "single_outcome",
        "intentKinds": ["test_drive"],
        "answeredQuestionId": None,
        "resolvedReferences": [],
        "referenceCandidates": [],
        "resolvedInputs": [],
        "ambiguity": "none",
        "confidence": "medium",
        "supportingContextIds": [],
    }

    resolved = validate_turn_understanding(payload, context, set())

    assert resolved.dialogueAct == "continue_goal"
    assert resolved.resolvedReferences == []


def test_visible_ordinal_replaces_a_stale_reference_outside_an_open_question() -> None:
    context = PlanningContext(
        latest_customer_message="book a test drive for option 5",
        workflow_state={
            "activeWorkflow": "vehicle_search",
            "pausedWorkflow": {
                "activeWorkflow": "test_drive",
                "entities": {"vehicleId": "veh-042"},
            },
        },
        displayed_choices=[
            {
                "position": position,
                "entityReference": f"vehicle:veh-0{position:02}",
                "label": f"Option {position} — Volvo XC40",
            }
            for position in range(1, 6)
        ],
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "role": "user",
                "text": "book a test drive for option 5",
            }
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "resume_goal",
        "goalRelation": "paused",
        "intentStructure": "single_outcome",
        "intentKinds": ["test_drive"],
        "answeredQuestionId": None,
        "resolvedReferences": ["vehicle:veh-042"],
        "referenceCandidates": [],
        "resolvedInputs": [
            {
                "field": "vehicleIds",
                "value": ["veh-042"],
                "sourceContextId": "message:customer-1",
                "sourceText": "option 5",
            }
        ],
        "ambiguity": "none",
        "confidence": "high",
        "supportingContextIds": ["message:customer-1"],
    }

    resolved = validate_turn_understanding(payload, context, {"vehicleIds"})

    assert resolved.resolvedReferences == ["vehicle:veh-005"]
    assert resolved.resolvedInputs == []
    assert resolved.dialogueAct == "resume_goal"


@pytest.mark.parametrize("workflow_kind", sorted(CapabilityRegistry.SPECS))
def test_validated_resume_compiles_the_control_transition_for_every_workflow(
    workflow_kind: str,
) -> None:
    goal_intent = CapabilityRegistry.goal_intent_for_active_workflow(workflow_kind)
    context = PlanningContext(
        latest_customer_message="continue my earlier request",
        workflow_state={
            "activeWorkflow": "vehicle_search",
            "pausedWorkflow": {
                "activeWorkflow": workflow_kind,
                "entities": {"vehicleId": "veh-042"},
            },
        },
    )
    understanding = TurnUnderstanding(
        dialogueAct="resume_goal",
        goalRelation="paused",
        intentKinds=[goal_intent],
        confidence="high",
    )

    compiled = _compiled_conversation_control(context, understanding)

    assert compiled is not None
    control, remaining = compiled
    assert control.name == "resume_paused_capability"
    assert control.arguments == {}
    assert control.intent_kind == "capability"
    assert remaining is None


@pytest.mark.parametrize("workflow_kind", sorted(CapabilityRegistry.SPECS))
def test_validated_cancel_compiles_the_control_transition_for_every_workflow(
    workflow_kind: str,
) -> None:
    context = PlanningContext(
        latest_customer_message="cancel that request",
        workflow_state={"activeWorkflow": workflow_kind},
    )
    understanding = TurnUnderstanding(
        dialogueAct="cancel_goal",
        goalRelation="active",
        intentKinds=["capability"],
        confidence="high",
    )

    compiled = _compiled_conversation_control(context, understanding)

    assert compiled is not None
    control, remaining = compiled
    assert control.name == "cancel_active_capability"
    assert remaining is None


def test_compound_resume_leaves_only_the_other_outcomes_for_business_planning() -> None:
    context = PlanningContext(
        latest_customer_message="continue my test drive and show Saturday opening hours",
        workflow_state={
            "activeWorkflow": "vehicle_search",
            "pausedWorkflow": {"activeWorkflow": "test_drive"},
        },
    )
    understanding = TurnUnderstanding(
        dialogueAct="resume_goal",
        goalRelation="paused",
        intentStructure="compound_outcomes",
        intentKinds=["test_drive", "opening_hours"],
        confidence="high",
    )

    compiled = _compiled_conversation_control(context, understanding)

    assert compiled is not None
    control, remaining = compiled
    assert control.name == "resume_paused_capability"
    assert remaining is not None
    assert remaining.intentKinds == ["opening_hours"]
    assert remaining.intentStructure == "single_outcome"


@pytest.mark.asyncio
async def test_explicit_resume_bypasses_fresh_subject_planning_and_restores_paused_goal() -> None:
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        body = json.loads(request.content)
        assert {tool["name"] for tool in body["tools"]} == {"resolve_conversation_turn"}
        return httpx.Response(
            200,
            json=function_call(
                "resolve_conversation_turn",
                {
                    "schemaVersion": 1,
                    "dialogueAct": "resume_goal",
                    "goalRelation": "paused",
                    "intentStructure": "single_outcome",
                    "intentKinds": ["test_drive"],
                    "resultPresentation": "default",
                    "answeredQuestionId": None,
                    "resolvedReferences": ["vehicle:veh-046"],
                    "referenceCandidates": [],
                    "resolvedInputs": [],
                    "requestedInputChanges": [],
                    "ambiguity": "none",
                    "confidence": "high",
                    "supportingContextIds": ["message:customer-1"],
                },
            ),
        )

    catalogue = UnifiedToolCatalog(NeverExecute())
    context = PlanningContext(
        latest_customer_message="Go back to my MINI test drive",
        workflow_state={
            "activeWorkflow": "vehicle_search",
            "entities": {},
            "pausedWorkflow": {
                "activeWorkflow": "test_drive",
                "entities": {"vehicleId": "veh-042"},
            },
        },
        displayed_vehicles=[
            {"position": 1, "vehicleId": "veh-046", "make": "Volvo", "model": "XC40"}
        ],
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "role": "user",
                "text": "Go back to my MINI test drive",
            }
        ],
    )
    semantic_messages = SemanticMessages(
        [{"role": "user", "content": context.latest_customer_message}],
        context,
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        hosted = HostedLlmProvider(
            provider_url="https://provider.example/v1",
            api_key="shared-key",
            model="hosted-model",
            catalogue=catalogue,
            retriever=FixedRetriever(catalogue),
            client=http,
        )
        reply = await hosted.generate_turn(semantic_messages)

    assert requests == 1
    assert [call.name for call in reply.tool_calls] == ["resume_paused_capability"]
    assert reply.tool_calls[0].arguments == {}
    assert reply.turn_understanding is not None
    assert reply.turn_understanding.resolvedReferences == ["vehicle:veh-046"]


@pytest.mark.asyncio
async def test_compound_resume_executes_control_before_planning_the_remaining_outcome() -> None:
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        body = json.loads(request.content)
        names = {tool["name"] for tool in body["tools"]}
        if "resolve_conversation_turn" in names:
            return httpx.Response(
                200,
                json=function_call(
                    "resolve_conversation_turn",
                    {
                        "schemaVersion": 1,
                        "dialogueAct": "resume_goal",
                        "goalRelation": "paused",
                        "intentStructure": "compound_outcomes",
                        "intentKinds": ["test_drive", "opening_hours"],
                        "resultPresentation": "explicit_request",
                        "answeredQuestionId": None,
                        "resolvedReferences": [],
                        "referenceCandidates": [],
                        "resolvedInputs": [
                            {
                                "field": "day",
                                "value": "Saturday",
                                "sourceContextId": "message:customer-1",
                                "sourceText": "Saturday",
                            }
                        ],
                        "requestedInputChanges": [],
                        "ambiguity": "none",
                        "confidence": "high",
                        "supportingContextIds": ["message:customer-1"],
                    },
                ),
            )
        assert "resume_paused_capability" not in names
        assert "prepare_test_drive" not in names
        assert "list_opening_hours" in names
        return httpx.Response(
            200,
            json=function_call("list_opening_hours", {"day": "Saturday"}),
        )

    catalogue = UnifiedToolCatalog(NeverExecute())
    context = PlanningContext(
        latest_customer_message=(
            "Go back to my test drive and tell me the Saturday opening hours"
        ),
        workflow_state={
            "activeWorkflow": "vehicle_search",
            "pausedWorkflow": {
                "activeWorkflow": "test_drive",
                "entities": {"vehicleId": "veh-042"},
            },
        },
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "role": "user",
                "text": "Go back to my test drive and tell me the Saturday opening hours",
            }
        ],
    )
    semantic_messages = SemanticMessages(
        [{"role": "user", "content": context.latest_customer_message}],
        context,
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        hosted = HostedLlmProvider(
            provider_url="https://provider.example/v1",
            api_key="shared-key",
            model="hosted-model",
            catalogue=catalogue,
            retriever=FixedRetriever(catalogue),
            client=http,
        )
        reply = await hosted.generate_turn(semantic_messages)

    assert requests == 2
    assert [call.name for call in reply.tool_calls] == [
        "resume_paused_capability",
        "list_opening_hours",
    ]
    assert reply.turn_understanding is not None
    assert reply.turn_understanding.intentKinds == ["test_drive", "opening_hours"]


def test_finite_scalar_choice_precedence_resolves_the_declared_target_field() -> None:
    context = PlanningContext(
        latest_customer_message="Parts",
        workflow_state={"activeWorkflow": "callback"},
        pending_interaction={
            "kind": "input",
            "question_id": "question-department",
            "goal_intent": "callback",
            "candidate_references": [],
            "expected_fields": ["department"],
        },
        displayed_choices=[
            {
                "entityReference": "workflow_option:sales",
                "label": "Sales",
                "inputField": "department",
                "value": "sales",
            },
            {
                "entityReference": "workflow_option:service",
                "label": "Service",
                "inputField": "department",
                "value": "service",
            },
            {
                "entityReference": "workflow_option:parts",
                "label": "Parts",
                "inputField": "department",
                "value": "parts",
            },
        ],
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "kind": "conversation_turn",
                "relation": "answers_open_question",
                "relevance": 1.0,
                "role": "user",
                "text": "Parts",
            },
            {
                "contextId": "question-department",
                "kind": "open_question",
                "relation": "expects_latest_answer",
                "relevance": 1.0,
            },
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "continue_goal",
        "goalRelation": "active",
        "intentStructure": "single_outcome",
        "intentKinds": ["callback"],
        "answeredQuestionId": None,
        "resolvedReferences": [],
        "referenceCandidates": [],
        "resolvedInputs": [],
        "ambiguity": "none",
        "confidence": "medium",
        "supportingContextIds": ["message:customer-1"],
    }

    resolved = validate_turn_understanding(payload, context, {"department"})

    assert resolved.dialogueAct == "answer_open_question"
    assert resolved.answeredQuestionId == "question-department"
    assert resolved.resolvedReferences == []
    assert [(item.field, item.value) for item in resolved.resolvedInputs] == [
        ("department", "parts")
    ]


def test_exact_entity_choice_precedence_repairs_a_mislabeled_new_goal() -> None:
    context = PlanningContext(
        latest_customer_message="bolton",
        workflow_state={"activeWorkflow": "callback"},
        pending_interaction={
            "kind": "reference_choice",
            "question_id": "question-dealership",
            "goal_intent": "callback",
            "candidate_references": [
                "dealership:northstar-bolton",
                "dealership:northstar-liverpool",
            ],
            "expected_fields": ["dealershipId"],
        },
        displayed_choices=[
            {
                "position": 1,
                "entityReference": "dealership:northstar-bolton",
                "label": "Northstar Bolton",
            },
            {
                "position": 2,
                "entityReference": "dealership:northstar-liverpool",
                "label": "Northstar Liverpool",
            },
        ],
    )
    payload = {
        "dialogueAct": "start_goal",
        "goalRelation": "new",
        "intentKinds": ["dealership_detail"],
        "resolvedReferences": [],
        "referenceCandidates": [],
        "resolvedInputs": [],
        "ambiguity": "none",
        "confidence": "medium",
    }

    resolved = validate_turn_understanding(payload, context, set())

    assert resolved.dialogueAct == "answer_open_question"
    assert resolved.goalRelation == "open_question"
    assert resolved.intentKinds == ["callback"]
    assert resolved.answeredQuestionId == "question-dealership"
    assert resolved.resolvedReferences == ["dealership:northstar-bolton"]


def test_deictic_reference_ambiguity_does_not_require_a_turn_wide_provenance_label() -> None:
    context = PlanningContext(
        latest_customer_message="Can I get finance information for this vehicle?",
        displayed_vehicles=[
            {"vehicleId": "veh-014", "make": "BMW"},
            {"vehicleId": "veh-042", "make": "MINI"},
        ],
    )
    payload = {
        "dialogueAct": "interrupt_with_information_request",
        "goalRelation": "new",
        "intentKinds": ["business_information"],
        "referenceBasis": "described",
        "resolvedReferences": [],
        "referenceCandidates": ["vehicle:veh-014", "vehicle:veh-042"],
        "resolvedInputs": [],
        "ambiguity": "reference",
        "confidence": "medium",
    }

    resolved = validate_turn_understanding(payload, context, set())

    assert resolved.referenceCandidates == ["vehicle:veh-014", "vehicle:veh-042"]
    assert resolved.ambiguity == "reference"


def test_finite_choice_wins_when_provider_adds_an_unrelated_resolved_input() -> None:
    context = PlanningContext(
        latest_customer_message="break",
        workflow_state={"activeWorkflow": "workshop_booking"},
        pending_interaction={
            "kind": "reference_choice",
            "question_id": "question-service",
            "goal_intent": "workshop_booking",
            "candidate_references": [
                "service:brake-inspection",
                "service:diagnostic",
            ],
            "expected_fields": ["serviceTypeId"],
        },
        displayed_choices=[
            {
                "entityReference": "service:brake-inspection",
                "label": "Brake inspection",
            },
            {"entityReference": "service:diagnostic", "label": "Diagnostic inspection"},
        ],
        context_evidence=[{"contextId": "message:customer-1", "role": "user", "text": "break"}],
    )
    payload = {
        "dialogueAct": "answer_open_question",
        "goalRelation": "open_question",
        "intentKinds": ["workshop_booking"],
        "referenceBasis": "described",
        "answeredQuestionId": "question-service",
        "resolvedReferences": ["service:brake-inspection"],
        "referenceCandidates": [],
        "resolvedInputs": [
            {
                "field": "q",
                "value": "break",
                "sourceContextId": "message:customer-1",
                "sourceText": "break",
            }
        ],
        "requestedInputChanges": [
            {
                "field": "phone",
                "sourceContextId": "message:customer-1",
                "sourceText": "break",
            }
        ],
        "ambiguity": "none",
        "confidence": "high",
    }

    resolved = validate_turn_understanding(payload, context, {"q"})

    assert resolved.dialogueAct == "answer_open_question"
    assert resolved.resolvedReferences == ["service:brake-inspection"]
    assert resolved.resolvedInputs == []
    assert resolved.requestedInputChanges == []


def test_explicit_goal_switch_is_not_consumed_as_a_finite_choice_answer() -> None:
    context = PlanningContext(
        latest_customer_message="I want to book a test drive instead",
        workflow_state={"activeWorkflow": "workshop_booking"},
        pending_interaction={
            "kind": "reference_choice",
            "question_id": "question-service",
            "goal_intent": "workshop_booking",
            "candidate_references": ["service:brake-inspection"],
            "expected_fields": ["serviceTypeId"],
        },
        displayed_choices=[
            {
                "entityReference": "service:brake-inspection",
                "label": "Brake inspection",
            }
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "switch_goal",
        "goalRelation": "new",
        "intentStructure": "single_outcome",
        "intentKinds": ["test_drive"],
        "answeredQuestionId": None,
        "resolvedReferences": [],
        "referenceCandidates": [],
        "resolvedInputs": [],
        "ambiguity": "none",
        "confidence": "high",
        "supportingContextIds": [],
    }

    resolved = validate_turn_understanding(payload, context, set())

    assert resolved.dialogueAct == "switch_goal"
    assert resolved.intentKinds == ["test_drive"]
    assert resolved.answeredQuestionId is None


def test_reference_resolution_preserves_multi_entity_values_in_one_namespace() -> None:
    context = PlanningContext(
        latest_customer_message="Compare the MINI and BMW",
        displayed_vehicles=[
            {"vehicleId": "veh-001", "make": "MINI"},
            {"vehicleId": "veh-002", "make": "BMW"},
        ],
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "kind": "conversation_turn",
                "relation": "recent_supporting_evidence",
                "relevance": 1.0,
                "role": "user",
                "text": "Compare the MINI and BMW",
            }
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "start_goal",
        "goalRelation": "new",
        "intentStructure": "single_outcome",
        "intentKinds": ["vehicle_comparison"],
        "answeredQuestionId": None,
        "resolvedReferences": ["vehicle:veh-001", "vehicle:veh-002"],
        "referenceCandidates": [],
        "resolvedInputs": [
            {
                "field": "make",
                "value": ["MINI", "BMW"],
                "sourceContextId": "message:customer-1",
                "sourceText": "MINI and BMW",
            }
        ],
        "ambiguity": "none",
        "confidence": "high",
        "supportingContextIds": ["message:customer-1"],
    }

    resolved = validate_turn_understanding(payload, context, {"make"})

    assert resolved.resolvedReferences == ["vehicle:veh-001", "vehicle:veh-002"]
    assert resolved.referenceCandidates == []


def test_natural_acceptance_can_resolve_the_only_open_finite_choice() -> None:
    context = PlanningContext(
        latest_customer_message="this works",
        pending_interaction={
            "kind": "reference_choice",
            "question_id": "question-slot",
            "goal_intent": "test_drive",
            "candidate_references": ["appointment:td-slot-0280"],
            "expected_fields": [],
        },
        displayed_choices=[
            {
                "position": 1,
                "entityReference": "appointment:td-slot-0280",
                "startsAt": "2026-09-10T16:00:00Z",
            }
        ],
        context_evidence=[
            {
                "contextId": "question-slot",
                "kind": "open_question",
                "relation": "expects_latest_answer",
                "relevance": 1.0,
            }
        ],
    )
    payload = {
        "schemaVersion": 1,
        "dialogueAct": "answer_open_question",
        "goalRelation": "open_question",
        "intentStructure": "single_outcome",
        "intentKinds": ["test_drive"],
        "answeredQuestionId": "question-slot",
        "resolvedReferences": ["appointment:td-slot-0280"],
        "referenceCandidates": [],
        "resolvedInputs": [],
        "ambiguity": "none",
        "confidence": "high",
        "supportingContextIds": ["question-slot"],
    }

    result = validate_turn_understanding(payload, context, set())
    assert result.resolvedReferences == ["appointment:td-slot-0280"]


def test_semantic_resolution_receives_only_the_customer_facing_appointment_clock() -> None:
    context = PlanningContext(
        latest_customer_message="Tuesday at 10 am works",
        displayed_choices=[
            {
                "position": 1,
                "entityReference": "appointment:td-slot-0241",
                "label": "Tue, 8 Sept 2026, 10:00",
                "startsAt": "2026-09-08T09:00:00Z",
                "localStartsAt": "2026-09-08T10:00+01:00",
                "localDate": "2026-09-08",
                "localTime": "10:00",
                "displayLabel": "Tue, 8 Sept 2026, 10:00",
            }
        ],
    )

    request = resolution_request(
        model="hosted-model",
        context=context,
        input_fields=set(),
        input_catalogue=[],
        intent_catalogue=[],
    )
    envelope = json.loads(request["input"][0]["content"])
    choice = envelope["displayedEntities"]["choices"][0]

    assert choice["label"] == "Tue, 8 Sept 2026, 10:00"
    assert choice["localTime"] == "10:00"
    assert "startsAt" not in choice
    assert "localStartsAt" not in choice


def test_semantic_resolution_receives_ranked_customer_knowledge_without_routing_on_phrases() -> (
    None
):
    context = PlanningContext(
        latest_customer_message="will you pick my car?",
        workflow_state={"activeWorkflow": "part_exchange", "stage": "estimate_ready"},
    )
    knowledge = [
        {
            "id": "knowledge:customer.vehicle_pickup_and_collection_policy",
            "title": "Vehicle pickup and collection policy",
            "text": "Northstar has no confirmed collection or home-pickup information.",
        }
    ]

    request = resolution_request(
        model="hosted-model",
        context=context,
        input_fields=set(),
        input_catalogue=[],
        intent_catalogue=[],
        knowledge_catalogue=knowledge,
    )
    envelope = json.loads(request["input"][0]["content"])

    assert envelope["rankedKnowledgeEvidence"] == knowledge
    supporting_ids = request["tools"][0]["parameters"]["properties"]["supportingContextIds"][
        "items"
    ]["enum"]
    assert "knowledge:customer.vehicle_pickup_and_collection_policy" in supporting_ids


@pytest.mark.parametrize(
    ("tool_name", "argument_field", "reference", "planner_value", "trusted_value"),
    [
        ("get_vehicle", "id", "vehicle:veh-001", "veh-999", "veh-001"),
        ("get_offer", "id", "offer:offer-02", "Offer 2", "offer-02"),
        (
            "get_dealership",
            "id",
            "dealership:northstar-bolton",
            "Bolton",
            "northstar-bolton",
        ),
        ("list_workshop_slots", "serviceTypeId", "service:mot", "MOT", "mot"),
    ],
)
def test_validated_semantic_references_own_business_call_identity(
    tool_name: str,
    argument_field: str,
    reference: str,
    planner_value: str,
    trusted_value: str,
) -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    context = PlanningContext(
        latest_customer_message="selected choice",
        turn_understanding=TurnUnderstanding(
            dialogueAct="start_goal",
            goalRelation="new",
            intentStructure="single_outcome",
            intentKinds=["workshop_booking"],
            resolvedReferences=[reference],
            ambiguity="none",
            confidence="high",
        ),
    )
    proposal = TurnProposal(
        [
            ToolCall(
                "call-1",
                tool_name,
                {argument_field: planner_value, "q": "preserved"},
                intent_kind="workshop_booking",
            )
        ]
    )

    bound = _bind_resolved_reference_arguments(proposal, context, catalogue)

    assert bound.tool_calls[0].arguments[argument_field] == trusted_value
    assert bound.tool_calls[0].arguments["q"] == "preserved"


def test_information_interruption_cannot_execute_a_transactional_workflow() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    context = PlanningContext(
        latest_customer_message="Is that service available?",
        turn_understanding=TurnUnderstanding(
            dialogueAct="interrupt_with_information_request",
            goalRelation="unrelated",
            intentStructure="single_outcome",
            intentKinds=["part_exchange"],
            ambiguity="none",
            confidence="high",
        ),
    )
    proposal = TurnProposal(
        [ToolCall("call-1", "prepare_part_exchange", {}, intent_kind="part_exchange")]
    )

    with pytest.raises(ValueError, match="information request cannot start a workflow"):
        _validate_plan_against_understanding(proposal, context, catalogue)


@pytest.mark.asyncio
async def test_ranked_policy_evidence_keeps_a_factual_follow_up_out_of_the_active_workflow() -> (
    None
):
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        names = {tool["name"] for tool in body["tools"]}
        if "resolve_conversation_turn" in names:
            envelope = json.loads(body["input"][0]["content"])
            assert envelope["latestCustomerMessage"] == "will you pick my car?"
            assert "knowledge:customer.vehicle_pickup_and_collection_policy" in {
                item["id"] for item in envelope["rankedKnowledgeEvidence"]
            }
            return httpx.Response(
                200,
                json=function_call(
                    "resolve_conversation_turn",
                    {
                        "schemaVersion": 1,
                        "dialogueAct": "interrupt_with_information_request",
                        "goalRelation": "unrelated",
                        "intentStructure": "single_outcome",
                        "intentKinds": ["business_information"],
                        "resultPresentation": "explicit_request",
                        "answeredQuestionId": None,
                        "resolvedReferences": [],
                        "referenceCandidates": [],
                        "resolvedInputs": [],
                        "requestedInputChanges": [],
                        "ambiguity": "none",
                        "confidence": "high",
                        "supportingContextIds": [
                            "knowledge:customer.vehicle_pickup_and_collection_policy"
                        ],
                    },
                ),
            )
        raise AssertionError("typed knowledge evidence should bypass transactional planning")

    catalogue = UnifiedToolCatalog(NeverExecute())
    contextual_messages = SemanticMessages(
        [{"role": "user", "content": "will you pick my car?"}],
        PlanningContext(
            latest_customer_message="will you pick my car?",
            workflow_state={
                "activeWorkflow": "part_exchange",
                "stage": "estimate_ready",
                "entities": {},
                "constraints": {},
            },
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        hosted = HostedLlmProvider(
            provider_url="https://provider.example/v1",
            api_key="shared-key",
            model="hosted-model",
            catalogue=catalogue,
            retriever=FixedRetriever(catalogue),
            client=http,
        )
        reply = await hosted.generate_turn(contextual_messages)

    assert calls == 1
    assert reply.tool_calls == []
    assert reply.response_mode == "answer"
    assert "confirmed Northstar information" in reply.text


def test_latest_conversation_results_supersede_host_page_vehicle_scope() -> None:
    context = PlanningContext(
        latest_customer_message="book an appointment for the BMW 1 Series",
        displayed_vehicles=[
            {"position": 1, "vehicleId": "veh-001", "model": "3 Series"},
            {"position": 3, "vehicleId": "veh-003", "model": "1 Series"},
        ],
        page_vehicles=[
            {"position": 7, "vehicleId": "veh-007", "model": "1 Series"},
            {"position": 10, "vehicleId": "veh-010", "model": "1 Series"},
        ],
    )

    references = trusted_context_references(context)

    assert "vehicle:veh-003" in references
    assert "vehicle:veh-007" not in references
    assert "vehicle:veh-010" not in references


@pytest.mark.asyncio
async def test_only_multi_intent_business_tools_require_an_intent_declaration() -> None:
    async with httpx.AsyncClient(base_url="https://provider.example/v1") as http:
        hosted = provider(http)
        payload = hosted._planner_request_payload(
            messages("compare the cars"),
            FixedRetriever(hosted.catalogue).candidates,
            PlanningContext(latest_customer_message="compare the cars"),
        )
        tools = {tool["name"]: tool for tool in payload["tools"]}

    assert "intentKind" not in tools["search_vehicles"]["parameters"]["properties"]
    assert "intentKind" not in tools["search_vehicles"]["parameters"].get("required", [])
    assert "intentKind" in tools["list_workshop_slots"]["parameters"]["required"]
    assert set(tools["list_workshop_slots"]["parameters"]["properties"]["intentKind"]["enum"]) == {
        "booking_amendment",
        "workshop_availability",
        "workshop_booking",
    }


@pytest.mark.asyncio
async def test_clarification_schema_requires_a_retrieved_tool_blocker() -> None:
    async with httpx.AsyncClient(base_url="https://provider.example/v1") as http:
        hosted = provider(http)
        candidates = FixedRetriever(hosted.catalogue).candidates
        payload = hosted._planner_request_payload(
            messages("Help me with a vehicle"),
            candidates,
            PlanningContext(latest_customer_message="Help me with a vehicle"),
        )

    clarification = next(
        tool for tool in payload["tools"] if tool["name"] == "ask_conversational_clarification"
    )
    assert set(clarification["parameters"]["required"]) == {
        "question",
        "blockingTool",
        "blockingFields",
        "blockingPreconditions",
    }
    assert set(clarification["parameters"]["properties"]["blockingTool"]["enum"]) == {
        tool.id
        for tool in candidates.tools
        if tool.input_schema.get("required") or tool.preconditions
    }
    assert (
        "search_vehicles" not in clarification["parameters"]["properties"]["blockingTool"]["enum"]
    )


@pytest.mark.asyncio
async def test_clarification_capability_is_omitted_when_every_tool_can_proceed() -> None:
    async with httpx.AsyncClient(base_url="https://provider.example/v1") as http:
        hosted = provider(http)
        candidates = CandidateSet((hosted.catalogue.get("search_vehicles"),), ())
        payload = hosted._planner_request_payload(
            messages("I want black cars"),
            candidates,
            PlanningContext(latest_customer_message="I want black cars"),
        )

    assert "ask_conversational_clarification" not in {tool["name"] for tool in payload["tools"]}


def test_opening_hours_preflight_keeps_progress_and_removes_blocked_sibling() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    understanding = TurnUnderstanding(
        dialogueAct="start_goal",
        goalRelation="new",
        intentStructure="single_outcome",
        intentKinds=["opening_hours"],
        resolvedInputs=[
            ResolvedInput(
                field="day",
                value="Saturday",
                sourceContextId="message:customer-1",
                sourceText="Saturday",
            )
        ],
        ambiguity="none",
        confidence="high",
    )
    context = PlanningContext(
        latest_customer_message="What are your opening hours this Saturday?",
        turn_understanding=understanding,
    )
    affordances = PlannerAffordanceEvaluator(catalogue).evaluate(
        CandidateSet(
            (
                catalogue.get("get_opening_hours"),
                catalogue.get("list_opening_hours"),
            ),
            (),
        ),
        context,
    )

    assert [tool.id for tool in affordances.candidates.tools] == ["list_opening_hours"]
    assert affordances.executable_tool_ids == {"list_opening_hours"}
    assert affordances.blocked_tool_ids == {"get_opening_hours"}
    assert affordances.allow_generic_clarification is False


def test_workflow_entry_preflight_does_not_treat_optional_fields_as_blockers() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    understanding = TurnUnderstanding(
        dialogueAct="start_goal",
        goalRelation="new",
        intentStructure="single_outcome",
        intentKinds=["callback"],
        ambiguity="none",
        confidence="high",
    )
    affordances = PlannerAffordanceEvaluator(catalogue).evaluate(
        CandidateSet((catalogue.get("prepare_callback"),), ()),
        PlanningContext(
            latest_customer_message="Please call me back",
            turn_understanding=understanding,
        ),
    )

    assert affordances.executable_tool_ids == {"prepare_callback"}
    assert affordances.allow_generic_clarification is False


def test_required_trusted_reference_remains_a_real_planning_blocker() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    understanding = TurnUnderstanding(
        dialogueAct="start_goal",
        goalRelation="new",
        intentStructure="single_outcome",
        intentKinds=["vehicle_detail"],
        ambiguity="none",
        confidence="high",
    )
    candidates = CandidateSet((catalogue.get("get_vehicle"),), ())
    affordances = PlannerAffordanceEvaluator(catalogue).evaluate(
        candidates,
        PlanningContext(
            latest_customer_message="Tell me about a vehicle",
            turn_understanding=understanding,
        ),
    )

    assert affordances.candidates == candidates
    assert affordances.executable_tool_ids == set()
    assert affordances.blocked_tool_ids == {"get_vehicle"}
    assert affordances.allow_generic_clarification is True


def test_resolved_required_input_retains_generic_clarification_affordance() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    understanding = TurnUnderstanding(
        dialogueAct="start_goal",
        goalRelation="new",
        intentStructure="single_outcome",
        intentKinds=["vehicle_detail"],
        ambiguity="required_input",
        confidence="medium",
    )

    affordances = PlannerAffordanceEvaluator(catalogue).evaluate(
        CandidateSet((catalogue.get("get_vehicle"),), ()),
        PlanningContext(
            latest_customer_message="Tell me about a vehicle",
            turn_understanding=understanding,
        ),
    )

    assert affordances.executable_tool_ids == set()
    assert affordances.blocked_tool_ids == {"get_vehicle"}
    assert affordances.allow_generic_clarification is True


def test_required_input_never_leaves_the_planner_without_a_legal_next_move() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    candidates = CandidateSet(tuple(catalogue.planner_tools()), ())
    understanding = TurnUnderstanding(
        dialogueAct="start_goal",
        goalRelation="new",
        intentStructure="single_outcome",
        intentKinds=["vehicle_detail"],
        ambiguity="required_input",
        confidence="medium",
    )

    affordances = PlannerAffordanceEvaluator(catalogue).evaluate(
        candidates,
        PlanningContext(
            latest_customer_message="Tell me about a vehicle",
            turn_understanding=understanding,
        ),
    )

    assert affordances.executable_tool_ids == set()
    assert affordances.blocked_tool_ids == {tool.id for tool in candidates.tools}
    assert affordances.allow_generic_clarification is True


def test_resolved_trusted_reference_keeps_the_exact_read_executable() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    understanding = TurnUnderstanding(
        dialogueAct="start_goal",
        goalRelation="new",
        intentStructure="single_outcome",
        intentKinds=["vehicle_detail"],
        resolvedReferences=["vehicle:veh-001"],
        ambiguity="none",
        confidence="high",
    )
    affordances = PlannerAffordanceEvaluator(catalogue).evaluate(
        CandidateSet((catalogue.get("get_vehicle"),), ()),
        PlanningContext(
            latest_customer_message="Tell me more about this car",
            displayed_vehicles=[{"vehicleId": "veh-001"}],
            turn_understanding=understanding,
        ),
    )

    assert affordances.executable_tool_ids == {"get_vehicle"}
    assert affordances.blocked_tool_ids == set()
    assert affordances.allow_generic_clarification is False


def test_described_vehicle_availability_has_one_executable_resolution_operation() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    understanding = TurnUnderstanding(
        dialogueAct="start_goal",
        goalRelation="new",
        intentStructure="single_outcome",
        intentKinds=["vehicle_availability"],
        resolvedInputs=[
            ResolvedInput(
                field="model",
                value="1 Series",
                sourceContextId="message:customer-1",
                sourceText="1 Series",
            )
        ],
        ambiguity="none",
        confidence="high",
    )
    candidates = CandidateSet(
        (
            catalogue.get("get_vehicle_availability"),
            catalogue.get("resolve_vehicle_availability"),
        ),
        (),
    )

    affordances = PlannerAffordanceEvaluator(catalogue).evaluate(
        candidates,
        PlanningContext(
            latest_customer_message="Is the 1 Series sold?",
            turn_understanding=understanding,
        ),
    )

    assert affordances.executable_tool_ids == {"resolve_vehicle_availability"}
    assert affordances.blocked_tool_ids == {"get_vehicle_availability"}
    assert affordances.allow_generic_clarification is False


def test_answered_reference_choice_exposes_only_operations_that_bind_its_selection() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    understanding = TurnUnderstanding(
        dialogueAct="answer_open_question",
        goalRelation="open_question",
        intentStructure="single_outcome",
        intentKinds=["test_drive"],
        answeredQuestionId="question-slot",
        resolvedReferences=["appointment:td-slot-0280"],
        ambiguity="none",
        confidence="high",
    )
    context = PlanningContext(
        latest_customer_message="Thu, 10 Sept 2026, 17:00",
        workflow_state={
            "version": 3,
            "activeWorkflow": "test_drive",
            "stage": "choosing_slot",
            "entities": {
                "vehicleId": "veh-042",
                "dealershipId": "northstar-stockport",
            },
            "constraints": {
                "collectedPublicValues": {"vehicleId": "veh-042"},
                "selectionEvidence": {
                    "vehicleId": "veh-042",
                    "basis": "trusted_action",
                    "matchedIdentity": "veh-042",
                },
                "missingPublicFields": ["slotId"],
            },
        },
        pending_interaction={
            "kind": "reference_choice",
            "question_id": "question-slot",
            "goal_intent": "test_drive",
            "candidate_references": ["appointment:td-slot-0280"],
            "expected_fields": ["slotId"],
        },
        displayed_choices=[
            {
                "position": 1,
                "entityReference": "appointment:td-slot-0280",
                "label": "Thu, 10 Sept 2026, 17:00",
                "startsAt": "2026-09-10T16:00:00Z",
                "dealershipId": "northstar-stockport",
                "dealershipName": "Northstar Stockport",
                "vehicleId": "veh-042",
            }
        ],
        turn_understanding=understanding,
    )

    affordances = PlannerAffordanceEvaluator(catalogue).evaluate(
        CandidateSet(
            (
                catalogue.get("prepare_test_drive"),
                catalogue.get("list_test_drive_slots"),
            ),
            (),
        ),
        context,
    )

    assert [tool.id for tool in affordances.candidates.tools] == ["prepare_test_drive"]
    assert affordances.executable_tool_ids == {"prepare_test_drive"}
    assert affordances.blocked_tool_ids == {"list_test_drive_slots"}


def test_contextual_reference_choice_does_not_block_a_reference_free_information_read() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    understanding = TurnUnderstanding(
        dialogueAct="answer_open_question",
        goalRelation="open_question",
        intentStructure="single_outcome",
        intentKinds=["business_information"],
        answeredQuestionId="question-vehicle-context",
        resolvedReferences=["vehicle:veh-006"],
        ambiguity="none",
        confidence="high",
    )
    context = PlanningContext(
        latest_customer_message="the 2025 Blazing Blue MINI Countryman",
        pending_interaction={
            "kind": "reference_choice",
            "question_id": "question-vehicle-context",
            "goal_intent": "business_information",
            "candidate_references": ["vehicle:veh-001", "vehicle:veh-006"],
            "expected_fields": [],
        },
        displayed_vehicles=[
            {"vehicleId": "veh-001", "make": "MINI", "model": "Cooper"},
            {"vehicleId": "veh-006", "make": "MINI", "model": "Countryman"},
        ],
        turn_understanding=understanding,
    )
    candidates = CandidateSet((catalogue.get("get_business_information"),), ())

    affordances = PlannerAffordanceEvaluator(catalogue).evaluate(candidates, context)

    assert affordances.executable_tool_ids == {"get_business_information"}
    assert affordances.blocked_tool_ids == set()
    assert affordances.allow_generic_clarification is False

    proposal = TurnProposal(
        [
            ToolCall(
                "call-1",
                "get_business_information",
                {
                    "topic": "finance",
                    "question": "Can I get finance information for this vehicle?",
                },
                intent_kind="business_information",
            )
        ]
    )
    _validate_plan_against_understanding(proposal, context, catalogue)


def test_described_subject_replacement_exposes_only_the_rebinding_operation() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    understanding = TurnUnderstanding(
        dialogueAct="modify_goal",
        goalRelation="active",
        intentKinds=["test_drive"],
        resolvedInputs=[
            ResolvedInput(
                field="make",
                value="MINI",
                sourceContextId="message:customer",
                sourceText="MINI Cooper in Manchester",
            ),
            ResolvedInput(
                field="model",
                value="Cooper",
                sourceContextId="message:customer",
                sourceText="MINI Cooper in Manchester",
            ),
            ResolvedInput(
                field="dealershipTown",
                value="Manchester",
                sourceContextId="message:customer",
                sourceText="MINI Cooper in Manchester",
            ),
        ],
        ambiguity="none",
        confidence="high",
    )
    context = PlanningContext(
        latest_customer_message="Book a test drive for a MINI Cooper in Manchester",
        workflow_state={
            "version": 3,
            "activeWorkflow": "test_drive",
            "stage": "choosing_schedule_preferences",
            "entities": {"vehicleId": "veh-042"},
            "constraints": {
                "schedulingPreferences": {
                    "dateFrom": "2026-09-07",
                    "dateTo": "2026-09-07",
                    "timeOfDay": "afternoon",
                }
            },
        },
        displayed_vehicles=[
            {
                "vehicleId": "veh-042",
                "make": "MINI",
                "model": "Countryman",
            }
        ],
        turn_understanding=understanding,
    )

    affordances = PlannerAffordanceEvaluator(catalogue).evaluate(
        CandidateSet(
            (
                catalogue.get("prepare_test_drive"),
                catalogue.get("list_test_drive_slots"),
            ),
            (),
        ),
        context,
    )

    assert [tool.id for tool in affordances.candidates.tools] == ["prepare_test_drive"]
    assert affordances.executable_tool_ids == {"prepare_test_drive"}
    assert affordances.blocked_tool_ids == {"list_test_drive_slots"}


def test_answered_finite_workflow_value_remains_an_executable_choice() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    understanding = TurnUnderstanding(
        dialogueAct="answer_open_question",
        goalRelation="open_question",
        intentStructure="single_outcome",
        intentKinds=["callback"],
        answeredQuestionId="question-department",
        resolvedReferences=["workflow_option:sales"],
        resolvedInputs=[
            ResolvedInput(
                field="department",
                value="sales",
                sourceContextId="message:latest",
                sourceText="Sales",
            )
        ],
        ambiguity="none",
        confidence="high",
    )
    context = PlanningContext(
        latest_customer_message="Sales",
        workflow_state={
            "version": 3,
            "activeWorkflow": "callback",
            "stage": "collecting",
            "entities": {"dealershipId": "northstar-stockport"},
            "constraints": {
                "collectedPublicValues": {"dealershipId": "northstar-stockport"},
                "missingPublicFields": ["department", "reason"],
            },
        },
        pending_interaction={
            "kind": "reference_choice",
            "question_id": "question-department",
            "goal_intent": "callback",
            "candidate_references": [
                "workflow_option:sales",
                "workflow_option:service",
                "workflow_option:parts",
            ],
            "expected_fields": ["department"],
        },
        displayed_choices=[
            {
                "position": 1,
                "entityReference": "workflow_option:sales",
                "label": "Sales",
                "inputField": "department",
                "value": "sales",
            }
        ],
        turn_understanding=understanding,
    )

    affordances = PlannerAffordanceEvaluator(catalogue).evaluate(
        CandidateSet((catalogue.get("prepare_callback"),), ()),
        context,
    )

    assert affordances.executable_tool_ids == {"prepare_callback"}
    assert affordances.blocked_tool_ids == set()
    assert affordances.allow_generic_clarification is False


def test_catalogue_declares_generic_reference_bindings_without_policy_name_inference() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    expected = {
        "get_vehicle": (("id", "vehicle"),),
        "get_vehicle_availability": (("id", "vehicle"),),
        "get_offer": (("id", "offer"),),
        "get_dealership": (("id", "dealership"),),
        "get_opening_hours": (("id", "dealership"),),
    }

    assert {name: catalogue.get(name).reference_inputs for name in expected} == expected


@pytest.mark.asyncio
async def test_clear_first_turn_cannot_choose_unjustified_clarification() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    requests: list[dict] = []

    class HoursRetriever:
        def retrieve(self, query, *, tool_limit=8, knowledge_limit=5):
            del query, tool_limit, knowledge_limit
            return CandidateSet(
                (
                    catalogue.get("get_opening_hours"),
                    catalogue.get("list_opening_hours"),
                ),
                (),
            )

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        names = {tool["name"] for tool in body["tools"]}
        if "resolve_conversation_turn" in names:
            return httpx.Response(
                200,
                json=function_call(
                    "resolve_conversation_turn",
                    {
                        "schemaVersion": 1,
                        "dialogueAct": "start_goal",
                        "goalRelation": "new",
                        "intentStructure": "single_outcome",
                        "intentKinds": ["opening_hours"],
                        "resultPresentation": "explicit_request",
                        "answeredQuestionId": None,
                        "resolvedReferences": [],
                        "referenceCandidates": [],
                        "resolvedInputs": [
                            {
                                "field": "day",
                                "value": "Saturday",
                                "sourceContextId": "message:customer-1",
                                "sourceText": "Saturday",
                            }
                        ],
                        "ambiguity": "none",
                        "confidence": "high",
                        "supportingContextIds": ["message:customer-1"],
                    },
                ),
            )
        assert "list_opening_hours" in names
        assert "get_opening_hours" not in names
        assert "ask_conversational_clarification" not in names
        return httpx.Response(
            200,
            json=function_call("list_opening_hours", {"day": "Saturday"}),
        )

    context = PlanningContext(
        latest_customer_message="What are your opening hours this Saturday?",
        context_evidence=[
            {
                "contextId": "message:customer-1",
                "kind": "conversation_turn",
                "relation": "recent_supporting_evidence",
                "relevance": 1.0,
                "role": "user",
                "text": "What are your opening hours this Saturday?",
            }
        ],
    )
    semantic_messages = SemanticMessages(
        [{"role": "user", "content": context.latest_customer_message}],
        context,
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        hosted = HostedLlmProvider(
            provider_url="https://provider.example/v1",
            api_key="shared-key",
            model="hosted-model",
            catalogue=catalogue,
            retriever=HoursRetriever(),
            client=http,
        )
        reply = await hosted.generate_turn(semantic_messages)

    assert len(requests) == 2
    assert [call.name for call in reply.tool_calls] == ["list_opening_hours"]
    assert reply.tool_calls[0].arguments == {"day": "Saturday"}


@pytest.mark.asyncio
async def test_semantic_intent_clarification_is_a_first_class_planning_outcome() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        clarification = next(
            tool for tool in body["tools"] if tool["name"] == "clarify_customer_intent"
        )
        assert set(clarification["parameters"]["required"]) == {
            "question",
            "candidateIntents",
        }
        return httpx.Response(
            200,
            json=function_call(
                "clarify_customer_intent",
                {
                    "question": (
                        "Would you like to make a workshop booking, or send the dealership "
                        "a message?"
                    ),
                    "candidateIntents": ["workshop_booking", "dealership_message"],
                },
            ),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(messages("I need the dealership to sort this"))

    assert reply.text == (
        "Would you like to make a workshop booking, or send the dealership a message?"
    )
    assert reply.response_mode == "clarify"


@pytest.mark.asyncio
async def test_resolved_intent_ambiguity_compiles_to_a_persisted_open_question() -> None:
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if body["tools"][0]["name"] == "resolve_conversation_turn":
            return httpx.Response(
                200,
                json=function_call(
                    "resolve_conversation_turn",
                    {
                        "schemaVersion": 1,
                        "dialogueAct": "start_goal",
                        "goalRelation": "new",
                        "intentStructure": "uncertain_intent",
                        "intentKinds": ["service_discovery", "workshop_booking"],
                        "answeredQuestionId": None,
                        "resolvedReferences": [],
                        "referenceCandidates": [],
                        "resolvedInputs": [],
                        "ambiguity": "intent",
                        "confidence": "medium",
                        "supportingContextIds": [],
                    },
                ),
            )
        assert [tool["name"] for tool in body["tools"]] == ["clarify_customer_intent"]
        assert body["tools"][0]["parameters"]["required"] == ["question"]
        return httpx.Response(
            200,
            json=function_call(
                "clarify_customer_intent",
                {
                    "question": (
                        "Would you like to see servicing options, or book a workshop visit?"
                    )
                },
            ),
        )

    catalogue = UnifiedToolCatalog(NeverExecute())
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        hosted = HostedLlmProvider(
            provider_url="https://provider.example/v1",
            api_key="shared-key",
            model="hosted-model",
            catalogue=catalogue,
            retriever=FixedRetriever(catalogue),
            client=http,
        )
        reply = await hosted.generate_turn(messages("servicing"))

    assert len(requests) == 2
    assert reply.response_mode == "clarify"
    assert reply.interaction is not None
    assert reply.interaction.kind == "intent_choice"
    assert reply.interaction.question_id is not None
    assert reply.interaction.candidate_intents == ["service_discovery", "workshop_booking"]


@pytest.mark.asyncio
async def test_large_authoritative_reference_ambiguity_asks_one_grounded_narrowing_question() -> (
    None
):
    requests = []
    references = [f"vehicle:veh-{index:03d}" for index in range(1, 13)]

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if body["tools"][0]["name"] == "resolve_conversation_turn":
            return httpx.Response(
                200,
                json=function_call(
                    "resolve_conversation_turn",
                    {
                        "schemaVersion": 1,
                        "dialogueAct": "start_goal",
                        "goalRelation": "new",
                        "intentStructure": "single_outcome",
                        "intentKinds": ["business_information"],
                        "resultPresentation": "default",
                        "answeredQuestionId": None,
                        "resolvedReferences": [],
                        "referenceCandidates": references,
                        "resolvedInputs": [],
                        "requestedInputChanges": [],
                        "ambiguity": "reference",
                        "confidence": "medium",
                        "supportingContextIds": [],
                    },
                ),
            )
        assert [tool["name"] for tool in body["tools"]] == ["clarify_customer_reference"]
        assert body["tools"][0]["parameters"]["required"] == ["question"]
        assert body["tool_choice"] == {
            "type": "function",
            "name": "clarify_customer_reference",
        }
        return httpx.Response(
            200,
            json=function_call(
                "clarify_customer_reference",
                {
                    "question": (
                        "Which vehicle are you asking about? You can give me its make and model."
                    )
                },
            ),
        )

    catalogue = UnifiedToolCatalog(NeverExecute())
    page_vehicles = [
        {
            "position": index,
            "vehicleId": f"veh-{index:03d}",
            "label": f"Vehicle {index}",
            "make": "Northstar",
            "model": f"Model {index}",
        }
        for index in range(1, 13)
    ]
    contextual_messages = SemanticMessages(
        [{"role": "user", "content": "Can I get finance information for this vehicle?"}],
        PlanningContext(
            latest_customer_message="Can I get finance information for this vehicle?",
            page_vehicles=page_vehicles,
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        hosted = HostedLlmProvider(
            provider_url="https://provider.example/v1",
            api_key="shared-key",
            model="hosted-model",
            catalogue=catalogue,
            retriever=FixedRetriever(catalogue),
            client=http,
        )
        reply = await hosted.generate_turn(contextual_messages)

    assert len(requests) == 2
    assert reply.response_mode == "clarify"
    assert reply.interaction is not None
    assert reply.interaction.kind == "reference_choice"
    assert reply.interaction.goal_intent == "business_information"
    assert reply.interaction.candidate_references == references
    assert reply.text == ("Which vehicle are you asking about? You can give me its make and model.")


@pytest.mark.asyncio
async def test_contextual_reference_answer_continues_the_original_information_request() -> None:
    requests: list[dict] = []
    catalogue = UnifiedToolCatalog(NeverExecute())

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        names = {tool["name"] for tool in body["tools"]}
        if "resolve_conversation_turn" in names:
            return httpx.Response(
                200,
                json=function_call(
                    "resolve_conversation_turn",
                    {
                        "schemaVersion": 1,
                        "dialogueAct": "answer_open_question",
                        "goalRelation": "open_question",
                        "intentStructure": "single_outcome",
                        "intentKinds": ["business_information"],
                        "resultPresentation": "default",
                        "answeredQuestionId": "question-vehicle-context",
                        "resolvedReferences": ["vehicle:veh-006"],
                        "referenceCandidates": [],
                        "resolvedInputs": [],
                        "requestedInputChanges": [],
                        "ambiguity": "none",
                        "confidence": "high",
                        "supportingContextIds": [],
                    },
                ),
            )
        assert "get_business_information" in names
        assert "ask_conversational_clarification" not in names
        return httpx.Response(
            200,
            json=function_call(
                "get_business_information",
                {
                    "topic": "finance",
                    "question": "Can I get finance information for this vehicle?",
                },
            ),
        )

    context = PlanningContext(
        latest_customer_message="the 2025 Blazing Blue MINI Countryman",
        pending_interaction={
            "kind": "reference_choice",
            "question_id": "question-vehicle-context",
            "goal_intent": "business_information",
            "candidate_references": ["vehicle:veh-001", "vehicle:veh-006"],
            "expected_fields": [],
        },
        displayed_vehicles=[
            {"vehicleId": "veh-001", "make": "MINI", "model": "Cooper"},
            {"vehicleId": "veh-006", "make": "MINI", "model": "Countryman"},
        ],
        context_evidence=[
            {
                "contextId": "message:customer-original",
                "kind": "conversation_turn",
                "relation": "open_question_origin",
                "relevance": 1.0,
                "role": "user",
                "text": "Can I get finance information for this vehicle?",
            }
        ],
    )
    semantic_messages = SemanticMessages(
        [
            {"role": "user", "content": "Can I get finance information for this vehicle?"},
            {
                "role": "assistant",
                "content": "Which vehicle are you asking about?",
            },
            {"role": "user", "content": context.latest_customer_message},
        ],
        context,
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        hosted = HostedLlmProvider(
            provider_url="https://provider.example/v1",
            api_key="shared-key",
            model="hosted-model",
            catalogue=catalogue,
            retriever=FixedRetriever(catalogue),
            client=http,
        )
        reply = await hosted.generate_turn(semantic_messages)

    assert len(requests) == 2
    assert [call.name for call in reply.tool_calls] == ["get_business_information"]
    assert reply.tool_calls[0].arguments == {
        "topic": "finance",
        "question": "Can I get finance information for this vehicle?",
    }


@pytest.mark.asyncio
async def test_entity_reference_clarification_is_a_first_class_planning_outcome() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        clarification = next(
            tool for tool in body["tools"] if tool["name"] == "clarify_customer_reference"
        )
        assert set(
            clarification["parameters"]["properties"]["candidateReferences"]["items"]["enum"]
        ) == {"vehicle:veh-001", "vehicle:veh-025"}
        return httpx.Response(
            200,
            json=function_call(
                "clarify_customer_reference",
                {
                    "question": "Which of the displayed BMW 1 Series options do you mean?",
                    "candidateReferences": ["vehicle:veh-001", "vehicle:veh-025"],
                    "continuationIntent": "vehicle_search",
                },
            ),
        )

    contextual_messages = SemanticMessages(
        [{"role": "user", "content": "Book the BMW 1 Series"}],
        PlanningContext(
            latest_customer_message="Book the BMW 1 Series",
            displayed_vehicles=[
                {"vehicleId": "veh-001", "position": 1, "model": "1 Series"},
                {"vehicleId": "veh-025", "position": 2, "model": "1 Series"},
            ],
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(contextual_messages)

    assert reply.response_mode == "clarify"
    assert reply.text == "Which of the displayed BMW 1 Series options do you mean?"
    assert reply.tool_calls == []
    assert reply.interaction is not None
    assert reply.interaction.kind == "reference_choice"
    assert reply.interaction.goal_intent == "vehicle_search"
    assert reply.interaction.candidate_references == [
        "vehicle:veh-001",
        "vehicle:veh-025",
    ]


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
        PlanningContext(latest_customer_message=text),
    )


def test_retrieval_query_includes_bounded_active_operation_state() -> None:
    query = _retrieval_query(
        PlanningContext(
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
        PlanningContext(
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


@pytest.mark.asyncio
async def test_planner_can_use_a_uniquely_resolved_sold_page_vehicle() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["parallel_tool_calls"] is True
        return httpx.Response(
            200,
            json=function_call("get_vehicle_availability", {"id": "veh-013"}),
        )

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
        PlanningContext(
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
        PlanningContext(
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


def test_candidate_retrieval_unions_each_resolved_intent_without_phrase_splitting() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())

    class ClauseSensitiveRetriever:
        def retrieve(self, query, *, tool_limit=8, knowledge_limit=5):
            del tool_limit, knowledge_limit
            if query == "vehicle_search":
                names = ("search_vehicles",)
            elif query == "opening_hours":
                names = ("list_opening_hours",)
            else:
                names = ("list_dealerships",)
            return CandidateSet(tuple(catalogue.get(name) for name in names), ())

    context = PlanningContext(
        latest_customer_message=(
            "Do you have electric cars under £35,000, and is Bolton open on Saturday?"
        ),
        turn_understanding=TurnUnderstanding(
            dialogueAct="start_goal",
            goalRelation="new",
            intentKinds=["vehicle_search", "opening_hours"],
            ambiguity="none",
            confidence="high",
        ),
    )
    retrieved = _retrieve_candidates(ClauseSensitiveRetriever(), context)
    candidates = _semantic_candidates(
        retrieved,
        context,
        catalogue,
    )

    assert [tool.id for tool in retrieved.tools] == [
        "list_dealerships",
        "search_vehicles",
        "list_opening_hours",
    ]
    semantic_ids = [tool.id for tool in candidates.tools]
    assert {"search_vehicles", "list_opening_hours"}.issubset(semantic_ids)
    assert "list_dealerships" not in semantic_ids


def function_call(name: str, arguments: dict, call_id: str = "call-1") -> dict:
    if name in {tool.id for tool in UnifiedToolCatalog(NeverExecute()).planner_tools()}:
        arguments = {**arguments, "intentKind": primary_intent(name)}
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


@pytest.mark.asyncio
async def test_semantic_resolution_owns_free_form_answers_before_business_planning() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    requests: list[dict] = []

    class TestDriveOnlyRetriever:
        def retrieve(self, query, *, tool_limit=8, knowledge_limit=5):
            del query, tool_limit, knowledge_limit
            return CandidateSet((catalogue.get("prepare_test_drive"),), ())

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        names = {tool["name"] for tool in body["tools"]}
        if "resolve_conversation_turn" in names:
            envelope = json.loads(body["input"][0]["content"])
            assert envelope["openQuestion"]["goal_intent"] == "test_drive"
            dialogue_acts = body["tools"][0]["parameters"]["properties"]["dialogueAct"][
                "enum"
            ]
            if "answer_open_question" not in dialogue_acts:
                return httpx.Response(
                    200,
                    json=function_call(
                        "resolve_conversation_turn",
                        {
                            "schemaVersion": 1,
                            "dialogueAct": "continue_goal",
                            "goalRelation": "active",
                            "intentStructure": "single_outcome",
                            "intentKinds": ["test_drive"],
                            "answeredQuestionId": None,
                            "resolvedReferences": ["vehicle:veh-049"],
                            "referenceCandidates": [],
                            "resolvedInputs": [],
                            "ambiguity": "none",
                            "confidence": "high",
                            "supportingContextIds": ["question-current-choice"],
                        },
                    ),
                )
            return httpx.Response(
                200,
                json=function_call(
                    "resolve_conversation_turn",
                    {
                        "schemaVersion": 1,
                        "dialogueAct": "answer_open_question",
                        "goalRelation": "open_question",
                        "intentStructure": "single_outcome",
                        "intentKinds": ["test_drive"],
                        "answeredQuestionId": "question-current-choice",
                        "resolvedReferences": ["vehicle:veh-049"],
                        "referenceCandidates": [],
                        "resolvedInputs": [],
                        "ambiguity": "none",
                        "confidence": "high",
                        "supportingContextIds": ["question-current-choice"],
                    },
                ),
            )
        assert {"prepare_test_drive", "respond_socially"}.issubset(names)
        assert "prepare_sales_enquiry" not in names
        assert "vehicle:veh-049" in body["input"][-1]["content"]
        return httpx.Response(
            200,
            json=function_call("prepare_test_drive", {"vehicleId": "veh-049"}),
        )

    context = PlanningContext(
        latest_customer_message="the newest one",
        workflow_state={"activeWorkflow": "test_drive"},
        pending_interaction={
            "version": 1,
            "kind": "reference_choice",
            "prompt": "Which vehicle would you like?",
            "question_id": "question-current-choice",
            "goal_intent": "test_drive",
            "candidate_references": [
                "vehicle:veh-001",
                "vehicle:veh-025",
                "vehicle:veh-049",
            ],
            "candidate_intents": [],
            "actions": [],
            "blocking_fields": [],
            "blocking_preconditions": [],
        },
        displayed_vehicles=[
            {"position": 1, "vehicleId": "veh-001", "year": 2020},
            {"position": 2, "vehicleId": "veh-025", "year": 2023},
            {"position": 3, "vehicleId": "veh-049", "year": 2026},
        ],
        context_evidence=[
            {
                "contextId": "question-current-choice",
                "kind": "open_question",
                "relation": "expects_latest_answer",
                "relevance": 1.0,
            }
        ],
    )
    semantic_messages = SemanticMessages([{"role": "user", "content": "the newest one"}], context)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        hosted = HostedLlmProvider(
            provider_url="https://provider.example/v1",
            api_key="shared-key",
            model="hosted-model",
            catalogue=catalogue,
            retriever=TestDriveOnlyRetriever(),
            client=http,
        )
        reply = await hosted.generate_turn(semantic_messages)

    assert len(requests) == 3
    assert reply.turn_understanding is not None
    assert reply.turn_understanding.resolvedReferences == ["vehicle:veh-049"]
    assert [(call.name, call.arguments) for call in reply.tool_calls] == [
        ("prepare_test_drive", {"vehicleId": "veh-049"})
    ]


@pytest.mark.asyncio
async def test_open_service_choice_price_question_is_disambiguated_as_information() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    requests: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        names = {tool["name"] for tool in body["tools"]}
        if "resolve_conversation_turn" in names:
            envelope = json.loads(body["input"][0]["content"])
            assert envelope["openQuestion"]["goal_intent"] == "workshop_booking"
            dialogue_acts = body["tools"][0]["parameters"]["properties"]["dialogueAct"][
                "enum"
            ]
            if "answer_open_question" in dialogue_acts:
                # Reproduce the hosted-model mistake from the reported conversation: a valid
                # service reference is incorrectly treated as selection of that service.
                return httpx.Response(
                    200,
                    json=function_call(
                        "resolve_conversation_turn",
                        {
                            "schemaVersion": 1,
                            "dialogueAct": "answer_open_question",
                            "goalRelation": "open_question",
                            "intentStructure": "single_outcome",
                            "intentKinds": ["workshop_booking"],
                            "resultPresentation": "default",
                            "answeredQuestionId": "question-service",
                            "resolvedReferences": ["service:mot"],
                            "referenceCandidates": [],
                            "resolvedInputs": [],
                            "requestedInputChanges": [],
                            "ambiguity": "none",
                            "confidence": "high",
                            "supportingContextIds": ["question-service", "message:cost"],
                        },
                    ),
                )
            assert "second semantic outcome pass" in body["instructions"]
            return httpx.Response(
                200,
                json=function_call(
                    "resolve_conversation_turn",
                    {
                        "schemaVersion": 1,
                        "dialogueAct": "interrupt_with_information_request",
                        "goalRelation": "unrelated",
                        "intentStructure": "single_outcome",
                        "intentKinds": ["service_detail"],
                        "resultPresentation": "explicit_request",
                        "answeredQuestionId": None,
                        "resolvedReferences": ["service:mot"],
                        "referenceCandidates": [],
                        "resolvedInputs": [],
                        "requestedInputChanges": [],
                        "ambiguity": "none",
                        "confidence": "high",
                        "supportingContextIds": ["message:cost"],
                    },
                ),
            )
        assert "get_service_information" in names
        assert "refine_workshop_slots" not in names
        return httpx.Response(
            200,
            json=function_call(
                "get_service_information",
                {"serviceTypeId": "mot"},
            ),
        )

    context = PlanningContext(
        latest_customer_message="what is the cost of mot?",
        workflow_state={
            "version": 3,
            "activeWorkflow": "workshop_booking",
            "stage": "choosing_service",
            "entities": {},
            "constraints": {"missingPublicFields": ["serviceTypeId"]},
        },
        pending_interaction={
            "version": 1,
            "kind": "reference_choice",
            "prompt": "Which service would you like to book?",
            "question_id": "question-service",
            "goal_intent": "workshop_booking",
            "candidate_references": ["service:mot", "service:full-service"],
            "candidate_intents": [],
            "expected_fields": ["serviceTypeId"],
        },
        displayed_choices=[
            {"position": 1, "entityReference": "service:mot", "label": "MOT"},
            {
                "position": 2,
                "entityReference": "service:full-service",
                "label": "Full service",
            },
        ],
        context_evidence=[
            {
                "contextId": "message:cost",
                "kind": "conversation_turn",
                "relation": "latest_customer_message",
                "relevance": 1.0,
                "role": "user",
                "text": "what is the cost of mot?",
            },
            {
                "contextId": "question-service",
                "kind": "open_question",
                "relation": "expects_latest_answer",
                "relevance": 1.0,
            },
        ],
    )
    semantic_messages = SemanticMessages(
        [{"role": "user", "content": "what is the cost of mot?"}],
        context,
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        hosted = HostedLlmProvider(
            provider_url="https://provider.example/v1",
            api_key="shared-key",
            model="hosted-model",
            catalogue=catalogue,
            retriever=FixedRetriever(catalogue),
            client=http,
        )
        reply = await hosted.generate_turn(semantic_messages)

    assert len(requests) == 3
    assert reply.turn_understanding is not None
    assert reply.turn_understanding.dialogueAct == "interrupt_with_information_request"
    assert reply.turn_understanding.intentKinds == ["service_detail"]
    assert [(call.name, call.arguments) for call in reply.tool_calls] == [
        ("get_service_information", {"serviceTypeId": "mot"})
    ]


@pytest.mark.asyncio
async def test_planner_emits_a_schema_validated_native_tool_call_without_a_second_reviewer() -> (
    None
):
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        assert body["store"] is False
        assert request.headers["Authorization"] == "Bearer shared-key"
        assert "api-key" not in request.headers
        assert body["parallel_tool_calls"] is True
        names = {tool["name"] for tool in body["tools"]}
        assert {
            "search_vehicles",
            "answer_from_knowledge",
            "respond_socially",
        }.issubset(names)
        return httpx.Response(200, json=function_call("search_vehicles", {"make": "Volvo"}))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(messages("Show me Volvos"))

    assert reply.tool_calls[0].name == "search_vehicles"
    assert reply.tool_calls[0].arguments == {"make": "Volvo"}
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_invalid_hosted_plan_is_not_replaced_by_phrase_fallback() -> None:
    calls = []
    catalogue = UnifiedToolCatalog(NeverExecute())

    class TestDriveRetriever:
        def retrieve(self, query, *, tool_limit=8, knowledge_limit=5):
            del query, tool_limit, knowledge_limit
            return CandidateSet((catalogue.get("prepare_test_drive"),), ())

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"output": []})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        hosted = HostedLlmProvider(
            provider_url="https://provider.example/v1",
            api_key="shared-key",
            model="hosted-model",
            catalogue=catalogue,
            retriever=TestDriveRetriever(),
            client=http,
            semantic_resolution=False,
        )
        with pytest.raises(PlanningValidationError):
            await hosted.generate_turn(messages("Book a test drive for a BMW"))

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_provider_500_is_not_reported_as_a_planning_failure() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        with pytest.raises(ProviderUnavailableError, match="transport unavailable"):
            await provider(http).generate_turn(messages("Book a test drive"))

    assert calls == 3


@pytest.mark.asyncio
async def test_vehicle_appointment_plan_derives_single_intent_application_side() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())

    class VehicleAppointmentRetriever:
        def retrieve(self, query, *, tool_limit=8, knowledge_limit=5):
            del query, tool_limit, knowledge_limit
            return CandidateSet((catalogue.get("prepare_test_drive"),), ())

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        tool = next(item for item in body["tools"] if item["name"] == "prepare_test_drive")
        assert "intentKind" not in tool["parameters"]["properties"]
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "appointment-1",
                        "name": "prepare_test_drive",
                        "arguments": json.dumps({"vehicleId": "veh-014"}),
                    }
                ]
            },
        )

    context = PlanningContext(
        latest_customer_message="book appintment for 3 Series",
        workflow_state={
            "version": 3,
            "activeWorkflow": "vehicle_comparison",
            "stage": "viewing",
            "entities": {"vehicleIds": ["veh-014", "veh-042", "veh-049"]},
        },
        displayed_vehicles=[
            {"vehicleId": "veh-014", "make": "BMW", "model": "3 Series"},
            {"vehicleId": "veh-042", "make": "MINI", "model": "Countryman"},
            {"vehicleId": "veh-049", "make": "BMW", "model": "1 Series"},
        ],
    )
    semantic_messages = SemanticMessages(
        [{"role": "user", "content": context.latest_customer_message}],
        context,
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        hosted = HostedLlmProvider(
            provider_url="https://provider.example/v1",
            api_key="shared-key",
            model="hosted-model",
            catalogue=catalogue,
            retriever=VehicleAppointmentRetriever(),
            client=http,
            semantic_resolution=False,
        )
        reply = await hosted.generate_turn(semantic_messages)

    assert [(call.name, call.intent_kind, call.arguments) for call in reply.tool_calls] == [
        ("prepare_test_drive", "test_drive", {"vehicleId": "veh-014"})
    ]


@pytest.mark.asyncio
async def test_planner_can_combine_approved_knowledge_with_live_reads() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["parallel_tool_calls"] is True
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "knowledge-1",
                        "name": "answer_from_knowledge",
                        "arguments": json.dumps({"citationIds": ["customer.pch"]}),
                    },
                    {
                        "type": "function_call",
                        "call_id": "offers-1",
                        "name": "list_offers",
                        "arguments": json.dumps(
                            {"productType": "PCH", "intentKind": "offer_discovery"}
                        ),
                    },
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(
            messages("What is PCH, and show me the current PCH offers")
        )

    assert [(call.name, call.arguments) for call in reply.tool_calls] == [
        ("list_offers", {"productType": "PCH"})
    ]
    assert reply.text == ""
    assert reply.approved_content == (
        {
            "id": "customer.pch",
            "title": "PCH",
            "text": "PCH means Personal Contract Hire.",
            "source": "docs/CUSTOMER-KNOWLEDGE.md#PCH",
        },
    )


@pytest.mark.asyncio
async def test_grounded_composition_is_a_separate_strict_schema_call_after_tools() -> None:
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        assert body["parallel_tool_calls"] is False
        assert body["tools"][0]["name"] == "compose_grounded_response"
        envelope = json.loads(body["input"][0]["content"])
        assert envelope["trustedToolResults"][0]["resultId"] == "result-one"
        assert envelope["applicationContinuation"] == {
            "kind": "single_action",
            "prompt": "Would you like help contacting a dealership?",
        }
        return httpx.Response(
            200,
            json=function_call(
                "compose_grounded_response",
                {
                    "schemaVersion": 1,
                    "messages": [
                        {
                            "purpose": "answer",
                            "segments": [{"type": "text", "text": "Here are the results."}],
                        }
                    ],
                    "cardReferences": ["card:result-one:vehicle_preview"],
                    "suggestionReferences": [],
                    "linkReferences": [],
                },
            ),
        )

    semantic_messages = SemanticMessages(
        [{"role": "user", "content": "Show me a BMW"}],
        PlanningContext(
            latest_customer_message="Show me a BMW",
            trusted_tool_facts={
                "results": [
                    {
                        "resultId": "result-one",
                        "availableCards": [{"reference": "card:result-one:vehicle_preview"}],
                    }
                ],
                "applicationContinuation": {
                    "kind": "single_action",
                    "prompt": "Would you like help contacting a dealership?",
                },
            },
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(semantic_messages)

    assert reply.response_draft is not None
    assert reply.response_draft.cardReferences == ["card:result-one:vehicle_preview"]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_hosted_refinement_can_express_a_negative_vehicle_preference() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["parallel_tool_calls"] is True
        refinement = next(tool for tool in body["tools"] if tool["name"] == "refine_vehicle_search")
        assert "excludedMakes" in refinement["parameters"]["properties"]
        return httpx.Response(
            200,
            json=function_call(
                "refine_vehicle_search",
                {"excludedMakes": ["Land Rover"]},
            ),
        )

    context = PlanningContext(
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
    ("recent_messages", "candidate_arguments"),
    [
        (
            [],
            {"message": "Send a message to the dealership"},
        ),
        (
            ["Please ask whether my service plan covers tyres."],
            {"message": "Please ask whether my service plan covers tyres."},
        ),
    ],
)
async def test_planner_proposal_is_not_model_rewritten_after_schema_validation(
    recent_messages, candidate_arguments
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["parallel_tool_calls"] is True
        return httpx.Response(
            200,
            json=function_call("prepare_dealership_message", candidate_arguments),
        )

    semantic_messages = SemanticMessages(
        [{"role": "user", "content": "Send a message to the dealership"}],
        PlanningContext(
            latest_customer_message="Send a message to the dealership",
            recent_customer_messages=recent_messages,
        ),
    )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(semantic_messages)

    assert reply.tool_calls[0].name == "prepare_dealership_message"
    assert reply.tool_calls[0].arguments == candidate_arguments


@pytest.mark.asyncio
async def test_schema_valid_social_response_is_not_replaced_by_a_second_model() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["parallel_tool_calls"] is True
        return httpx.Response(
            200,
            json=function_call(
                "respond_socially",
                {"act": "capabilities"},
            ),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(messages("What is the cost of tyre fitting?"))

    assert reply.tool_calls == []
    assert "find vehicles" in reply.text


@pytest.mark.asyncio
async def test_likely_compound_request_gets_one_semantic_completeness_recheck() -> None:
    planner_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal planner_calls
        body = json.loads(request.content)
        if "resolve_conversation_turn" in {tool["name"] for tool in body["tools"]}:
            return httpx.Response(
                200,
                json=function_call(
                    "resolve_conversation_turn",
                    {
                        "schemaVersion": 1,
                        "dialogueAct": "start_goal",
                        "goalRelation": "new",
                        "intentStructure": "compound_outcomes",
                        "intentKinds": ["vehicle_search", "opening_hours"],
                        "answeredQuestionId": None,
                        "resolvedReferences": [],
                        "referenceCandidates": [],
                        "resolvedInputs": [],
                        "ambiguity": "none",
                        "confidence": "high",
                        "supportingContextIds": [],
                    },
                ),
            )
        planner_calls += 1
        if planner_calls == 1:
            return httpx.Response(
                200,
                json=function_call(
                    "list_opening_hours",
                    {"town": "Bolton", "day": "Saturday"},
                ),
            )
        assert "compound_request_incomplete" in body["input"][-1]["content"]
        assert "list_opening_hours" not in {tool["name"] for tool in body["tools"]}
        return httpx.Response(
            200,
            json=function_call(
                "search_vehicles",
                {"fuelType": "Electric", "maxPricePence": 3_500_000},
                "vehicle-1",
            ),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        catalogue = UnifiedToolCatalog(NeverExecute())
        hosted = HostedLlmProvider(
            provider_url="https://provider.example/v1",
            api_key="shared-key",
            model="hosted-model",
            catalogue=catalogue,
            retriever=FixedRetriever(catalogue),
            client=http,
        )
        reply = await hosted.generate_turn(
            messages("Do you have electric cars under £35,000, and is Bolton open on Saturday?")
        )

    assert [call.name for call in reply.tool_calls] == [
        "list_opening_hours",
        "search_vehicles",
    ]
    assert planner_calls == 2


def test_compound_repair_candidates_exclude_every_tool_for_a_fulfilled_intent() -> None:
    catalogue = UnifiedToolCatalog(NeverExecute())
    candidates = CandidateSet(
        (
            catalogue.get("get_opening_hours"),
            catalogue.get("list_opening_hours"),
            catalogue.get("search_vehicles"),
        ),
        (),
    )
    seed = TurnProposal(
        [ToolCall("call-hours", "list_opening_hours", {}, intent_kind="opening_hours")]
    )
    context = PlanningContext(
        latest_customer_message="Show me electric cars and Saturday opening hours",
        turn_understanding=TurnUnderstanding(
            dialogueAct="start_goal",
            goalRelation="new",
            intentStructure="compound_outcomes",
            intentKinds=["vehicle_search", "opening_hours"],
            ambiguity="none",
            confidence="high",
        ),
    )

    remaining = _remaining_compound_candidates(candidates, seed, context)

    assert [tool.id for tool in remaining.tools] == ["search_vehicles"]


@pytest.mark.asyncio
async def test_customer_answer_must_use_retrieved_citation() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["parallel_tool_calls"] is True
        return httpx.Response(
            200,
            json=function_call(
                "answer_from_knowledge",
                {"citationIds": ["customer.pch"]},
            ),
        )

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
        assert body["parallel_tool_calls"] is True
        return httpx.Response(
            200,
            json=function_call(
                "answer_from_knowledge",
                {"citationIds": ["customer.pcp", "customer.pch"]},
            ),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(messages("How does vehicle finance work?"))

    assert reply.text == (
        "- PCP: PCP means Personal Contract Purchase.\n- PCH: PCH means Personal Contract Hire."
    )
    assert reply.citation_ids == ("customer.pcp", "customer.pch")


@pytest.mark.asyncio
async def test_knowledge_answer_carries_its_declared_follow_up_action() -> None:
    citation = "customer.vehicle_pickup_and_collection_policy"

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["parallel_tool_calls"] is True
        return httpx.Response(
            200,
            json=function_call(
                "answer_from_knowledge",
                {"citationIds": [citation]},
            ),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(messages("Will you pick up my car?"))

    assert reply.citation_ids == (citation,)
    assert reply.interaction is not None
    assert reply.interaction.kind == "single_action"
    assert reply.interaction.actions == [{"type": "show_dealership_contact_options"}]
    assert reply.interaction.handoff == ActionHandoff(
        topic="part_exchange",
        customerReason="Will you pick up my car?",
    )


@pytest.mark.asyncio
async def test_planner_keeps_a_vague_finance_request_grounded_in_retrieved_knowledge() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["parallel_tool_calls"] is True
        assert "TurnUnderstanding" in body["instructions"]
        return httpx.Response(
            200,
            json=function_call(
                "answer_from_knowledge",
                {"citationIds": ["customer.pcp", "customer.pch"]},
            ),
        )

    context = PlanningContext(
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

    assert reply.text == (
        "- PCP: PCP means Personal Contract Purchase.\n- PCH: PCH means Personal Contract Hire."
    )
    assert reply.citation_ids == ("customer.pcp", "customer.pch")


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
        assert body["parallel_tool_calls"] is True
        names = {tool["name"] for tool in body["tools"]}
        assert "accept_pending_interaction" in names
        assert "decline_pending_interaction" in names
        return httpx.Response(
            200,
            json=function_call("accept_pending_interaction", {}),
        )

    semantic_messages = SemanticMessages(
        [{"role": "user", "content": "That sounds good, please continue."}],
        PlanningContext(
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
        with pytest.raises(PlanningValidationError, match="valid_typed_customer_turn_proposal"):
            await provider(http).generate_turn(messages("Show me cars under £35,000."))


@pytest.mark.asyncio
async def test_planner_does_not_depend_on_a_second_model_request() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        assert body["parallel_tool_calls"] is True
        return httpx.Response(200, json=function_call("search_vehicles", {}))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(messages("show me cars"))

    assert reply.tool_calls[0].name == "search_vehicles"
    assert calls == 1


@pytest.mark.asyncio
async def test_planner_success_has_one_bounded_request() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        assert body["parallel_tool_calls"] is True
        return httpx.Response(200, json=function_call("search_vehicles", {}))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(messages("show me cars"))

    assert reply.tool_calls[0].name == "search_vehicles"
    assert calls == 1


@pytest.mark.asyncio
async def test_planner_recovers_once_from_a_malformed_response() -> None:
    planner_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal planner_calls
        body = json.loads(request.content)
        assert body["parallel_tool_calls"] is True
        planner_calls += 1
        if planner_calls == 1:
            return httpx.Response(200, json={"output": []})
        return httpx.Response(200, json=function_call("search_vehicles", {}))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(messages("show me cars"))

    assert reply.tool_calls[0].name == "search_vehicles"
    assert planner_calls == 2


@pytest.mark.asyncio
async def test_test_drive_clarification_is_replanned_as_live_vehicle_discovery() -> None:
    planner_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal planner_calls
        body = json.loads(request.content)
        planner_calls += 1
        if planner_calls == 1:
            return httpx.Response(
                200,
                json=function_call(
                    "ask_conversational_clarification",
                    {
                        "question": (
                            "Did you mean BMW? If so, which BMW on the page are you interested "
                            "in for a test drive?"
                        ),
                        "blockingTool": "search_vehicles",
                        "blockingFields": [],
                        "blockingPreconditions": [],
                    },
                ),
            )
        assert body["input"][-1] == {
            "role": "developer",
            "content": (
                "The previous proposal violated a deterministic conversation contract. "
                "Re-plan the same customer request. Failure: "
                "clarification_requires_a_real_required_field_or_declared_precondition."
            ),
        }
        return httpx.Response(
            200,
            json=function_call("search_vehicles", {"make": "BMW"}),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(messages("Is test drive available for mnw"))

    assert [(call.name, call.arguments) for call in reply.tool_calls] == [
        ("search_vehicles", {"make": "BMW"})
    ]
    assert planner_calls == 2


@pytest.mark.asyncio
async def test_colour_preference_is_replanned_as_full_inventory_search() -> None:
    planner_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal planner_calls
        body = json.loads(request.content)
        planner_calls += 1
        if planner_calls == 1:
            return httpx.Response(
                200,
                json=function_call(
                    "ask_conversational_clarification",
                    {
                        "question": (
                            "Do you want black cars from the current results, "
                            "or from all available stock?"
                        ),
                        "blockingTool": "search_vehicles",
                        "blockingFields": ["q"],
                        "blockingPreconditions": [],
                    },
                ),
            )
        assert (
            "clarification_requires_a_real_required_field_or_declared_precondition"
            in body["input"][-1]["content"]
        )
        return httpx.Response(
            200,
            json=function_call("search_vehicles", {"q": "black"}),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(messages("I want black cars"))

    assert [(call.name, call.arguments) for call in reply.tool_calls] == [
        ("search_vehicles", {"q": "black"})
    ]
    assert planner_calls == 2


@pytest.mark.asyncio
async def test_direct_clarification_cannot_expose_page_context() -> None:
    planner_calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal planner_calls
        body = json.loads(request.content)
        planner_calls += 1
        if planner_calls == 1:
            return httpx.Response(
                200,
                json=function_call(
                    "ask_conversational_clarification",
                    {
                        "question": "Which option on the page did you mean?",
                        "blockingTool": "get_vehicle_availability",
                        "blockingFields": ["id"],
                        "blockingPreconditions": ["trusted_vehicle_id"],
                    },
                ),
            )
        assert "keep_page_and_application_context_internal" in body["input"][-1]["content"]
        return httpx.Response(
            200,
            json=function_call(
                "ask_conversational_clarification",
                {
                    "question": "Which option did you mean?",
                    "blockingTool": "get_vehicle_availability",
                    "blockingFields": ["id"],
                    "blockingPreconditions": ["trusted_vehicle_id"],
                },
            ),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(messages("Tell me more about it"))

    assert reply.text == "Which option did you mean?"
    assert planner_calls == 2


@pytest.mark.asyncio
async def test_optional_workshop_arguments_are_left_for_the_policy_gate() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["parallel_tool_calls"] is True
        return httpx.Response(200, json=function_call("list_workshop_slots", {}))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        reply = await provider(http).generate_turn(
            SemanticMessages(
                [{"role": "user", "content": "I want tyre"}],
                PlanningContext(
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

    assert [(call.name, call.arguments) for call in reply.tool_calls] == [
        ("list_workshop_slots", {})
    ]


@pytest.mark.asyncio
async def test_unknown_planner_function_is_rejected() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=function_call("delete_everything", {}))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://provider.example/v1"
    ) as http:
        with pytest.raises(PlanningValidationError, match="valid_typed_customer_turn_proposal"):
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
