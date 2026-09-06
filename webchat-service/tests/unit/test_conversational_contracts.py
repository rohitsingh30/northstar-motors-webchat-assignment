import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from webchat.api.models import OpenVehicleDetailAction
from webchat.domain.capabilities import CapabilityRegistry
from webchat.domain.conversation_state import (
    ConversationState,
    ConversationStateReducer,
    PendingInteractionState,
    StateEvent,
    TrustedEntity,
)
from webchat.domain.interactions import PendingInteraction
from webchat.domain.protected_interactions import explicit_decision, resolve_latest
from webchat.integrations.contracts import PlanningContext
from webchat.orchestration.contracts.facts import (
    AlternativeOffer,
    AtomicFact,
    AvailableCard,
    AvailableCollection,
    AvailableLink,
    AvailableSuggestion,
    ResponseObligation,
    ToolResultEnvelope,
)
from webchat.orchestration.contracts.plan import AgentPlan, PlanIntent
from webchat.orchestration.contracts.presentation import CollectionPresentation
from webchat.orchestration.contracts.response import (
    BulletSegment,
    FactSegment,
    GroundedMessageDraft,
    GroundedResponseDraft,
    LinkSegment,
    QuickReplyDraft,
    TextSegment,
)
from webchat.orchestration.contracts.semantics import ResolvedInput, TurnUnderstanding
from webchat.orchestration.fact_normalization import FactNormalizer
from webchat.orchestration.grounding import GroundingValidator, normalize_optional_suggestions
from webchat.orchestration.orchestrator import (
    _apply_alternative_disclosure,
    _dialogue_question_disposition,
    _normalize_persisted_message_completeness,
    _retained_question_choice_view,
    _workflow_dialogue_question,
)
from webchat.orchestration.presentation.clarifications import (
    clarification_suggestion_result,
    reference_clarification_presentation,
)
from webchat.orchestration.presentation.suggestions import vehicle_comparison_suggestions
from webchat.orchestration.state import WorkflowStateReducer
from webchat.orchestration.tools.result import ToolResult, alternative_offer
from webchat.orchestration.workflow_kernel import (
    test_drive_slot_resolution as resolve_test_drive_slots,
)
from webchat.orchestration.workflow_kernel import (
    test_drive_transition as transition_test_drive,
)


def test_agent_plan_is_strict_and_bounded_to_four_intents() -> None:
    intents = [
        PlanIntent(
            intentId=f"intent-{index}",
            kind="vehicle_search",
            order=index,
            goal="Find a matching vehicle",
            status="ready",
        )
        for index in range(1, 5)
    ]
    plan = AgentPlan(mode="direct_response", intents=intents)
    assert len(plan.intents) == 4
    with pytest.raises(ValidationError):
        AgentPlan.model_validate(
            {
                "schemaVersion": 1,
                "mode": "direct_response",
                "intents": [
                    *[item.model_dump() for item in intents],
                    {
                        "intentId": "intent-5",
                        "kind": "vehicle_search",
                        "order": 4,
                        "goal": "One too many",
                        "status": "ready",
                    },
                ],
                "unexpected": True,
            }
        )


def test_dialogue_state_closes_only_the_question_explicitly_answered() -> None:
    reducer = ConversationStateReducer()
    state = reducer.apply(
        ConversationState(),
        StateEvent(
            type="DIALOGUE_QUESTION_OPENED",
            turnId="turn-question",
            data={
                "questionId": "question-schedule",
                "kind": "input",
                "prompt": "What day and approximate time would suit you?",
                "goalIntent": "test_drive",
                "candidateReferences": [],
                "candidateIntents": [],
                "expectedFields": ["dateFrom", "timeOfDay"],
                "originatingMessageId": "message-question",
                "createdAtStateVersion": 1,
            },
        ),
    )
    understanding = TurnUnderstanding(
        dialogueAct="answer_open_question",
        goalRelation="open_question",
        intentKinds=["test_drive"],
        answeredQuestionId="question-schedule",
        resolvedInputs=[
            ResolvedInput(
                field="dateFrom",
                value="2026-09-06",
                sourceContextId="message:customer-answer",
                sourceText="tomorrow",
            ),
            ResolvedInput(
                field="timeOfDay",
                value="morning",
                sourceContextId="message:customer-answer",
                sourceText="morning",
            ),
        ],
        ambiguity="none",
        confidence="high",
    )

    resolved = reducer.apply(
        state,
        StateEvent(
            type="DIALOGUE_TURN_RESOLVED",
            turnId="turn-answer",
            data={
                "understanding": understanding.model_dump(mode="json"),
                "questionDisposition": "consume",
            },
        ),
    )

    assert resolved.dialogue.activeQuestion is None
    assert resolved.dialogue.lastResolution is not None
    assert resolved.dialogue.lastResolution.understanding.resolvedInputs[0].field == "dateFrom"


@pytest.mark.parametrize(
    ("active_workflow", "goal_intent", "expected_field"),
    [
        ("test_drive", "test_drive", "dateFrom"),
        ("workshop_booking", "workshop_booking", "serviceTypeId"),
        ("sales_enquiry", "sales_enquiry", "message"),
        ("vehicle_interest", "vehicle_interest", "vehicleId"),
        ("callback", "callback", "reason"),
        ("dealership_message", "dealership_message", "message"),
        ("part_exchange", "part_exchange", "dealershipId"),
        ("existing_workshop_booking", "booking_lookup", "reference"),
        ("workshop_amend", "booking_amendment", "slotId"),
        ("workshop_cancel", "booking_cancellation", "reference"),
    ],
)
def test_information_interruption_preserves_the_open_workflow_question_without_displaying_it(
    active_workflow: str,
    goal_intent: str,
    expected_field: str,
) -> None:
    workflow = {
        "version": 3,
        "activeWorkflow": active_workflow,
        "stage": "collecting",
        "entities": {},
        "constraints": {
            "missingPublicFields": [expected_field],
            "acceptedInputFields": [expected_field],
        },
    }
    state = ConversationStateReducer().apply(
        ConversationState(agentWorkflow=workflow),
        StateEvent(
            type="DIALOGUE_QUESTION_OPENED",
            turnId="turn-question",
            data={
                "questionId": "question-original",
                "kind": "input",
                "prompt": "What detail would you like to provide next?",
                "goalIntent": goal_intent,
                "candidateReferences": [],
                "candidateIntents": [],
                "expectedFields": [expected_field],
                "originatingMessageId": "message-original",
                "createdAtStateVersion": 0,
            },
        ),
    )
    understanding = TurnUnderstanding(
        dialogueAct="interrupt_with_information_request",
        goalRelation="unrelated",
        intentKinds=["dealership_detail"],
        confidence="high",
    )
    messages = [
        {
            "text": "Would you like another dealership detail or to continue?",
            "purpose": "follow_up",
            "viewType": "grounded_presentation",
            "view": {"cards": [{"type": "dealership", "data": {}}]},
        }
    ]
    disposition = _dialogue_question_disposition(
        state,
        workflow,
        understanding,
        [],
        WorkflowStateReducer(),
    )

    assert messages[-1]["text"] == "Would you like another dealership detail or to continue?"
    assert messages[-1]["purpose"] == "follow_up"
    assert messages[-1]["viewType"] == "grounded_presentation"
    assert (
        _workflow_dialogue_question(
            messages,
            [],
            workflow,
            disposition,
            state.stateVersion + 1,
        )
        is None
    )
    assert state.dialogue.activeQuestion is not None
    assert state.dialogue.activeQuestion.goalIntent == goal_intent


@pytest.mark.parametrize(
    ("active_workflow", "goal_intent"),
    [
        ("existing_workshop_booking", "booking_lookup"),
        ("workshop_amend", "booking_amendment"),
        ("workshop_cancel", "booking_cancellation"),
    ],
)
def test_workflow_question_owner_preserves_specialized_booking_goal_intents(
    active_workflow: str,
    goal_intent: str,
) -> None:
    question = _workflow_dialogue_question(
        [{"text": "What day would suit you?", "purpose": "workflow_prompt"}],
        [],
        {
            "version": 3,
            "activeWorkflow": active_workflow,
            "stage": "collecting",
            "entities": {},
            "constraints": {
                "missingPublicFields": ["preferredDayOrDate"],
                "acceptedInputFields": ["dateFrom", "dateTo"],
            },
        },
        "retain",
        1,
    )

    assert question is not None
    assert question["goalIntent"] == goal_intent


@pytest.mark.parametrize(
    "active_workflow",
    sorted(
        {
            *CapabilityRegistry.SPECS,
            *CapabilityRegistry.ACTIVE_WORKFLOW_ALIASES,
        }
    ),
)
def test_executed_information_read_preserves_every_registered_workflow_question_even_when_semantics_says_switch(
    active_workflow: str,
) -> None:
    workflow = {
        "version": 3,
        "activeWorkflow": active_workflow,
        "stage": "collecting",
        "entities": {},
        "constraints": {
            "missingPublicFields": ["message"],
            "acceptedInputFields": ["message"],
        },
    }
    state = ConversationStateReducer().apply(
        ConversationState(agentWorkflow=workflow),
        StateEvent(
            type="DIALOGUE_QUESTION_OPENED",
            turnId="turn-question",
            data={
                "questionId": "question-original",
                "kind": "input",
                "prompt": "What would you like us to know?",
                "goalIntent": CapabilityRegistry.goal_intent_for_active_workflow(active_workflow),
                "candidateReferences": [],
                "candidateIntents": [],
                "expectedFields": ["message"],
                "originatingMessageId": "message-original",
                "createdAtStateVersion": 0,
            },
        ),
    )
    reducer = WorkflowStateReducer()
    next_workflow = reducer.advance(
        workflow,
        "search_vehicles",
        {"make": "BMW", "colour": "black"},
        "vehicle_list",
        {"items": []},
    )
    understanding = TurnUnderstanding(
        dialogueAct="switch_goal",
        goalRelation="active",
        intentKinds=["vehicle_search"],
        resolvedInputs=[
            ResolvedInput(
                field="make",
                value="BMW",
                sourceContextId="message:customer",
                sourceText="black BMW",
            ),
            ResolvedInput(
                field="colour",
                value="black",
                sourceContextId="message:customer",
                sourceText="black BMW",
            ),
        ],
        confidence="high",
    )

    disposition = _dialogue_question_disposition(
        state,
        next_workflow,
        understanding,
        [SimpleNamespace(tool="search_vehicles")],
        reducer,
    )
    messages = [
        {
            "text": "Would you like to broaden the search or change the make filter?",
            "purpose": "follow_up",
        }
    ]
    resolved = ConversationStateReducer().apply(
        state,
        StateEvent(
            type="DIALOGUE_TURN_RESOLVED",
            turnId="turn-interruption",
            data={
                "understanding": understanding.model_dump(mode="json"),
                "questionDisposition": disposition,
            },
        ),
    )

    assert disposition == "preserve"
    assert next_workflow["activeWorkflow"] == active_workflow
    assert messages[-1]["text"] == "Would you like to broaden the search or change the make filter?"
    assert (
        _workflow_dialogue_question(
            messages,
            [SimpleNamespace(tool="search_vehicles")],
            next_workflow,
            disposition,
            resolved.stateVersion + 1,
        )
        is None
    )
    assert resolved.dialogue.activeQuestion == state.dialogue.activeQuestion


@pytest.mark.parametrize(
    ("active_workflow", "field", "namespace", "tool", "view_type"),
    [
        ("test_drive", "vehicleId", "vehicle", "search_vehicles", "vehicle_list"),
        (
            "workshop_booking",
            "serviceTypeId",
            "service",
            "list_service_types",
            "service_list",
        ),
        ("callback", "dealershipId", "dealership", "list_dealerships", "dealership_list"),
        ("sales_enquiry", "offerId", "offer", "list_offers", "offer_list"),
    ],
)
def test_visible_candidate_refresh_replaces_the_open_choice_for_every_reference_namespace(
    active_workflow: str,
    field: str,
    namespace: str,
    tool: str,
    view_type: str,
) -> None:
    workflow = {
        "version": 3,
        "activeWorkflow": active_workflow,
        "stage": "choosing_subject",
        "entities": {},
        "constraints": {"missingPublicFields": [field]},
    }
    state = ConversationStateReducer().apply(
        ConversationState(agentWorkflow=workflow),
        StateEvent(
            type="DIALOGUE_QUESTION_OPENED",
            turnId="turn-old-candidates",
            data={
                "questionId": "question-old-candidates",
                "kind": "reference_choice",
                "prompt": "Which result would you like?",
                "goalIntent": CapabilityRegistry.goal_intent_for_active_workflow(active_workflow),
                "candidateReferences": [
                    f"{namespace}:old-1",
                    f"{namespace}:old-2",
                    f"{namespace}:old-3",
                ],
                "candidateIntents": [],
                "expectedFields": [field],
                "originatingMessageId": "message-old-candidates",
                "createdAtStateVersion": 0,
            },
        ),
    )
    items = [{"id": f"new-{index}", "name": f"Choice {index}"} for index in range(1, 6)]
    if namespace == "vehicle":
        payload = {
            "version": 1,
            "items": [{"id": item["id"], "make": "MINI", "model": item["name"]} for item in items],
            "page": 2,
            "pageSize": 5,
            "total": 8,
            "search": {"filters": {"maxPricePence": 3_500_000}, "page": 2},
            "suggestions": [],
        }
    else:
        payload = {
            "version": 1,
            "selectionOnly": True,
            "choiceEntityType": namespace,
            "choiceField": field,
            "items": items,
            "collectionPresentation": {
                "schemaVersion": 1,
                "layout": "bullet_list",
                "purpose": "choice",
                "items": [{"label": item["name"]} for item in items],
            },
        }
    normalized = FactNormalizer().normalize(
        result_id="result-new-candidates",
        call_id="call-new-candidates",
        intent_id="intent-new-candidates",
        tool=tool,
        result=ToolResult("Here are more choices.", view_type, payload, payload),
    )
    reducer = WorkflowStateReducer()
    next_workflow = reducer.advance(
        workflow,
        tool,
        {},
        view_type,
        payload,
    )

    disposition = _dialogue_question_disposition(
        state,
        next_workflow,
        None,
        [normalized],
        reducer,
    )
    next_question = _workflow_dialogue_question(
        [
            {
                "id": "message-new-candidates",
                "text": "Which of these would you like?",
                "purpose": "workflow_prompt",
            }
        ],
        [normalized],
        next_workflow,
        disposition,
        state.stateVersion + 1,
        active_question=state.dialogue.activeQuestion,
    )

    assert disposition == "refresh"
    assert next_workflow["activeWorkflow"] == active_workflow
    assert next_question is not None
    assert next_question["candidateReferences"] == [
        f"{namespace}:new-{index}" for index in range(1, 6)
    ]


def test_single_entity_detail_read_does_not_shrink_an_open_reference_choice() -> None:
    workflow = {
        "version": 3,
        "activeWorkflow": "test_drive",
        "stage": "choosing_vehicle",
        "entities": {},
        "constraints": {"missingPublicFields": ["vehicleId"]},
    }
    state = ConversationStateReducer().apply(
        ConversationState(agentWorkflow=workflow),
        StateEvent(
            type="DIALOGUE_QUESTION_OPENED",
            turnId="turn-vehicles",
            data={
                "questionId": "question-vehicles",
                "kind": "reference_choice",
                "prompt": "Which vehicle would you like?",
                "goalIntent": "test_drive",
                "candidateReferences": ["vehicle:veh-001", "vehicle:veh-002"],
                "candidateIntents": [],
                "expectedFields": ["vehicleId"],
                "originatingMessageId": "message-vehicles",
                "createdAtStateVersion": 0,
            },
        ),
    )
    payload = {
        "version": 1,
        "vehicle": {"id": "veh-002", "make": "MINI", "model": "Cooper"},
        "suggestions": [],
    }
    normalized = FactNormalizer().normalize(
        result_id="result-vehicle-detail",
        call_id="call-vehicle-detail",
        intent_id="intent-vehicle-detail",
        tool="get_vehicle",
        result=ToolResult("Here are its details.", "vehicle_details", payload, payload["vehicle"]),
    )

    assert (
        _dialogue_question_disposition(
            state,
            workflow,
            None,
            [normalized],
            WorkflowStateReducer(),
        )
        == "preserve"
    )


def test_successful_transaction_switch_closes_the_previous_question() -> None:
    workflow = {
        "version": 3,
        "activeWorkflow": "test_drive",
        "stage": "choosing_schedule_preferences",
        "entities": {"vehicleId": "veh-049"},
        "constraints": {
            "missingPublicFields": ["preferredDayOrDate"],
            "acceptedInputFields": ["dateFrom", "dateTo"],
        },
    }
    state = ConversationStateReducer().apply(
        ConversationState(agentWorkflow=workflow),
        StateEvent(
            type="DIALOGUE_QUESTION_OPENED",
            turnId="turn-question",
            data={
                "questionId": "question-original",
                "kind": "input",
                "prompt": "What day would suit you?",
                "goalIntent": "test_drive",
                "candidateReferences": [],
                "candidateIntents": [],
                "expectedFields": ["dateFrom", "dateTo"],
                "originatingMessageId": "message-original",
                "createdAtStateVersion": 0,
            },
        ),
    )
    reducer = WorkflowStateReducer()
    next_workflow = reducer.advance(
        workflow,
        "prepare_callback",
        {},
        "draft",
        {
            "summary": {},
            "missingPublicFields": ["dealershipId"],
            "secureFields": ["firstName"],
            "secureInputReady": False,
        },
    )
    understanding = TurnUnderstanding(
        dialogueAct="switch_goal",
        goalRelation="new",
        intentKinds=["callback"],
        confidence="high",
    )

    disposition = _dialogue_question_disposition(
        state,
        next_workflow,
        understanding,
        [SimpleNamespace(tool="prepare_callback")],
        reducer,
    )
    resolved = ConversationStateReducer().apply(
        state,
        StateEvent(
            type="DIALOGUE_TURN_RESOLVED",
            turnId="turn-switch",
            data={
                "understanding": understanding.model_dump(mode="json"),
                "questionDisposition": disposition,
            },
        ),
    )

    assert disposition == "close"
    assert next_workflow["activeWorkflow"] == "callback"
    assert resolved.dialogue.activeQuestion is None


def test_executed_cancellation_closes_the_question_with_an_empty_workflow() -> None:
    workflow = {
        "version": 3,
        "activeWorkflow": "test_drive",
        "stage": "choosing_schedule_preferences",
        "entities": {"vehicleId": "veh-049"},
        "constraints": {"missingPublicFields": ["preferredDayOrDate"]},
    }
    state = ConversationStateReducer().apply(
        ConversationState(agentWorkflow=workflow),
        StateEvent(
            type="DIALOGUE_QUESTION_OPENED",
            turnId="turn-question",
            data={
                "questionId": "question-original",
                "kind": "input",
                "prompt": "What day would suit you?",
                "goalIntent": "test_drive",
                "candidateReferences": [],
                "candidateIntents": [],
                "expectedFields": ["dateFrom"],
                "originatingMessageId": "message-original",
                "createdAtStateVersion": 0,
            },
        ),
    )
    understanding = TurnUnderstanding(
        dialogueAct="cancel_goal",
        goalRelation="active",
        intentKinds=["capability"],
        confidence="high",
    )

    disposition = _dialogue_question_disposition(
        state,
        {},
        understanding,
        [SimpleNamespace(tool="cancel_active_capability")],
        WorkflowStateReducer(),
    )
    resolved = ConversationStateReducer().apply(
        state,
        StateEvent(
            type="DIALOGUE_TURN_RESOLVED",
            turnId="turn-cancel",
            data={
                "understanding": understanding.model_dump(mode="json"),
                "questionDisposition": disposition,
            },
        ),
    )

    assert disposition == "close"
    assert resolved.dialogue.activeQuestion is None


def test_executed_information_read_preserves_question_over_incorrect_answer_label() -> None:
    workflow = {
        "version": 3,
        "activeWorkflow": "test_drive",
        "stage": "choosing_schedule_preferences",
        "entities": {"vehicleId": "veh-049"},
        "constraints": {"missingPublicFields": ["preferredDayOrDate"]},
    }
    state = ConversationStateReducer().apply(
        ConversationState(agentWorkflow=workflow),
        StateEvent(
            type="DIALOGUE_QUESTION_OPENED",
            turnId="turn-question",
            data={
                "questionId": "question-original",
                "kind": "input",
                "prompt": "What day would suit you?",
                "goalIntent": "test_drive",
                "candidateReferences": [],
                "candidateIntents": [],
                "expectedFields": ["dateFrom"],
                "originatingMessageId": "message-original",
                "createdAtStateVersion": 0,
            },
        ),
    )
    understanding = TurnUnderstanding(
        dialogueAct="answer_open_question",
        goalRelation="open_question",
        intentKinds=["vehicle_search"],
        answeredQuestionId="question-original",
        confidence="high",
    )
    reducer = WorkflowStateReducer()
    next_workflow = reducer.advance(
        workflow,
        "search_vehicles",
        {"make": "BMW", "colour": "black"},
        "vehicle_list",
        {"items": []},
    )

    disposition = _dialogue_question_disposition(
        state,
        next_workflow,
        understanding,
        [SimpleNamespace(tool="search_vehicles")],
        reducer,
    )

    assert disposition == "preserve"


def test_candidate_branch_closes_the_hidden_question_but_keeps_paused_work_resumable() -> None:
    active = {
        "version": 3,
        "activeWorkflow": "test_drive",
        "stage": "choosing_schedule_preferences",
        "entities": {"vehicleId": "veh-036"},
        "constraints": {"missingPublicFields": ["preferredDayOrDate"]},
    }
    state = ConversationStateReducer().apply(
        ConversationState(agentWorkflow=active),
        StateEvent(
            type="DIALOGUE_QUESTION_OPENED",
            turnId="turn-test-drive",
            data={
                "questionId": "question-test-drive-date",
                "kind": "input",
                "prompt": "What day would suit you?",
                "goalIntent": "test_drive",
                "candidateReferences": [],
                "candidateIntents": [],
                "expectedFields": ["dateFrom", "timeOfDay"],
                "originatingMessageId": "message-test-drive-date",
                "createdAtStateVersion": 0,
            },
        ),
    )
    branch = WorkflowStateReducer().advance(
        active,
        "search_vehicles",
        {"make": "Volvo", "bodyStyle": "SUV"},
        "vehicle_list",
        {"items": [{"id": "veh-049"}]},
        transition="branch",
    )
    understanding = TurnUnderstanding(
        dialogueAct="interrupt_with_information_request",
        goalRelation="unrelated",
        intentKinds=["vehicle_search"],
        confidence="high",
    )

    disposition = _dialogue_question_disposition(
        state,
        branch,
        understanding,
        [SimpleNamespace(tool="search_vehicles")],
        WorkflowStateReducer(),
    )
    resolved = ConversationStateReducer().apply(
        state,
        StateEvent(
            type="DIALOGUE_TURN_RESOLVED",
            turnId="turn-volvo-search",
            data={
                "understanding": understanding.model_dump(mode="json"),
                "questionDisposition": disposition,
            },
        ),
    )

    assert disposition == "close"
    assert resolved.dialogue.activeQuestion is None
    assert branch["pausedWorkflow"] == active


def test_dialogue_resolution_rejects_a_raw_model_decision_without_compiled_disposition() -> None:
    understanding = TurnUnderstanding(
        dialogueAct="switch_goal",
        goalRelation="new",
        intentKinds=["callback"],
        confidence="high",
    )

    with pytest.raises(ValueError):
        ConversationStateReducer().apply(
            ConversationState(),
            StateEvent(
                type="DIALOGUE_TURN_RESOLVED",
                turnId="turn-switch",
                data=understanding.model_dump(mode="json"),
            ),
        )


def test_dialogue_state_supports_confirmation_of_one_finite_choice() -> None:
    state = ConversationStateReducer().apply(
        ConversationState(),
        StateEvent(
            type="DIALOGUE_QUESTION_OPENED",
            turnId="turn-slot",
            data={
                "questionId": "question-slot",
                "kind": "reference_choice",
                "prompt": "Does the available appointment suit you?",
                "goalIntent": "test_drive",
                "candidateReferences": ["appointment:slot-1"],
                "candidateIntents": [],
                "expectedFields": [],
                "originatingMessageId": "message-slot",
                "createdAtStateVersion": 1,
            },
        ),
    )

    assert state.dialogue.activeQuestion is not None
    assert state.dialogue.activeQuestion.candidateReferences == ["appointment:slot-1"]


@pytest.mark.parametrize("terminal_event", ["INTERACTION_CANCELLED", "INTERACTION_EXECUTED"])
def test_finishing_a_protected_workflow_resumes_the_paused_workflow(
    terminal_event: str,
) -> None:
    paused = {
        "version": 3,
        "activeWorkflow": "test_drive",
        "stage": "choosing_slot",
        "entities": {"vehicleId": "veh-049"},
        "constraints": {"missingPublicFields": ["slotId"]},
    }
    state = ConversationState(
        agentWorkflow={
            "version": 3,
            "activeWorkflow": "callback",
            "stage": "awaiting_confirmation",
            "entities": {"dealershipId": "northstar-stockport"},
            "constraints": {"draftId": "draft-callback"},
            "pausedWorkflow": paused,
        },
        pendingInteraction=PendingInteractionState(
            interactionId="interaction-callback",
            kind="protected_confirmation",
            originatingMessageId="message-callback",
            createdAtStateVersion=0,
            status=(
                "confirmed" if terminal_event == "INTERACTION_EXECUTED" else "awaiting_confirmation"
            ),
            activeDraftId="draft-callback",
            workflowKind="callback",
        ),
    )

    result = ConversationStateReducer().apply(
        state,
        StateEvent(
            type=terminal_event,
            turnId="turn-terminal",
            data={"interactionId": "interaction-callback"},
        ),
    )

    assert result.agentWorkflow == paused


def test_superseding_confirmation_pauses_workflow_without_losing_its_parent() -> None:
    parent = {
        "version": 3,
        "activeWorkflow": "test_drive",
        "stage": "choosing_slot",
        "entities": {"vehicleId": "veh-049"},
        "constraints": {"missingPublicFields": ["slotId"]},
    }
    state = ConversationState(
        agentWorkflow={
            "version": 3,
            "activeWorkflow": "callback",
            "stage": "awaiting_confirmation",
            "entities": {"dealershipId": "northstar-stockport"},
            "constraints": {"draftId": "draft-callback", "secureInputReady": True},
            "pausedWorkflow": parent,
        },
        pendingInteraction=PendingInteractionState(
            interactionId="interaction-callback",
            kind="protected_confirmation",
            originatingMessageId="message-callback",
            createdAtStateVersion=0,
            status="awaiting_confirmation",
            activeDraftId="draft-callback",
            workflowKind="callback",
        ),
    )

    result = ConversationStateReducer().apply(
        state,
        StateEvent(
            type="INTERACTION_SUPERSEDED",
            turnId="turn-interruption",
            data={"interactionId": "interaction-callback"},
        ),
    )

    assert result.pendingInteraction is not None
    assert result.pendingInteraction.status == "superseded"
    assert result.agentWorkflow["activeWorkflow"] == "callback"
    assert result.agentWorkflow["stage"] == "paused"
    assert "draftId" not in result.agentWorkflow["constraints"]
    assert result.agentWorkflow["pausedWorkflow"] == parent


@pytest.mark.parametrize(
    ("requested_count", "rendered_count"),
    [(1, 1), (2, 2), (3, 2), (4, 4)],
)
def test_ai_quick_replies_never_render_an_unbalanced_three_chip_row(
    requested_count: int,
    rendered_count: int,
) -> None:
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="follow_up",
                blocks=[
                    {
                        "type": "paragraph",
                        "segments": [{"type": "text", "text": "What matters most?"}],
                    }
                ],
            )
        ],
        quickReplies=[
            QuickReplyDraft(label=f"Choice {index}", message=f"Choice {index}")
            for index in range(1, requested_count + 1)
        ],
    )

    resolved = GroundingValidator().resolve(draft, [])

    assert len(resolved.suggestions) == rendered_count


def test_three_trusted_suggestions_are_completed_from_the_same_candidate_set() -> None:
    suggestions = [
        AvailableSuggestion(
            reference=f"suggestion:result-vehicles:{index}",
            label=f"Choice {index}",
            message=f"Choice {index}",
        )
        for index in range(1, 5)
    ]
    result = ToolResultEnvelope(
        resultId="result-vehicles",
        callId="call-vehicles",
        intentId="intent-vehicles",
        tool="search_vehicles",
        status="success",
        availableSuggestions=suggestions,
        observedAt=datetime.now(UTC),
    )
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="next_step",
                segments=[TextSegment(type="text", text="What would you like to do next?")],
            )
        ],
        suggestionReferences=[item.reference for item in suggestions[:3]],
    )

    resolved = GroundingValidator().resolve(
        draft,
        [result],
        workflow_state={"activeWorkflow": "vehicle_search", "constraints": {}},
    )

    assert [item.reference for item in resolved.suggestions] == [
        item.reference for item in suggestions
    ]


def test_three_ai_clarification_examples_reduce_to_two_without_inventing_an_answer() -> None:
    interaction = PendingInteraction(
        kind="input",
        prompt="Which style do you prefer?",
        blocking_tool="search_vehicles",
        blocking_fields=["bodyStyle"],
    )

    result = clarification_suggestion_result(
        interaction.prompt,
        ("SUV", "Hatchback", "Estate"),
        interaction,
    )

    assert result is not None
    assert [item["label"] for item in result.view_payload["suggestions"]] == [
        "SUV",
        "Hatchback",
    ]


def test_reference_ambiguity_uses_one_complete_chip_collection_owned_by_the_application() -> None:
    interaction = PendingInteraction(
        kind="reference_choice",
        prompt=(
            "Which vehicle would you like to test drive: option 1, the BMW 3 Series; "
            "option 2, the MINI Countryman; or option 3, the BMW 1 Series?"
        ),
        question_id="question-vehicle-choice",
        goal_intent="test_drive",
        candidate_references=["vehicle:veh-014", "vehicle:veh-042", "vehicle:veh-049"],
    )
    context = PlanningContext(
        latest_customer_message="book a test drive for one of these vehicles",
        displayed_vehicles=[
            {
                "position": 1,
                "vehicleId": "veh-014",
                "make": "BMW",
                "model": "3 Series",
                "variant": "320d M Sport",
                "dealershipTown": "Stockport",
            },
            {
                "position": 2,
                "vehicleId": "veh-042",
                "make": "MINI",
                "model": "Countryman",
                "variant": "Cooper Classic",
                "dealershipTown": "Stockport",
            },
            {
                "position": 3,
                "vehicleId": "veh-049",
                "make": "BMW",
                "model": "1 Series",
                "variant": "118i M Sport",
                "dealershipTown": "Manchester",
            },
        ],
    )

    presentation = reference_clarification_presentation(interaction, context)

    assert presentation is not None
    prompt, view = presentation
    assert prompt == "Which vehicle would you like to test drive?"
    assert view["selectionOnly"] is True
    assert view["choiceEntityType"] == "vehicle"
    assert view["collectionPresentation"] == {
        "schemaVersion": 1,
        "layout": "bullet_list",
        "purpose": "clarification",
        "items": [
            {
                "label": "Option 1 — BMW 3 Series",
                "description": "320d M Sport · Stockport",
            },
            {
                "label": "Option 2 — MINI Countryman",
                "description": "Cooper Classic · Stockport",
            },
            {
                "label": "Option 3 — BMW 1 Series",
                "description": "118i M Sport · Manchester",
            },
        ],
    }


def test_same_model_reference_choices_are_visibly_distinguishable() -> None:
    interaction = PendingInteraction(
        kind="reference_choice",
        prompt="Which MINI Cooper do you mean?",
        question_id="question-cooper-choice",
        goal_intent="test_drive",
        candidate_references=["vehicle:veh-041", "vehicle:veh-005"],
    )
    context = PlanningContext(
        latest_customer_message="the MINI Cooper",
        displayed_vehicles=[
            {
                "position": 1,
                "vehicleId": "veh-041",
                "year": 2025,
                "make": "MINI",
                "model": "Cooper",
                "variant": "Cooper S Exclusive",
                "colour": "Midnight Black",
                "mileage": 3_500,
                "dealershipTown": "Manchester",
            },
            {
                "position": 2,
                "vehicleId": "veh-005",
                "year": 2024,
                "make": "MINI",
                "model": "Cooper",
                "variant": "Cooper S Exclusive",
                "colour": "British Racing Green",
                "mileage": 28_500,
                "dealershipTown": "Manchester",
            },
        ],
    )

    presentation = reference_clarification_presentation(interaction, context)

    assert presentation is not None
    items = presentation[1]["collectionPresentation"]["items"]
    assert items == [
        {
            "label": "Option 1 — 2025 MINI Cooper",
            "description": ("Cooper S Exclusive · Midnight Black · 3,500 mi · Manchester"),
        },
        {
            "label": "Option 2 — 2024 MINI Cooper",
            "description": ("Cooper S Exclusive · British Racing Green · 28,500 mi · Manchester"),
        },
    ]


def test_paginated_candidates_receive_one_unique_consolidated_choice_order() -> None:
    interaction = PendingInteraction(
        kind="reference_choice",
        prompt="Which MINI Countryman would you like to test drive?",
        question_id="question-paginated-countryman",
        goal_intent="test_drive",
        candidate_references=[
            "vehicle:veh-012",
            "vehicle:veh-024",
            "vehicle:veh-036",
            "vehicle:veh-060",
        ],
    )
    context = PlanningContext(
        latest_customer_message="book a test drive for mini",
        displayed_choices=[
            {
                "position": 1,
                "entityReference": "vehicle:veh-060",
                "label": "Option 1 — 2021 MINI Countryman",
            }
        ],
        displayed_vehicles=[
            {
                "resultPage": 1,
                "position": 1,
                "vehicleId": "veh-012",
                "year": 2023,
                "make": "MINI",
                "model": "Countryman",
            },
            {
                "resultPage": 2,
                "position": 1,
                "vehicleId": "veh-060",
                "year": 2021,
                "make": "MINI",
                "model": "Countryman",
            },
            {
                "resultPage": 1,
                "position": 2,
                "vehicleId": "veh-024",
                "year": 2026,
                "make": "MINI",
                "model": "Countryman",
            },
            {
                "resultPage": 1,
                "position": 3,
                "vehicleId": "veh-036",
                "year": 2025,
                "make": "MINI",
                "model": "Countryman",
            },
        ],
    )

    presentation = reference_clarification_presentation(interaction, context)

    assert presentation is not None
    view = presentation[1]
    assert [item["position"] for item in view["items"]] == [1, 2, 3, 4]
    assert [item["label"] for item in view["collectionPresentation"]["items"]] == [
        "Option 1 — 2023 MINI Countryman",
        "Option 2 — 2026 MINI Countryman",
        "Option 3 — 2025 MINI Countryman",
        "Option 4 — 2021 MINI Countryman",
    ]


def test_large_reference_ambiguity_keeps_scope_without_dumping_a_choice_menu() -> None:
    references = [f"vehicle:veh-{index:03d}" for index in range(1, 6)]
    interaction = PendingInteraction(
        kind="reference_choice",
        prompt="Which vehicle are you asking about? You can give me its make and model.",
        question_id="question-large-vehicle-set",
        goal_intent="business_information",
        candidate_references=references,
    )
    context = PlanningContext(
        latest_customer_message="Can I get finance information for this vehicle?",
        page_vehicles=[
            {
                "position": index,
                "vehicleId": f"veh-{index:03d}",
                "make": "Northstar",
                "model": f"Model {index}",
            }
            for index in range(1, 6)
        ],
    )

    presentation = reference_clarification_presentation(interaction, context)

    assert presentation == (interaction.prompt, None)
    assert interaction.candidate_references == references


@pytest.mark.parametrize(
    ("namespace", "context_field", "records", "references", "labels"),
    [
        (
            "offer",
            "displayed_offers",
            [
                {"offerId": "offer-01", "title": "BMW finance offer"},
                {"offerId": "offer-02", "title": "MINI finance offer"},
            ],
            ["offer:offer-01", "offer:offer-02"],
            ["BMW finance offer", "MINI finance offer"],
        ),
        (
            "dealership",
            "displayed_dealerships",
            [
                {"dealershipId": "dealer-01", "name": "Northstar Stockport"},
                {"dealershipId": "dealer-02", "name": "Northstar Manchester"},
            ],
            ["dealership:dealer-01", "dealership:dealer-02"],
            ["Northstar Stockport", "Northstar Manchester"],
        ),
        (
            "service",
            "displayed_choices",
            [
                {"entityReference": "service:mot", "label": "MOT"},
                {"entityReference": "service:full", "label": "Full service"},
            ],
            ["service:mot", "service:full"],
            ["MOT", "Full service"],
        ),
    ],
)
def test_reference_clarification_projection_is_shared_by_displayed_entity_types(
    namespace: str,
    context_field: str,
    records: list[dict],
    references: list[str],
    labels: list[str],
) -> None:
    interaction = PendingInteraction(
        kind="reference_choice",
        prompt="Which one did you mean?",
        question_id=f"question-{namespace}-choice",
        goal_intent="customer_goal",
        candidate_references=references,
    )
    context = PlanningContext(
        latest_customer_message="one of these",
        **{context_field: records},
    )

    presentation = reference_clarification_presentation(interaction, context)

    assert presentation is not None
    _, view = presentation
    assert view["choiceEntityType"] == namespace
    assert view["collectionPresentation"]["layout"] == "bullet_list"
    assert [item["label"] for item in view["collectionPresentation"]["items"]] == labels


def test_grounding_rejects_a_composer_question_without_a_typed_owner() -> None:
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                blocks=[
                    {
                        "type": "paragraph",
                        "segments": [
                            {
                                "type": "text",
                                "text": (
                                    "I cannot confirm that. Is this for servicing, buying, "
                                    "or selling?"
                                ),
                            }
                        ],
                    }
                ],
            )
        ]
    )

    with pytest.raises(ValueError, match="typed owner"):
        GroundingValidator().resolve(draft, [], question_owned=False)


def test_grounding_allows_a_question_with_a_typed_owner() -> None:
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="follow_up",
                blocks=[
                    {
                        "type": "paragraph",
                        "segments": [
                            {
                                "type": "text",
                                "text": "Would you like help contacting a dealership?",
                            }
                        ],
                    }
                ],
            )
        ]
    )

    resolved = GroundingValidator().resolve(draft, [], question_owned=True)

    assert resolved.messages[0].text.endswith("?")


def test_active_workflow_prompt_rejects_catalogue_chips_and_ai_guesses() -> None:
    envelope = ToolResultEnvelope(
        resultId="result-live-vehicle-search",
        callId="call-live-vehicle-search",
        intentId="intent-test-drive",
        tool="search_vehicles",
        status="success",
        availableSuggestions=[
            AvailableSuggestion(
                reference="suggestion:compare",
                label="Compare these vehicles",
                message="compare these vehicles",
                action={"type": "compare_displayed_vehicles"},
            )
        ],
        responseObligation=ResponseObligation(
            kind="catalogue_results",
            subjectReferences=["vehicle:catalogue"],
        ),
        observedAt=datetime.now(UTC),
    )
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="workflow_prompt",
                segments=[TextSegment(type="text", text="Which vehicle do you mean?")],
            )
        ],
        suggestionReferences=["suggestion:compare"],
        quickReplies=[
            QuickReplyDraft(label="Cheapest first", message="show cheapest first"),
            QuickReplyDraft(label="Change model", message="change model"),
        ],
    )

    resolved = GroundingValidator().resolve(
        draft,
        [envelope],
        workflow_state={
            "activeWorkflow": "test_drive",
            "stage": "choosing_vehicle",
            "entities": {},
            "constraints": {"missingPublicFields": ["vehicleId"]},
        },
    )

    assert resolved.suggestions == ()


def test_active_workflow_renders_only_trusted_actions_explicitly_selected_by_ai() -> None:
    suggestion = AvailableSuggestion(
        reference="suggestion:test-drive:volvo",
        label="Book Volvo XC40",
        message="Book a test drive for the Volvo XC40",
        action={"type": "select_test_drive_vehicle", "vehicleId": "veh-019"},
    )
    envelope = ToolResultEnvelope(
        resultId="result-live-vehicle-choice",
        callId="call-live-vehicle-choice",
        intentId="intent-test-drive",
        tool="search_vehicles",
        status="success",
        availableSuggestions=[suggestion],
        observedAt=datetime.now(UTC),
    )
    prompt = GroundedMessageDraft(
        purpose="workflow_prompt",
        segments=[TextSegment(type="text", text="Which vehicle would you like to test drive?")],
    )
    state = {
        "activeWorkflow": "test_drive",
        "stage": "choosing_vehicle",
        "entities": {},
        "constraints": {"missingPublicFields": ["vehicleId"]},
    }

    omitted = GroundingValidator().resolve(
        GroundedResponseDraft(messages=[prompt]),
        [envelope],
        workflow_state=state,
    )
    selected = GroundingValidator().resolve(
        GroundedResponseDraft(
            messages=[prompt],
            suggestionReferences=[suggestion.reference],
        ),
        [envelope],
        workflow_state=state,
    )

    assert omitted.suggestions == ()
    assert selected.suggestions == (suggestion,)


def test_grounded_suggestion_actions_use_the_public_turn_action_schema() -> None:
    suggestion = AvailableSuggestion(
        reference="suggestion:diesel",
        label="Diesel",
        message="Show me diesel vehicles",
        action={
            "type": "apply_vehicle_preference",
            "vehicleFilter": "fuelType",
            "vehicleFilterValue": "Diesel",
        },
    )
    assert suggestion.action == {
        "type": "apply_vehicle_preference",
        "vehicleFilter": "fuelType",
        "vehicleFilterValue": "Diesel",
    }
    with pytest.raises(ValidationError):
        AvailableSuggestion(
            reference="suggestion:unsafe",
            label="Unsafe",
            message="Do something unknown",
            action={"type": "execute_url", "url": "https://example.test"},
        )


def test_collection_presentation_is_strict_structured_and_visual_only() -> None:
    card = AvailableCard(
        reference="card:services",
        type="service",
        data={
            "collectionPresentation": {
                "schemaVersion": 1,
                "layout": "chip_grid",
                "purpose": "choice",
                "items": [{"label": "MOT", "description": "Annual MOT inspection."}],
            }
        },
    )
    assert card.data["collectionPresentation"]["items"][0]["label"] == "MOT"

    appointment_reference = CollectionPresentation(
        layout="bullet_list",
        purpose="choice",
        items=[
            {
                "label": "Thu, 10 Sept 2026, 17:00",
                "message": "Choose Thu, 10 Sept 2026, 17:00",
            }
        ],
    )
    assert appointment_reference.layout == "bullet_list"
    assert appointment_reference.items[0].message == "Choose Thu, 10 Sept 2026, 17:00"
    clarification = CollectionPresentation(
        layout="bullet_list",
        purpose="clarification",
        items=[{"label": "MOT"}],
    )
    assert clarification.layout == "bullet_list"
    with pytest.raises(ValidationError, match="information collections require bullet_list"):
        CollectionPresentation(
            layout="chip_grid",
            purpose="information",
            items=[{"label": "MOT"}],
        )

    with pytest.raises(ValidationError):
        AvailableCard(
            reference="card:invalid-services",
            type="service",
            data={
                "collectionPresentation": {
                    "schemaVersion": 1,
                    "layout": "paragraph",
                    "purpose": "choice",
                    "items": [{"label": "MOT", "url": "/book"}],
                }
            },
        )


def test_alternative_offer_requires_trusted_candidates_and_explicit_customer_acceptance() -> None:
    now = datetime.now(UTC)
    offer = AlternativeOffer(
        reasonCode="requested_location_unavailable",
        requestedOutcome="Workshop service in Stockport",
        failureReason="No matching appointment is available in Stockport.",
        offeredOutcome="A matching appointment in Manchester.",
        changes=[
            {
                "dimension": "Location",
                "requested": "Stockport",
                "offered": "Manchester",
            }
        ],
        preserved=["Workshop service", "Requested date"],
        candidateReferences=["appointment:ws-slot-1"],
    )
    result = ToolResultEnvelope(
        resultId="result-alternative",
        callId="call-alternative",
        intentId="intent-alternative",
        tool="list_workshop_slots",
        status="empty",
        entities=[{"reference": "appointment:ws-slot-1", "type": "appointment"}],
        alternativeOffer=offer,
        observedAt=now,
    )

    assert result.alternativeOffer.requiresCustomerAcceptance is True
    with pytest.raises(ValidationError, match="trusted result references"):
        result.model_copy(
            update={
                "alternativeOffer": offer.model_copy(
                    update={"candidateReferences": ["appointment:unknown"]}
                )
            }
        ).__class__.model_validate(
            {
                **result.model_dump(mode="json"),
                "alternativeOffer": {
                    **offer.model_dump(mode="json"),
                    "candidateReferences": ["appointment:unknown"],
                },
            }
        )


def test_alternative_offer_becomes_ordinary_conversation_not_a_browser_card() -> None:
    offer = {
        "reasonCode": "requested_location_unavailable",
        "requestedOutcome": "A dealership in Newcastle",
        "failureReason": "Northstar has no Newcastle dealership.",
        "offeredOutcome": "Current dealerships in Manchester and Stockport.",
        "changes": [
            {
                "dimension": "Location",
                "requested": "Newcastle",
                "offered": "Manchester or Stockport",
            }
        ],
        "preserved": ["Northstar dealership"],
        "candidateReferences": [],
        "requiresCustomerAcceptance": True,
    }
    messages = [
        {
            "text": "Which location would you prefer?",
            "viewType": "choice_list",
            "view": {"version": 1, "collectionPresentation": {"items": []}},
        }
    ]

    _apply_alternative_disclosure(messages, offer)
    _apply_alternative_disclosure(messages, offer)

    assert messages[0]["viewType"] == "choice_list"
    assert "alternativeOffer" not in messages[0]["view"]
    assert messages[0]["text"] == (
        "Northstar has no Newcastle dealership. "
        "Current dealerships in Manchester and Stockport.\n\n"
        "Which location would you prefer?"
    )


def test_alternative_offer_replaces_model_paraphrase_on_collection_owner_once() -> None:
    offer = {
        "failureReason": "No appointments matched Monday morning at the selected location.",
        "offeredOutcome": "These are the next available times at that location.",
    }
    messages = [
        {
            "text": "Nothing matched your requested Monday morning time. Alternatives follow:",
            "viewType": "slot_list",
            "view": {
                "version": 1,
                "collectionPresentation": {"purpose": "choice", "items": [{"label": "12:00"}]},
            },
            "blocks": [],
            "segments": [],
        },
        {
            "text": "Would one of these appointment times suit you?",
            "viewType": None,
            "view": None,
            "blocks": [],
            "segments": [],
        },
    ]

    _apply_alternative_disclosure(messages, offer)
    _apply_alternative_disclosure(messages, offer)

    assert messages[0]["text"] == (
        "No appointments matched Monday morning at the selected location. "
        "These are the next available times at that location."
    )
    assert messages[1]["text"] == "Would one of these appointment times suit you?"
    assert sum("No appointments matched" in message["text"] for message in messages) == 1


def test_recovery_actions_do_not_masquerade_as_a_substitution_offer() -> None:
    result = ToolResult(
        "Nothing matched. Try another location.",
        "suggestion_list",
        {
            "version": 1,
            "suggestions": [{"label": "Try Manchester", "text": "Try Manchester"}],
        },
        {"outcome": "unavailable"},
    )

    normalized = FactNormalizer().normalize(
        result_id="result-recovery-actions",
        call_id="call-recovery-actions",
        intent_id="intent-recovery-actions",
        tool="list_workshop_slots",
        result=result,
    )

    assert normalized.alternativeOffer is None


def test_actual_substitution_cannot_bypass_shared_provenance_contract() -> None:
    result = ToolResult(
        "No requested times matched. Here are times at another location.",
        "slot_list",
        {
            "version": 1,
            "items": [],
            "requestedDealershipHadNoAvailability": True,
        },
        {
            "resolution": {
                "kind": "workshop_slots",
                "status": "unavailable",
                "continuation": "offer_location_alternatives",
            },
            "items": [],
        },
    )

    with pytest.raises(ValueError, match="trusted alternative provenance"):
        FactNormalizer().normalize(
            result_id="result-missing-substitution",
            call_id="call-missing-substitution",
            intent_id="intent-missing-substitution",
            tool="list_workshop_slots",
            result=result,
        )


def test_grounding_resolves_fact_references_and_rejects_invented_business_values() -> None:
    now = datetime.now(UTC)
    result = ToolResultEnvelope(
        resultId="result-one",
        callId="call-one",
        intentId="intent-one",
        tool="get_service_information",
        status="success",
        facts=[
            AtomicFact(
                factId="fact-one-price",
                field="service.priceFromPence",
                value=14900,
                displayValue="£149.00",
                source="dealership_platform",
                sensitivity="public",
                observedAt=now,
            )
        ],
        observedAt=now,
    )
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[
                    TextSegment(type="text", text="The current price is "),
                    FactSegment(type="fact", factId="fact-one-price"),
                    TextSegment(type="text", text="."),
                ],
            )
        ]
    )
    assert GroundingValidator().resolve(draft, [result]).messages[0].text.endswith("£149.00.")
    unsafe = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[TextSegment(type="text", text="The current price is £99.")],
            )
        ]
    )
    with pytest.raises(ValueError, match="trusted fact"):
        GroundingValidator().resolve(unsafe, [result])


def test_service_information_cannot_replace_a_published_price_with_an_absence_claim() -> None:
    result = FactNormalizer().normalize(
        result_id="result-service-price",
        call_id="call-service-price",
        intent_id="intent-service-price",
        tool="get_service_information",
        result=ToolResult(
            "MOT details",
            None,
            None,
            {
                "resolution": {"status": "matched"},
                "service": {
                    "id": "mot",
                    "name": "MOT",
                    "priceFromPence": 5499,
                    "pricingStatus": "published",
                },
            },
        ),
    )
    assert result.responseObligation is not None
    assert len(result.responseObligation.requiredFactIds) == 1
    unsupported = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[
                    TextSegment(
                        type="text",
                        text="I don’t have pricing information for MOT appointments.",
                    )
                ],
            )
        ]
    )

    with pytest.raises(ValueError, match="omitted facts required"):
        GroundingValidator().resolve(unsupported, [result])

    grounded = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[
                    TextSegment(type="text", text="MOT appointments start from "),
                    FactSegment(
                        type="fact",
                        factId=result.responseObligation.requiredFactIds[0],
                    ),
                    TextSegment(type="text", text="."),
                ],
            )
        ]
    )
    assert GroundingValidator().resolve(grounded, [result]).messages[0].text.endswith(
        "£54.99."
    )


def test_grounding_keeps_page_context_out_of_customer_language() -> None:
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="clarification",
                segments=[
                    TextSegment(
                        type="text",
                        text="Which BMW on the page would you like to test drive?",
                    )
                ],
            )
        ]
    )

    with pytest.raises(ValueError, match="implementation context"):
        GroundingValidator().resolve(draft, [])


def test_vehicle_stock_cannot_be_presented_as_test_drive_availability() -> None:
    now = datetime.now(UTC)
    inventory = ToolResultEnvelope(
        resultId="result-bmw-stock",
        callId="call-bmw-stock",
        intentId="intent-bmw-stock",
        tool="search_vehicles",
        status="success",
        observedAt=now,
    )
    unsupported_claim = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[
                    TextSegment(
                        type="text",
                        text="Test drives are available for the BMW models in stock.",
                    )
                ],
            )
        ]
    )

    with pytest.raises(ValueError, match="trusted slot result"):
        GroundingValidator().resolve(unsupported_claim, [inventory])

    safe_progression = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[
                    TextSegment(
                        type="text",
                        text=(
                            "I found matching BMWs in stock. Choose one and I’ll check the "
                            "test-drive times."
                        ),
                    )
                ],
            )
        ]
    )
    resolved = GroundingValidator().resolve(safe_progression, [inventory])
    assert resolved.messages[0].text.endswith("test-drive times.")


def test_empty_test_drive_result_has_an_exact_workflow_continuation() -> None:
    result = FactNormalizer().normalize(
        result_id="result-empty-test-drive",
        call_id="call-empty-test-drive",
        intent_id="intent-empty-test-drive",
        tool="list_test_drive_slots",
        result=ToolResult(
            "No online test-drive times are currently available for this vehicle.",
            "test_drive_slot_picker",
            {"version": 1, "items": [], "vehicleId": "veh-049"},
            {
                "resolution": {
                    "kind": "test_drive_slots",
                    "status": "empty",
                    "scope": "broad",
                    "continuation": "offer_vehicle_alternatives",
                    "vehicleId": "veh-049",
                    "count": 0,
                }
            },
            alternative_offer=alternative_offer(
                reason_code="test_drive_no_online_appointments",
                requested_outcome="A test drive for the selected vehicle",
                failure_reason="No online test-drive appointment is available.",
                offered_outcome="Choose another vehicle or request dealership contact.",
                changes=[
                    {
                        "dimension": "Next step",
                        "requested": "Book this vehicle online",
                        "offered": "Choose another vehicle or dealership contact",
                    }
                ],
            ),
        ),
    )

    assert result.status == "empty"
    assert result.responseObligation is not None
    assert result.responseObligation.kind == "workflow_transition"
    assert result.responseObligation.workflowCode == "offer_vehicle_alternatives"
    assert result.responseObligation.requiredFactIds == []
    assert result.responseObligation.choiceMode is None
    assert not any(fact.field.startswith("resolution.") for fact in result.facts)


def test_empty_vehicle_search_requires_the_complete_applied_filter_summary() -> None:
    result = FactNormalizer().normalize(
        result_id="result-empty-vehicles",
        call_id="call-empty-vehicles",
        intent_id="intent-empty-vehicles",
        tool="refine_vehicle_search",
        result=ToolResult(
            "No matching vehicles.",
            "suggestion_list",
            {
                "version": 1,
                "filterSummary": "Make: BMW; Colour: black; Location: Manchester",
                "suggestions": [
                    {"label": "Change make", "message": "change make"},
                    {"label": "Any make", "message": "use any make"},
                ],
            },
            {
                "outcome": "empty",
                "appliedSearch": {
                    "make": "BMW",
                    "colour": "black",
                    "dealershipTown": "Manchester",
                },
                "filterSummary": "Make: BMW; Colour: black; Location: Manchester",
            },
        ),
    )

    assert result.status == "empty"
    assert result.responseObligation is not None
    assert result.responseObligation.kind == "informational_next_steps"
    required = set(result.responseObligation.requiredFactIds)
    assert len(required) == 1
    assert next(fact for fact in result.facts if fact.factId in required).displayValue == (
        "Make: BMW; Colour: black; Location: Manchester"
    )

    opaque = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[TextSegment(type="text", text="I couldn't find any BMWs in Manchester.")],
            ),
            GroundedMessageDraft(
                purpose="follow_up",
                segments=[TextSegment(type="text", text="Would you like to change the search?")],
            ),
        ]
    )
    with pytest.raises(ValueError, match="omitted facts required by its result contract"):
        GroundingValidator().resolve(opaque, [result])

    transparent = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[
                    TextSegment(type="text", text="No vehicles matched these active filters: "),
                    FactSegment(type="fact", factId=next(iter(required))),
                    TextSegment(type="text", text="."),
                ],
            ),
            GroundedMessageDraft(
                purpose="follow_up",
                segments=[TextSegment(type="text", text="Would you like to change the search?")],
            ),
        ]
    )

    resolved = GroundingValidator().resolve(transparent, [result])

    assert "Colour: black" in resolved.messages[0].text


def test_location_empty_test_drive_result_keeps_a_live_slot_choice() -> None:
    resolution = resolve_test_drive_slots(
        {"dealershipId": "northstar-manchester", "schedulePreferenceMode": "clear"},
        count=0,
        location_alternative_count=1,
    )
    transition = transition_test_drive(resolution)

    assert resolution["status"] == "empty"
    assert resolution["continuation"] == "offer_location_alternatives"
    assert transition == {
        "stage": "choosing_slot",
        "missingPublicFields": ["slotId"],
        "acceptedInputFields": [],
        "continuation": "offer_location_alternatives",
    }

    result = FactNormalizer().normalize(
        result_id="result-location-alternative",
        call_id="call-location-alternative",
        intent_id="intent-location-alternative",
        tool="list_test_drive_slots",
        result=ToolResult(
            "A test-drive appointment is available at another dealership.",
            "test_drive_slot_picker",
            {
                "version": 1,
                "vehicleId": "veh-042",
                "items": [
                    {
                        "id": "td-slot-0280",
                        "vehicleId": "veh-042",
                        "dealershipId": "northstar-stockport",
                        "startsAt": "2026-09-10T16:00:00Z",
                    }
                ],
            },
            {
                "items": [{"id": "td-slot-0280", "startsAt": "2026-09-10T16:00:00Z"}],
                "resolution": resolution,
            },
            alternative_offer=alternative_offer(
                reason_code="requested_location_no_availability",
                requested_outcome="The selected vehicle at Manchester",
                failure_reason="Manchester has no matching appointment.",
                offered_outcome="The same vehicle at Stockport.",
                changes=[
                    {
                        "dimension": "Dealership",
                        "requested": "Manchester",
                        "offered": "Stockport",
                    }
                ],
                candidate_references=["appointment:td-slot-0280"],
            ),
        ),
    )
    unsupported_ranking = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="warning",
                segments=[
                    TextSegment(
                        type="text",
                        text="The nearest available appointment is at another dealership.",
                    )
                ],
            ),
            GroundedMessageDraft(
                purpose="workflow_prompt",
                segments=[TextSegment(type="text", text="Would you like it?")],
            ),
        ]
    )

    with pytest.raises(ValueError, match="distance evidence"):
        GroundingValidator().resolve(
            unsupported_ranking,
            [result],
            workflow_state={
                "activeWorkflow": "test_drive",
                "entities": {"vehicleId": "veh-042"},
                "constraints": {"missingPublicFields": ["slotId"]},
            },
        )


def test_negative_appointment_claim_requires_a_trusted_empty_result() -> None:
    result = ToolResultEnvelope(
        resultId="result-ready-test-drive",
        callId="call-ready-test-drive",
        intentId="intent-ready-test-drive",
        tool="list_test_drive_slots",
        status="success",
        entities=[],
        facts=[],
        observedAt=datetime.now(UTC),
    )
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="warning",
                segments=[
                    TextSegment(
                        type="text",
                        text=(
                            "There are currently no test-drive slots available at the "
                            "Manchester dealership."
                        ),
                    )
                ],
            )
        ]
    )

    with pytest.raises(ValueError, match="trusted empty result"):
        GroundingValidator().resolve(draft, [result])


def test_available_slot_is_one_trusted_finite_choice_in_uk_local_time() -> None:
    resolution = resolve_test_drive_slots({"schedulePreferenceMode": "clear"}, count=1)
    transition = transition_test_drive(resolution)
    assert resolution["continuation"] == "request_slot_selection"
    assert transition["missingPublicFields"] == ["slotId"]

    result = FactNormalizer().normalize(
        result_id="result-one-slot",
        call_id="call-one-slot",
        intent_id="intent-one-slot",
        tool="list_test_drive_slots",
        result=ToolResult(
            "One slot found.",
            "test_drive_slot_picker",
            {
                "version": 1,
                "vehicleId": "veh-042",
                "collectionPresentation": {
                    "schemaVersion": 1,
                    "layout": "chip_grid",
                    "purpose": "choice",
                    "items": [{"label": "Thu, 10 Sept 2026, 17:00"}],
                },
                "items": [
                    {
                        "id": "td-slot-0280",
                        "vehicleId": "veh-042",
                        "dealershipId": "northstar-stockport",
                        "startsAt": "2026-09-10T16:00:00Z",
                    }
                ],
            },
            {
                "items": [{"id": "td-slot-0280", "startsAt": "2026-09-10T16:00:00Z"}],
                "resolution": resolution,
            },
        ),
    )

    assert any(entity.reference == "appointment:td-slot-0280" for entity in result.entities)
    assert result.responseObligation is not None
    assert result.responseObligation.choiceMode == "confirm_single"
    assert result.responseObligation.candidateReferences == ["appointment:td-slot-0280"]
    time_fact = next(fact for fact in result.facts if fact.field.endswith("startsAt"))
    assert time_fact.displayValue == "Thu 10 Sep at 17:00"

    prompt = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="workflow_prompt",
                segments=[TextSegment(type="text", text="Would you like this appointment?")],
            )
        ]
    )
    resolved = GroundingValidator().resolve(
        prompt,
        [result],
        workflow_state={
            "activeWorkflow": "test_drive",
            "entities": {"vehicleId": "veh-042"},
            "constraints": {"missingPublicFields": ["slotId"]},
        },
    )
    assert resolved.messages[-1].collection_reference == "collection:result-one-slot"


def test_customer_response_cannot_narrate_internal_availability_recovery() -> None:
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="workflow_prompt",
                segments=[
                    TextSegment(
                        type="text",
                        text=(
                            "I currently only have workshop availability recovery for Stockport. "
                            "What day works for you?"
                        ),
                    )
                ],
            )
        ]
    )

    with pytest.raises(ValueError, match="internal workflow terminology"):
        GroundingValidator().resolve(draft, [])


def test_catalogue_card_owns_vehicle_model_names_without_prose_duplication() -> None:
    now = datetime.now(UTC)
    inventory = ToolResultEnvelope(
        resultId="result-bmw-catalogue",
        callId="call-bmw-catalogue",
        intentId="intent-bmw-catalogue",
        tool="search_vehicles",
        status="success",
        availableCards=[
            AvailableCard(
                reference="card:result-bmw-catalogue:vehicle_preview",
                type="vehicle_preview",
                data={
                    "version": 1,
                    "items": [
                        {"id": "veh-014", "make": "BMW", "model": "3 Series"},
                        {"id": "veh-049", "make": "BMW", "model": "1 Series"},
                    ],
                },
            )
        ],
        responseObligation=ResponseObligation(
            kind="catalogue_results",
            subjectReferences=["vehicle:veh-014", "vehicle:veh-049"],
            requiredCardType="vehicle_preview",
        ),
        observedAt=now,
    )
    duplicate = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[
                    TextSegment(
                        type="text",
                        text="Options include the BMW 3 Series and BMW 1 Series.",
                    )
                ],
            )
        ],
        cardReferences=["card:result-bmw-catalogue:vehicle_preview"],
    )

    duplicated = GroundingValidator().resolve(duplicate, [inventory])
    assert "BMW 3 Series" in duplicated.messages[0].text

    dead_end = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[TextSegment(type="text", text="The matching options are shown below.")],
            )
        ],
        cardReferences=["card:result-bmw-catalogue:vehicle_preview"],
    )
    completed = GroundingValidator().resolve(dead_end, [inventory])
    assert completed.messages[0].text == "The matching options are shown below."

    concise = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="follow_up",
                segments=[
                    TextSegment(
                        type="text",
                        text="Which BMW would you like me to check for test-drive times?",
                    )
                ],
            )
        ],
        cardReferences=["card:result-bmw-catalogue:vehicle_preview"],
    )
    resolved = GroundingValidator().resolve(concise, [inventory])
    assert resolved.messages[0].text.startswith("Which BMW")


def test_grounding_attaches_trusted_collections_without_failing_on_presentation() -> None:
    now = datetime.now(UTC)
    collection = AvailableCollection(
        reference="collection:result-services",
        viewType="service_list",
        presentation=CollectionPresentation(
            layout="chip_grid",
            purpose="choice",
            items=[
                {
                    "label": "Brake inspection",
                    "description": "Brake condition and performance inspection.",
                },
                {"label": "MOT", "description": "Annual MOT inspection."},
            ],
        ),
    )
    result = ToolResultEnvelope(
        resultId="result-services",
        callId="call-services",
        intentId="intent-services",
        tool="list_workshop_slots",
        status="success",
        facts=[
            AtomicFact(
                factId="fact-service-brake-name",
                field="items.0.name",
                value="Brake inspection",
                displayValue="Brake inspection",
                source="dealership_platform",
                sensitivity="public",
                observedAt=now,
            ),
            AtomicFact(
                factId="fact-service-mot-name",
                field="items.1.name",
                value="MOT",
                displayValue="MOT",
                source="dealership_platform",
                sensitivity="public",
                observedAt=now,
            ),
        ],
        availableCollections=[collection],
        responseObligation=ResponseObligation(
            kind="location_resolution",
            subjectReferences=["service:brake-inspection", "service:mot"],
            choiceMode="choose_multiple",
            candidateReferences=["service:brake-inspection", "service:mot"],
        ),
        observedAt=now,
    )
    missing = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="workflow_prompt",
                segments=[TextSegment(type="text", text="Which service would you like?")],
            )
        ]
    )
    automatically_attached = GroundingValidator().resolve(missing, [result])
    assert automatically_attached.messages[0].collection_reference == collection.reference

    attached = missing.model_copy(
        update={
            "messages": [
                missing.messages[0].model_copy(update={"collectionReference": collection.reference})
            ]
        }
    )
    resolved = GroundingValidator().resolve(attached, [result])
    assert resolved.messages[0].collection_reference == collection.reference

    flattened = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="workflow_prompt",
                collectionReference=collection.reference,
                segments=[
                    TextSegment(
                        type="text",
                        text="Choose Brake inspection or MOT.",
                    )
                ],
            )
        ]
    )
    flattened_result = GroundingValidator().resolve(flattened, [result])
    assert len(flattened_result.messages) == 1
    assert flattened_result.messages[0].text == "Which option would you like?"
    assert flattened_result.messages[0].collection_reference == collection.reference

    named_question = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="workflow_prompt",
                collectionReference=collection.reference,
                segments=[
                    TextSegment(
                        type="text",
                        text="Would you like MOT or another service?",
                    )
                ],
            )
        ]
    )
    named_question_result = GroundingValidator().resolve(named_question, [result])
    assert named_question_result.messages[0].text == "Would you like MOT or another service?"
    assert named_question_result.messages[0].collection_reference == collection.reference

    ai_owned_list = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="workflow_prompt",
                collectionReference=collection.reference,
                blocks=[
                    {
                        "type": "paragraph",
                        "segments": [{"type": "text", "text": "Which service would you like?"}],
                    },
                    {
                        "type": "list",
                        "items": [
                            {"segments": [{"type": "text", "text": "Brake inspection"}]},
                            {"segments": [{"type": "text", "text": "MOT"}]},
                        ],
                    },
                ],
            )
        ]
    )
    ai_owned_result = GroundingValidator().resolve(ai_owned_list, [result])
    assert ai_owned_result.messages[0].collection_reference == collection.reference
    assert ai_owned_result.messages[0].collection_rendered_in_blocks is False
    assert [block["type"] for block in ai_owned_result.messages[0].blocks] == ["paragraph"]

    duplicated_across_messages = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                blocks=[
                    {
                        "type": "list",
                        "items": [
                            {
                                "segments": [
                                    FactSegment(
                                        type="fact",
                                        factId="fact-service-brake-name",
                                    )
                                ]
                            },
                            {
                                "segments": [
                                    FactSegment(
                                        type="fact",
                                        factId="fact-service-mot-name",
                                    )
                                ]
                            },
                        ],
                    }
                ],
            ),
            GroundedMessageDraft(
                purpose="workflow_prompt",
                collectionReference=collection.reference,
                segments=[TextSegment(type="text", text="Which service would you like?")],
            ),
        ]
    )
    single_owner = GroundingValidator().resolve(duplicated_across_messages, [result])
    assert len(single_owner.messages) == 1
    assert single_owner.messages[0].text == "Which service would you like?"
    assert single_owner.messages[0].collection_reference == collection.reference
    assert single_owner.messages[0].collection_rendered_in_blocks is False

    choice_with_partial_quick_replies = ai_owned_list.model_copy(
        update={
            "quickReplies": [QuickReplyDraft(label="Brake inspection", message="Brake inspection")]
        }
    )
    without_biased_subset = GroundingValidator().resolve(
        choice_with_partial_quick_replies,
        [result],
    )
    assert without_biased_subset.suggestions == ()


@pytest.mark.parametrize(
    ("orphaned_lead_in", "tool", "status", "context_text"),
    [
        (
            "The next available appointment is:",
            "list_test_drive_slots",
            "empty",
            "No appointments matched the requested schedule.",
        ),
        (
            "Here are the workshop services:",
            "list_workshop_slots",
            "success",
            "These workshop services are currently available.",
        ),
    ],
)
def test_application_owned_collection_removes_orphaned_colon_lead_ins(
    orphaned_lead_in: str,
    tool: str,
    status: str,
    context_text: str,
) -> None:
    collection = AvailableCollection(
        reference="collection:result-choice",
        viewType="choice_list",
        presentation=CollectionPresentation(
            layout="bullet_list",
            purpose="choice",
            items=[{"label": "Monday at 10:00"}],
        ),
    )
    result = ToolResultEnvelope(
        resultId="result-choice",
        callId="call-choice",
        intentId="intent-choice",
        tool=tool,
        status=status,
        availableCollections=[collection],
        observedAt=datetime.now(UTC),
    )
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[
                    TextSegment(
                        type="text",
                        text=context_text,
                    )
                ],
            ),
            GroundedMessageDraft(
                purpose="transition",
                segments=[TextSegment(type="text", text=orphaned_lead_in)],
            ),
            GroundedMessageDraft(
                purpose="workflow_prompt",
                segments=[TextSegment(type="text", text="Would this option suit you?")],
            ),
        ]
    )

    resolved = GroundingValidator().resolve(draft, [result])

    assert [message.text for message in resolved.messages] == [
        context_text,
        "Would this option suit you?",
    ]
    assert resolved.messages[-1].collection_reference == collection.reference
    assert all(not message.text.rstrip().endswith(":") for message in resolved.messages)


def test_trusted_card_removes_orphaned_colon_lead_in_at_shared_visual_boundary() -> None:
    card = AvailableCard(
        reference="card:result-vehicle:vehicle_preview",
        type="vehicle_preview",
        data={
            "version": 1,
            "items": [{"id": "veh-001", "make": "BMW", "model": "3 Series"}],
        },
    )
    result = ToolResultEnvelope(
        resultId="result-vehicle",
        callId="call-vehicle",
        intentId="intent-vehicle",
        tool="search_vehicles",
        status="success",
        availableCards=[card],
        observedAt=datetime.now(UTC),
    )
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[TextSegment(type="text", text="The matching vehicle is:")],
            )
        ],
        cardReferences=[card.reference],
    )

    resolved = GroundingValidator().resolve(draft, [result])

    assert [message.text for message in resolved.messages] == [
        "The matching vehicle is:",
        "What else can I help you with?",
    ]
    assert resolved.cards == (card,)


@pytest.mark.parametrize(
    "incomplete_text",
    [
        "The comparison has higher mileage at",
        "The available finance options include",
        "The answer is:",
    ],
)
def test_incomplete_sentence_blocks_are_never_returned_without_a_visual(
    incomplete_text: str,
) -> None:
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[TextSegment(type="text", text=incomplete_text)],
            )
        ]
    )

    resolved = GroundingValidator().resolve(draft, [])

    assert resolved.messages[0].text == "I couldn’t present that response clearly."
    assert incomplete_text not in resolved.messages[0].text


def test_colon_introduction_is_complete_when_followed_by_an_inline_list() -> None:
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                blocks=[
                    {
                        "type": "paragraph",
                        "segments": [{"type": "text", "text": "Available services:"}],
                    },
                    {
                        "type": "list",
                        "items": [
                            {"segments": [{"type": "text", "text": "MOT"}]},
                            {"segments": [{"type": "text", "text": "Full service"}]},
                        ],
                    },
                ],
            )
        ]
    )

    resolved = GroundingValidator().resolve(draft, [])

    assert resolved.messages[0].text == "Available services:\n- MOT\n- Full service"


def test_persistence_boundary_prunes_incomplete_legacy_and_fallback_messages() -> None:
    messages = [
        {"text": "The requested information is ready.", "purpose": "answer"},
        {"text": "The next available result is:", "purpose": "transition"},
    ]

    _normalize_persisted_message_completeness(messages)

    assert messages == [{"text": "The requested information is ready.", "purpose": "answer"}]


def test_persistence_boundary_preserves_visual_and_its_grammatical_introduction() -> None:
    messages = [
        {
            "text": "The matching vehicle is:",
            "purpose": "answer",
            "viewType": "grounded_presentation",
            "view": {"version": 1, "cards": [{"type": "vehicle_preview"}]},
            "segments": [{"type": "text", "text": "The matching vehicle is:"}],
            "blocks": [
                {
                    "type": "paragraph",
                    "segments": [{"type": "text", "text": "The matching vehicle is:"}],
                }
            ],
        }
    ]

    _normalize_persisted_message_completeness(messages)

    assert messages[0]["text"] == "The matching vehicle is:"
    assert messages[0]["viewType"] == "grounded_presentation"
    assert messages[0]["segments"] == [{"type": "text", "text": "The matching vehicle is:"}]


def test_finite_workflow_choice_owns_namespace_state_and_durable_question() -> None:
    items = [
        {"id": "brake-inspection", "name": "Brake inspection"},
        {"id": "diagnostic", "name": "Diagnostic inspection"},
    ]
    choice_contract = {
        "selectionOnly": True,
        "choiceEntityType": "service",
        "choiceField": "serviceTypeId",
        "items": items,
    }
    result = ToolResult(
        "Which service would you like to book?",
        "service_list",
        {
            "version": 1,
            **choice_contract,
            "collectionViewType": "choice_list",
            "collectionPresentation": {
                "schemaVersion": 1,
                "layout": "bullet_list",
                "purpose": "choice",
                "items": [{"label": item["name"]} for item in items],
            },
        },
        choice_contract,
    )

    normalized = FactNormalizer().normalize(
        result_id="result-services",
        call_id="call-services",
        intent_id="intent-services",
        tool="list_workshop_slots",
        result=result,
    )
    workflow = WorkflowStateReducer().advance(
        {},
        "list_workshop_slots",
        {"intentKind": "workshop_booking"},
        result.view_type,
        result.facts,
    )
    question = _workflow_dialogue_question(
        [
            {
                "id": "message-services",
                "text": "Which service would you like to book?",
                "purpose": "workflow_prompt",
            }
        ],
        [normalized],
        workflow,
        "replace",
        1,
    )

    assert [entity.reference for entity in normalized.entities] == [
        "service:brake-inspection",
        "service:diagnostic",
    ]
    assert normalized.availableCollections[0].viewType == "choice_list"
    assert workflow["stage"] == "choosing_service"
    assert workflow["constraints"]["missingPublicFields"] == ["serviceTypeId"]
    assert question is not None
    assert question["kind"] == "reference_choice"
    assert question["expectedFields"] == ["serviceTypeId"]
    assert question["candidateReferences"] == [
        "service:brake-inspection",
        "service:diagnostic",
    ]


@pytest.mark.parametrize(
    ("workflow", "field", "choice_entity_type", "labels"),
    [
        ("callback", "department", "workflow_option", ["Sales", "Service", "Parts"]),
        (
            "dealership_message",
            "department",
            "workflow_option",
            ["Sales", "Service", "Parts", "General enquiries"],
        ),
        (
            "sales_enquiry",
            "enquiryType",
            "workflow_option",
            ["General enquiry", "Vehicle availability", "Finance", "Part exchange"],
        ),
        (
            "dealership_message",
            "preferredContactMethod",
            "workflow_option",
            ["Email", "Phone"],
        ),
        ("workshop_booking", "serviceTypeId", "service", ["MOT", "Full service"]),
        ("test_drive", "vehicleId", "vehicle", ["MINI Cooper", "Volvo XC40"]),
        ("callback", "dealershipId", "dealership", ["Bolton", "Stockport"]),
    ],
)
def test_unanswered_workflow_choice_replays_the_complete_persisted_set(
    workflow: str,
    field: str,
    choice_entity_type: str,
    labels: list[str],
) -> None:
    view = {
        "version": 1,
        "selectionOnly": True,
        "choiceEntityType": choice_entity_type,
        "choiceField": field,
        "items": [
            {"id": f"choice-{index}", "name": label} for index, label in enumerate(labels, start=1)
        ],
        "collectionPresentation": {
            "schemaVersion": 1,
            "layout": "chip_grid",
            "purpose": "choice",
            "items": [{"label": label} for label in labels],
        },
    }
    state = ConversationState.model_validate(
        {
            "agentWorkflow": {
                "version": 3,
                "activeWorkflow": workflow,
                "stage": "collecting_public",
                "entities": {},
                "constraints": {"missingPublicFields": [field]},
            },
            "dialogue": {
                "activeQuestion": {
                    "questionId": "question-current-choice",
                    "kind": "input",
                    "prompt": "Which option would you like?",
                    "goalIntent": CapabilityRegistry.goal_intent_for_active_workflow(workflow),
                    "expectedFields": [field],
                    "originatingMessageId": "message-current-choice",
                    "createdAtStateVersion": 1,
                }
            },
        }
    )
    messages = [
        SimpleNamespace(
            id="message-current-choice",
            view_type="choice_list",
            view_payload_json=json.dumps(view),
        )
    ]

    assert _retained_question_choice_view(state, messages) == ("choice_list", view)


def test_finite_workflow_choice_cannot_render_without_a_semantic_target() -> None:
    with pytest.raises(ValueError, match="declare its entity type and target field"):
        FactNormalizer().normalize(
            result_id="result-invalid-choice",
            call_id="call-invalid-choice",
            intent_id="intent-invalid-choice",
            tool="list_workshop_slots",
            result=ToolResult(
                "Which service would you like?",
                "service_list",
                {
                    "selectionOnly": True,
                    "items": [{"id": "mot", "name": "MOT"}],
                    "collectionPresentation": {
                        "schemaVersion": 1,
                        "layout": "bullet_list",
                        "purpose": "choice",
                        "items": [{"label": "MOT"}],
                    },
                },
                {"selectionOnly": True, "items": [{"id": "mot", "name": "MOT"}]},
            ),
        )


def test_information_collection_may_remain_ai_rendered_without_a_duplicate_view() -> None:
    collection = AvailableCollection(
        reference="collection:result-services",
        viewType="service_list",
        presentation=CollectionPresentation(
            layout="bullet_list",
            purpose="information",
            items=[{"label": "MOT"}, {"label": "Full service"}],
        ),
    )
    result = ToolResultEnvelope(
        resultId="result-services",
        callId="call-services",
        intentId="intent-services",
        tool="list_service_types",
        status="success",
        availableCollections=[collection],
        observedAt=datetime.now(UTC),
    )
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                collectionReference=collection.reference,
                blocks=[
                    {
                        "type": "list",
                        "items": [
                            {"segments": [{"type": "text", "text": "MOT"}]},
                            {"segments": [{"type": "text", "text": "Full service"}]},
                        ],
                    }
                ],
            )
        ]
    )

    resolved = GroundingValidator().resolve(draft, [result])

    assert resolved.messages[0].collection_reference == collection.reference
    assert resolved.messages[0].collection_rendered_in_blocks is True
    assert [block["type"] for block in resolved.messages[0].blocks] == ["list"]


def test_multi_fact_grounded_answers_resolve_semantic_bullets() -> None:
    now = datetime.now(UTC)
    facts = [
        AtomicFact(
            factId=f"fact-one-{field}",
            field=f"service.{field}",
            value=value,
            displayValue=display,
            source="dealership_platform",
            sensitivity="public",
            observedAt=now,
        )
        for field, value, display in (
            ("price", 14900, "£149.00"),
            ("duration", 90, "90 minutes"),
        )
    ]
    result = ToolResultEnvelope(
        resultId="result-bullets",
        callId="call-bullets",
        intentId="intent-bullets",
        tool="get_service_information",
        status="success",
        facts=facts,
        observedAt=now,
    )
    message = GroundedMessageDraft(
        purpose="answer",
        segments=[
            TextSegment(type="text", text="Service details:"),
            BulletSegment(type="bullet"),
            TextSegment(type="text", text="Price: "),
            FactSegment(type="fact", factId="fact-one-price"),
            BulletSegment(type="bullet"),
            TextSegment(type="text", text="Duration: "),
            FactSegment(type="fact", factId="fact-one-duration"),
        ],
    )
    resolved = (
        GroundingValidator()
        .resolve(GroundedResponseDraft(messages=[message]), [result])
        .messages[0]
    )
    assert resolved.text == "Service details:\n- Price: £149.00\n- Duration: 90 minutes"
    assert [segment["type"] for segment in resolved.segments] == [
        "text",
        "bullet",
        "text",
        "fact",
        "bullet",
        "text",
        "fact",
    ]
    assert resolved.segments[3] == {
        "type": "fact",
        "factId": "fact-one-price",
        "text": "£149.00",
    }


@pytest.mark.parametrize(
    "unsafe_text",
    [
        "It is available at 10:30.",
        "The offer ends on 2026-09-30.",
        "It is a 2025 model.",
        "The work takes 90 minutes.",
        "The rate is 5.9% APR.",
    ],
)
def test_grounding_rejects_unreferenced_dates_times_years_and_rates(unsafe_text: str) -> None:
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[TextSegment(type="text", text=unsafe_text)],
            )
        ]
    )
    with pytest.raises(ValueError, match="trusted fact"):
        GroundingValidator().resolve(draft, [])


def test_workflow_question_shape_is_not_a_grounding_failure() -> None:
    state = {
        "version": 3,
        "activeWorkflow": "workshop_booking",
        "stage": "choosing_time",
        "entities": {"serviceTypeId": "brake-inspection"},
        "constraints": {
            "missingPublicFields": ["preferredDayOrDate", "approximateTime"],
        },
    }
    invalid = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[TextSegment(type="text", text="I found some appointments.")],
            )
        ]
    )

    imperfect = GroundingValidator().resolve(invalid, [], workflow_state=state)
    assert imperfect.messages[0].text == "I found some appointments."

    valid = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="workflow_prompt",
                segments=[
                    TextSegment(
                        type="text",
                        text="What workshop appointment would suit you? Please include:",
                    ),
                    BulletSegment(type="bullet"),
                    TextSegment(type="text", text="A preferred day or date"),
                    BulletSegment(type="bullet"),
                    TextSegment(type="text", text="An approximate time"),
                ],
            )
        ]
    )

    resolved = GroundingValidator().resolve(valid, [], workflow_state=state)
    assert resolved.messages[0].text.endswith("- An approximate time")


def test_grounding_rejects_vehicle_selection_claim_without_persisted_vehicle() -> None:
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="workflow_prompt",
                segments=[
                    TextSegment(
                        type="text",
                        text="You selected option one. What day would suit you?",
                    )
                ],
            )
        ]
    )

    with pytest.raises(ValueError, match="persisted selection evidence"):
        GroundingValidator().resolve(
            draft,
            [],
            workflow_state={
                "activeWorkflow": "test_drive",
                "stage": "choosing_vehicle",
                "entities": {},
                "constraints": {"missingPublicFields": ["vehicleId"]},
            },
        )


def test_grounding_does_not_use_slot_presentation_as_a_hard_failure() -> None:
    now = datetime.now(UTC)
    result = ToolResultEnvelope(
        resultId="result-broad-slots",
        callId="call-broad-slots",
        intentId="intent-broad-slots",
        tool="list_workshop_slots",
        status="success",
        facts=[
            AtomicFact(
                factId="fact-broad-slot-time",
                field="items.1.startsAt",
                value="2026-09-05T10:00:00+01:00",
                displayValue="Sat 05 Sep at 10:00",
                source="dealership_platform",
                sensitivity="public",
                observedAt=now,
            )
        ],
        observedAt=now,
    )
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="workflow_prompt",
                segments=[
                    TextSegment(type="text", text="Available times:"),
                    BulletSegment(type="bullet"),
                    FactSegment(type="fact", factId="fact-broad-slot-time"),
                    BulletSegment(type="bullet"),
                    TextSegment(type="text", text="Tell me another preference"),
                ],
            )
        ]
    )
    state = {
        "version": 3,
        "activeWorkflow": "workshop_booking",
        "stage": "choosing_time",
        "entities": {},
        "constraints": {
            "missingPublicFields": ["preferredDayOrDate", "approximateTime"],
        },
    }

    resolved = GroundingValidator().resolve(draft, [result], workflow_state=state)
    assert "Sat 05 Sep at 10:00" in resolved.messages[0].text


def test_grounding_does_not_reject_a_safe_long_appointment_shortlist() -> None:
    now = datetime.now(UTC)
    result = ToolResultEnvelope(
        resultId="result-refined-slots",
        callId="call-refined-slots",
        intentId="intent-refined-slots",
        tool="refine_workshop_slots",
        status="success",
        facts=[
            AtomicFact(
                factId=f"fact-refined-slot-{index}",
                field=f"items.{index}.startsAt",
                value=f"2026-09-05T{index + 8:02d}:00:00+01:00",
                displayValue=f"Sat 05 Sep at {index + 8:02d}:00",
                source="dealership_platform",
                sensitivity="public",
                observedAt=now,
            )
            for index in range(1, 6)
        ],
        observedAt=now,
    )
    segments = [TextSegment(type="text", text="Available times:")]
    for fact in result.facts:
        segments.extend(
            [
                BulletSegment(type="bullet"),
                FactSegment(type="fact", factId=fact.factId),
            ]
        )
    draft = GroundedResponseDraft(
        messages=[GroundedMessageDraft(purpose="workflow_prompt", segments=segments)]
    )
    state = {
        "version": 3,
        "activeWorkflow": "workshop_booking",
        "stage": "choosing_time",
        "entities": {},
        "constraints": {"missingPublicFields": ["slotId"]},
    }

    resolved = GroundingValidator().resolve(draft, [result], workflow_state=state)
    assert resolved.messages[0].text.count("Sat 05 Sep") == 5


def test_grounding_resolves_only_protocol_matched_trusted_links() -> None:
    now = datetime.now(UTC)
    telephone = AvailableLink(
        reference="link:dealer-phone",
        label="0161 555 0100",
        href="tel:01615550100",
        source="dealership_platform",
        destinationKind="telephone",
    )
    result = ToolResultEnvelope(
        resultId="result-links",
        callId="call-links",
        intentId="intent-links",
        tool="get_dealership_information",
        status="success",
        availableLinks=[telephone],
        observedAt=now,
    )
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[
                    TextSegment(type="text", text="You can call us on "),
                    LinkSegment(type="link", linkReference="link:dealer-phone"),
                    TextSegment(type="text", text="."),
                ],
            )
        ],
        linkReferences=["link:dealer-phone"],
    )
    resolved = GroundingValidator().resolve(draft, [result])
    assert resolved.messages[0].segments[1] == {
        "type": "link",
        "label": "0161 555 0100",
        "href": "tel:01615550100",
        "destinationKind": "telephone",
    }

    mismatched = result.model_copy(
        update={"availableLinks": [telephone.model_copy(update={"destinationKind": "email"})]}
    )
    with pytest.raises(ValueError, match="protocol"):
        GroundingValidator().resolve(draft, [mismatched])


def test_grounding_rejects_unapproved_web_link_hosts() -> None:
    now = datetime.now(UTC)
    result = ToolResultEnvelope(
        resultId="result-web-link",
        callId="call-web-link",
        intentId="intent-web-link",
        tool="get_dealership_information",
        status="success",
        availableLinks=[
            AvailableLink(
                reference="link:website",
                label="Website",
                href="https://untrusted.example/",
                source="dealership_platform",
                destinationKind="website",
            )
        ],
        observedAt=now,
    )
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[LinkSegment(type="link", linkReference="link:website")],
            )
        ],
        linkReferences=["link:website"],
    )
    with pytest.raises(ValueError, match="allow-listed"):
        GroundingValidator().resolve(draft, [result])


def test_part_exchange_normalization_drops_private_inputs_but_keeps_the_valuation() -> None:
    envelope = FactNormalizer().normalize(
        result_id="result-estimate",
        call_id="call-estimate",
        intent_id="intent-estimate",
        tool="estimate_part_exchange",
        result=ToolResult(
            "ignored tool prose",
            "part_exchange_estimate",
            {
                "registration": "AB19 XYZ",
                "mileage": 45_000,
                "condition": "good",
                "estimateLowPence": 1_000_000,
                "estimateHighPence": 1_150_000,
                "estimateNotice": "Indicative only.",
            },
            {
                "registration": "AB19 XYZ",
                "mileage": 45_000,
                "condition": "good",
                "estimateLowPence": 1_000_000,
                "estimateHighPence": 1_150_000,
                "estimateNotice": "Indicative only.",
            },
        ),
    )
    serialized = envelope.model_dump_json().casefold()
    assert "ab19" not in serialized
    assert '"mileage"' not in serialized
    assert '"condition"' not in serialized
    assert "£10,000.00" in serialized
    assert envelope.availableCards[0].data["estimateLowPence"] == 1_000_000


def test_approved_content_enters_composition_as_a_typed_fact() -> None:
    envelope = FactNormalizer().normalize_approved_content(
        result_id="result-knowledge",
        intent_id="intent-knowledge",
        entries=(
            {
                "id": "customer.pch",
                "title": "PCH",
                "text": "PCH means Personal Contract Hire.",
                "source": "docs/CUSTOMER-KNOWLEDGE.md#PCH",
            },
        ),
    )
    fact = envelope.facts[0]
    assert fact.source == "approved_content"
    assert fact.displayValue == "PCH means Personal Contract Hire."
    assert envelope.availableCards == []


def test_latest_interaction_requires_an_isolated_decision_and_exact_state_version() -> None:
    interaction = PendingInteractionState(
        interactionId="interaction-one",
        kind="open_vehicle_detail",
        trustedEntity=TrustedEntity(type="vehicle", reference="vehicle:veh-041"),
        originatingMessageId="message-one",
        createdAtStateVersion=3,
        status="awaiting_confirmation",
    )
    assert resolve_latest(interaction, state_version=3, text="go ahead").decision == "confirm"
    assert resolve_latest(interaction, state_version=4, text="yes") is None
    assert explicit_decision("yes, but is it available?") is None
    assert explicit_decision("don't open it") == "cancel"
    assert explicit_decision("Please confirm this request") == "confirm"
    assert explicit_decision("I want to cancel this request") == "cancel"
    assert explicit_decision("I want to cancel if the price changed") is None


def test_navigation_confirmation_wording_is_ai_composed_but_controls_are_mandatory() -> None:
    envelope = FactNormalizer().normalize(
        result_id="result-navigation",
        call_id="call-navigation",
        intent_id="intent-navigation",
        tool="propose_vehicle_navigation",
        result=ToolResult(
            "ignored",
            "suggestion_list",
            {
                "version": 1,
                "suggestions": [
                    {
                        "label": "Open vehicle",
                        "message": "Yes, open the vehicle",
                        "action": {"type": "confirm_active_interaction"},
                    },
                    {
                        "label": "Stay in chat",
                        "message": "No, stay in the chat",
                        "action": {"type": "cancel_active_interaction"},
                    },
                ],
            },
            {"navigationConfirmationRequested": True},
        ),
    )
    references = [item.reference for item in envelope.availableSuggestions]
    valid = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="follow_up",
                segments=[
                    TextSegment(
                        type="text",
                        text="Would you like me to open the full vehicle details?",
                    )
                ],
            )
        ],
        suggestionReferences=references,
    )
    resolved = GroundingValidator().resolve(valid, [envelope])
    assert resolved.messages[0].purpose == "follow_up"

    normalized = GroundingValidator().resolve(
        valid.model_copy(update={"suggestionReferences": []}),
        [envelope],
    )
    assert [item.reference for item in normalized.suggestions] == references


def test_department_answer_owns_details_and_requires_executable_next_steps() -> None:
    envelope = FactNormalizer().normalize(
        result_id="result-manchester-departments",
        call_id="call-manchester-departments",
        intent_id="intent-manchester-departments",
        tool="find_dealership_departments",
        result=ToolResult(
            "ignored tool prose",
            "dealership_list",
            {
                "version": 1,
                "items": [
                    {
                        "id": "northstar-manchester",
                        "name": "Northstar Manchester",
                        "town": "Manchester",
                        "departments": ["parts", "sales", "service"],
                    }
                ],
                "suggestions": [
                    {
                        "label": "Request a callback",
                        "text": "please have the dealership call me",
                        "action": {"type": "start_callback"},
                    },
                    {
                        "label": "Leave a message",
                        "text": "send a message to the dealership",
                        "action": {"type": "start_dealership_message"},
                    },
                ],
            },
            {
                "items": [
                    {
                        "id": "northstar-manchester",
                        "name": "Northstar Manchester",
                        "town": "Manchester",
                        "departments": ["parts", "sales", "service"],
                    }
                ]
            },
        ),
    )

    assert envelope.availableCards == []
    assert envelope.responseObligation is not None
    assert envelope.responseObligation.kind == "informational_next_steps"

    dead_end = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[TextSegment(type="text", text="Those are the available departments.")],
            )
        ]
    )
    normalized_dead_end = GroundingValidator().resolve(dead_end, [envelope])
    assert [message.purpose for message in normalized_dead_end.messages] == [
        "answer",
        "follow_up",
    ]
    assert normalized_dead_end.messages[-1].text == (
        "Which of these options would you like help with next?"
    )
    assert [item.action["type"] for item in normalized_dead_end.suggestions] == [
        "start_callback",
        "start_dealership_message",
    ]

    draft = GroundedResponseDraft(
        messages=[
            dead_end.messages[0],
            GroundedMessageDraft(
                purpose="follow_up",
                segments=[
                    TextSegment(
                        type="text",
                        text="Would you like a callback or to leave a message?",
                    )
                ],
            ),
        ],
        quickReplies=[
            QuickReplyDraft(
                label="Request a callback",
                message="please have the dealership call me",
            ),
            QuickReplyDraft(
                label="Leave a message",
                message="send a message to the dealership",
            ),
        ],
    )
    resolved = GroundingValidator().resolve(draft, [envelope])

    assert [item.action["type"] for item in resolved.suggestions] == [
        "start_callback",
        "start_dealership_message",
    ]


def test_information_without_suggestion_candidates_still_gets_an_open_follow_up() -> None:
    envelope = FactNormalizer().normalize(
        result_id="result-finance-information",
        call_id="call-finance-information",
        intent_id="intent-finance-information",
        tool="get_business_information",
        result=ToolResult(
            "Here is the current finance information.",
            "business_information",
            {"version": 1, "topic": "finance", "facts": []},
            {"topic": "finance", "outcome": "success"},
        ),
    )
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[TextSegment(type="text", text="That is the current information.")],
            )
        ]
    )

    resolved = GroundingValidator().resolve(draft, [envelope])

    assert envelope.responseObligation is None
    assert resolved.messages[-1].purpose == "follow_up"
    assert resolved.messages[-1].text == "What else can I help you with?"
    assert resolved.suggestions == ()


def test_follow_up_never_mentions_options_when_no_control_survives_normalization() -> None:
    now = datetime.now(UTC)
    envelope = ToolResultEnvelope(
        resultId="result-service-information",
        callId="call-service-information",
        intentId="intent-service-information",
        tool="get_service_information",
        status="success",
        facts=[],
        availableSuggestions=[
            AvailableSuggestion(
                reference="suggestion:result-service-information:1",
                label="Book this service",
                message="Book this service",
            )
        ],
        observedAt=now,
    )
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[
                    TextSegment(type="text", text="That is the current service information.")
                ],
            )
        ]
    )

    resolved = GroundingValidator().resolve(draft, [envelope])

    assert resolved.messages[-1].text == "What else can I help you with?"
    assert resolved.suggestions == ()


def test_vehicle_catalogue_materializes_result_scoped_controls_when_ai_omits_them() -> None:
    envelope = FactNormalizer().normalize(
        result_id="result-vehicles",
        call_id="call-vehicles",
        intent_id="intent-vehicles",
        tool="search_vehicles",
        result=ToolResult(
            "ignored",
            "vehicle_list",
            {
                "version": 1,
                "items": [
                    {"id": "veh-001", "make": "BMW", "model": "1 Series"},
                    {"id": "veh-002", "make": "MINI", "model": "Countryman"},
                ],
                "suggestions": [
                    {
                        "label": "Show me more",
                        "text": "show me more",
                        "action": {"type": "next_vehicle_page"},
                    },
                    {
                        "label": "Compare these vehicles",
                        "text": "compare the vehicles currently shown",
                        "action": {"type": "compare_displayed_vehicles"},
                    },
                ],
            },
            {"items": [{"id": "veh-001"}, {"id": "veh-002"}]},
        ),
    )
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="follow_up",
                segments=[
                    TextSegment(
                        type="text",
                        text="Which matching vehicle would you like to explore?",
                    )
                ],
            )
        ]
    )

    resolved = GroundingValidator().resolve(draft, [envelope])

    assert [item.action["type"] for item in resolved.suggestions] == [
        "next_vehicle_page",
        "compare_displayed_vehicles",
    ]


def test_vehicle_comparison_quality_does_not_hide_the_trusted_view() -> None:
    envelope = FactNormalizer().normalize(
        result_id="result-comparison",
        call_id="call-comparison",
        intent_id="intent-comparison",
        tool="compare_vehicle_models",
        result=ToolResult(
            "ignored",
            "vehicle_comparison",
            {
                "version": 1,
                "items": [
                    {
                        "id": "veh-014",
                        "make": "BMW",
                        "model": "3 Series",
                        "pricePence": 2100000,
                        "mileage": 30000,
                    },
                    {
                        "id": "veh-041",
                        "make": "MINI",
                        "model": "Cooper",
                        "pricePence": 2900000,
                        "mileage": 5000,
                    },
                ],
                "suggestions": [],
            },
            {
                "items": [
                    {
                        "id": "veh-014",
                        "make": "BMW",
                        "model": "3 Series",
                        "pricePence": 2100000,
                        "mileage": 30000,
                    },
                    {
                        "id": "veh-041",
                        "make": "MINI",
                        "model": "Cooper",
                        "pricePence": 2900000,
                        "mileage": 5000,
                    },
                ],
                "comparison": {
                    "vehicleIds": ["veh-014", "veh-041"],
                    "selectionBasis": "Newest currently available match for each named model.",
                },
            },
        ),
    )
    by_entity_and_field = {
        (fact.entityReference, fact.field.rsplit(".", 1)[-1]): fact.factId
        for fact in envelope.facts
    }
    selection_basis = next(
        fact.factId for fact in envelope.facts if fact.field == "comparison.selectionBasis"
    )
    valid = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="comparison",
                segments=[
                    TextSegment(type="text", text="Price is "),
                    FactSegment(
                        type="fact", factId=by_entity_and_field[("vehicle:veh-014", "pricePence")]
                    ),
                    TextSegment(type="text", text=" compared with "),
                    FactSegment(
                        type="fact", factId=by_entity_and_field[("vehicle:veh-041", "pricePence")]
                    ),
                    TextSegment(type="text", text=". Mileage is "),
                    FactSegment(
                        type="fact", factId=by_entity_and_field[("vehicle:veh-014", "mileage")]
                    ),
                    TextSegment(type="text", text=" compared with "),
                    FactSegment(
                        type="fact", factId=by_entity_and_field[("vehicle:veh-041", "mileage")]
                    ),
                    TextSegment(type="text", text=". Selection: "),
                    FactSegment(type="fact", factId=selection_basis),
                    TextSegment(type="text", text="."),
                ],
            ),
            GroundedMessageDraft(
                purpose="follow_up",
                segments=[
                    TextSegment(type="text", text="Which option would you like to explore next?")
                ],
            ),
        ],
        cardReferences=[envelope.availableCards[0].reference],
    )
    GroundingValidator().resolve(valid, [envelope])

    vague = valid.model_copy(
        update={
            "messages": [
                valid.messages[0],
                GroundedMessageDraft(
                    purpose="follow_up",
                    segments=[TextSegment(type="text", text="What would you like to do next?")],
                ),
            ]
        }
    )
    with pytest.raises(ValueError, match="concrete question"):
        GroundingValidator().resolve(vague, [envelope])
    inventory_listing = valid.model_copy(
        update={"messages": [valid.messages[0].model_copy(update={"purpose": "answer"})]}
    )
    with pytest.raises(ValueError, match="comparison message"):
        GroundingValidator().resolve(inventory_listing, [envelope])


def test_complete_suggestion_contract_preserves_all_vehicle_choices() -> None:
    vehicles = [
        {"id": "veh-014", "make": "BMW", "model": "3 Series", "pricePence": 2_125_000},
        {"id": "veh-042", "make": "MINI", "model": "Countryman", "pricePence": 3_225_000},
        {"id": "veh-049", "make": "BMW", "model": "1 Series", "pricePence": 1_850_000},
    ]
    suggestions = vehicle_comparison_suggestions(vehicles)
    envelope = FactNormalizer().normalize(
        result_id="result-three-way-comparison",
        call_id="call-three-way-comparison",
        intent_id="intent-three-way-comparison",
        tool="compare_vehicles",
        result=ToolResult(
            "ignored",
            "vehicle_comparison",
            {"version": 1, "items": vehicles, "suggestions": suggestions},
            {"items": vehicles, "comparison": {"vehicleIds": [item["id"] for item in vehicles]}},
        ),
    )
    obligation = envelope.responseObligation

    assert obligation is not None
    assert obligation.suggestionMode == "complete"
    assert len(obligation.suggestionReferences) == 4

    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="follow_up",
                segments=[TextSegment(type="text", text="Which vehicle would you like to book?")],
            )
        ],
        suggestionReferences=obligation.suggestionReferences[:2],
    )
    normalized = normalize_optional_suggestions(draft, [envelope], {})

    assert normalized.suggestionReferences == obligation.suggestionReferences


def test_grounding_rejects_operational_entity_ids_in_customer_prose() -> None:
    envelope = FactNormalizer().normalize(
        result_id="result-selected-vehicle",
        call_id="call-selected-vehicle",
        intent_id="intent-selected-vehicle",
        tool="list_test_drive_slots",
        result=ToolResult(
            "ignored",
            None,
            None,
            {"vehicle": {"id": "veh-049", "make": "BMW", "model": "1 Series"}},
        ),
    )
    unsafe_id_fact = AtomicFact(
        factId="fact-selected-vehicle-operational-id",
        entityReference="vehicle:veh-049",
        field="vehicleId",
        value="veh-049",
        displayValue="veh-049",
        source="dealership_platform",
        sensitivity="public",
        observedAt=datetime.now(UTC),
    )
    envelope = envelope.model_copy(update={"facts": [*envelope.facts, unsafe_id_fact]})
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="workflow_prompt",
                segments=[
                    TextSegment(
                        type="text",
                        text="I can arrange a test drive for ",
                    ),
                    FactSegment(type="fact", factId=unsafe_id_fact.factId),
                    TextSegment(type="text", text=". What date works for you?"),
                ],
            )
        ]
    )

    with pytest.raises(ValueError, match="operational entity identifier"):
        GroundingValidator().resolve(draft, [envelope])


def test_reducer_supersedes_the_previous_navigation_proposal() -> None:
    reducer = ConversationStateReducer()
    first = PendingInteractionState(
        interactionId="interaction-one",
        kind="open_vehicle_detail",
        trustedEntity=TrustedEntity(type="vehicle", reference="vehicle:veh-041"),
        originatingMessageId="message-one",
        createdAtStateVersion=1,
        status="awaiting_confirmation",
    )
    state = reducer.apply(
        ConversationState(),
        StateEvent(
            type="INTERACTION_PROPOSED",
            turnId="turn-one",
            data=first.model_dump(mode="json"),
        ),
    )
    second = PendingInteractionState(
        interactionId="interaction-two",
        kind="open_vehicle_detail",
        trustedEntity=TrustedEntity(type="vehicle", reference="vehicle:veh-005"),
        originatingMessageId="message-two",
        createdAtStateVersion=2,
        status="awaiting_confirmation",
    )
    state = reducer.apply(
        state,
        StateEvent(
            type="INTERACTION_PROPOSED",
            turnId="turn-two",
            data=second.model_dump(mode="json"),
        ),
    )
    assert state.pendingInteraction.interactionId == "interaction-two"
    assert state.stateVersion == 2


def test_agent_workflow_is_a_closed_versioned_state_document() -> None:
    state = ConversationState.model_validate(
        {
            "agentWorkflow": {
                "version": 3,
                "activeWorkflow": "workshop_booking",
                "stage": "choosing_service",
                "entities": {},
                "constraints": {"missingPublicFields": ["serviceTypeId"]},
            }
        }
    )
    assert state.agentWorkflow["stage"] == "choosing_service"

    with pytest.raises(ValidationError, match="unknown agent workflow fields"):
        ConversationState.model_validate(
            {
                "agentWorkflow": {
                    **state.agentWorkflow,
                    "browserQuestion": "Which form field is next?",
                }
            }
        )


def test_missing_holiday_entry_cannot_be_grounded_as_confirmed_closure() -> None:
    envelope = FactNormalizer().normalize(
        result_id="result-holiday",
        call_id="call-holiday",
        intent_id="intent-holiday",
        tool="list_holiday_opening_hours",
        result=ToolResult(
            "Holiday hours are not published.",
            "opening_hours",
            {"version": 1, "items": []},
            {
                "scheduleType": "holiday",
                "items": [
                    {
                        "town": "Bolton",
                        "holidayDepartment": {
                            "name": "Parts",
                            "date": "2026-09-19",
                            "status": "not_published",
                        },
                    }
                ],
            },
        ),
    )
    unsupported = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[TextSegment(type="text", text="The parts department is closed.")],
            )
        ]
    )

    with pytest.raises(ValueError, match="cannot support a closure claim"):
        GroundingValidator().resolve(unsupported, [envelope])


def test_filtered_offer_catalogue_has_a_valid_offer_card_obligation() -> None:
    envelope = FactNormalizer().normalize(
        result_id="result-offers",
        call_id="call-offers",
        intent_id="intent-offers",
        tool="list_offers",
        result=ToolResult(
            "Found two offers.",
            "offer_list",
            {
                "version": 1,
                "items": [
                    {"id": "offer-01", "make": "BMW", "title": "BMW 1 Series PCP"},
                    {"id": "offer-02", "make": "BMW", "title": "BMW 3 Series PCH"},
                ],
            },
            {
                "items": [
                    {"id": "offer-01", "make": "BMW", "title": "BMW 1 Series PCP"},
                    {"id": "offer-02", "make": "BMW", "title": "BMW 3 Series PCH"},
                ]
            },
        ),
    )

    assert envelope.responseObligation is not None
    assert envelope.responseObligation.requiredCardType == "offer"


def test_single_vehicle_result_owns_its_follow_up() -> None:
    envelope = FactNormalizer().normalize(
        result_id="result-vehicle",
        call_id="call-vehicle",
        intent_id="intent-vehicle",
        tool="search_vehicles",
        result=ToolResult(
            "Found one vehicle.",
            "vehicle_list",
            {
                "version": 1,
                "items": [{"id": "veh-001", "make": "Volvo", "model": "XC40"}],
            },
            {"items": [{"id": "veh-001", "make": "Volvo", "model": "XC40"}]},
        ),
    )

    assert envelope.responseObligation is not None
    assert envelope.responseObligation.kind == "catalogue_results"
    assert envelope.responseObligation.requiredCardType == "vehicle_preview"


@pytest.mark.parametrize(
    "text",
    [
        "That vehicle is already selected. What date would suit you?",
        "I've got your appointment slot selected. Please provide your registration and mileage.",
        "Would you like me to continue the callback request and collect your first name?",
    ],
)
def test_unbacked_workflow_progress_and_public_protected_requests_are_rejected(
    text: str,
) -> None:
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="workflow_prompt",
                segments=[TextSegment(type="text", text=text)],
            )
        ]
    )

    with pytest.raises(ValueError, match="trusted tool result|secure collector"):
        GroundingValidator().resolve(draft, [])


def test_unrelated_business_result_cannot_ground_vehicle_selection_progress() -> None:
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="workflow_prompt",
                segments=[
                    TextSegment(
                        type="text",
                        text="That sold vehicle is already selected. What date would suit you?",
                    )
                ],
            )
        ]
    )
    business_result = FactNormalizer().normalize(
        result_id="result-business",
        call_id="call-business",
        intent_id="intent-business",
        tool="get_business_information",
        result=ToolResult("Business information found.", "business_info", {}, {}),
    )

    with pytest.raises(ValueError, match="trusted tool result"):
        GroundingValidator().resolve(draft, [business_result])


def test_incomplete_finance_terms_cannot_ground_an_approximate_total() -> None:
    draft = GroundedResponseDraft(
        messages=[
            GroundedMessageDraft(
                purpose="answer",
                segments=[
                    TextSegment(
                        type="text",
                        text=(
                            "The total amount payable would be approximately the deposit plus "
                            "the vehicle price."
                        ),
                    )
                ],
            )
        ]
    )
    vehicle_result = FactNormalizer().normalize(
        result_id="result-vehicles",
        call_id="call-vehicles",
        intent_id="intent-vehicles",
        tool="search_vehicles",
        result=ToolResult("Found vehicles.", "vehicle_list", {"items": []}, {}),
    )

    with pytest.raises(ValueError, match="complete trusted terms"):
        GroundingValidator().resolve(draft, [vehicle_result])


def test_agent_workflow_upgrades_legacy_version_two_state() -> None:
    state = ConversationState.model_validate(
        {
            "agentWorkflow": {
                "version": 2,
                "domain": "workshop",
                "stage": "planned",
                "entities": {"serviceTypeId": "mot"},
                "constraints": {},
            }
        }
    )
    assert state.agentWorkflow == {
        "version": 3,
        "activeWorkflow": "workshop",
        "stage": "active",
        "entities": {"serviceTypeId": "mot"},
        "constraints": {},
    }


def test_client_navigation_url_is_derived_from_the_same_vehicle() -> None:
    OpenVehicleDetailAction(
        actionId="action-one",
        type="open_vehicle_detail",
        interactionId="interaction-one",
        vehicleId="veh-041",
        sameSiteUrl="/?vehicle=veh-041",
    )
    with pytest.raises(ValidationError, match="must match"):
        OpenVehicleDetailAction(
            actionId="action-two",
            type="open_vehicle_detail",
            interactionId="interaction-one",
            vehicleId="veh-041",
            sameSiteUrl="/?vehicle=veh-005",
        )
