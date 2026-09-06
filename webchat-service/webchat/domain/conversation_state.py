"""Authoritative, versioned conversation state and deterministic reducer.

Transcript prose is deliberately absent from this model. Only validated application events may
mutate it; an AI plan is input to policy, never a state patch.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from webchat.orchestration.contracts.semantics import TurnUnderstanding

_AGENT_WORKFLOW_KEYS = frozenset(
    {
        "version",
        "activeWorkflow",
        "stage",
        "entities",
        "constraints",
        "lastTool",
        "lastRenderer",
        "pausedWorkflow",
        "lastInterruptionTool",
    }
)
_AGENT_WORKFLOW_NAME = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_LEGACY_WORKFLOW_BY_DOMAIN = {
    "vehicle": "vehicle_search",
    "workshop": "workshop",
    "test_drive": "test_drive",
    "part_exchange": "part_exchange",
    "sales": "sales_contact",
    "dealership": "dealership_information",
    "offer": "offers",
}


def validate_agent_workflow(value: Any, *, depth: int = 0) -> dict[str, Any]:
    """Validate the closed public capability-state document and upgrade persisted V2 values."""
    if value is None or value == {}:
        return {}
    if not isinstance(value, dict):
        raise TypeError("agent workflow must be an object")
    if value.get("version") != 3:
        if value.get("version") not in {None, 2}:
            raise ValueError("unsupported agent workflow version")
        legacy_domain = str(value.get("domain") or "conversation")
        legacy_goal = str(value.get("goal") or "")
        legacy_stage = str(value.get("stage") or "active")
        value = {
            "version": 3,
            "activeWorkflow": _LEGACY_WORKFLOW_BY_DOMAIN.get(
                legacy_domain, legacy_goal or "conversation"
            ),
            "stage": (
                "viewing"
                if legacy_stage == "answered" or legacy_stage.startswith("viewing_")
                else "active"
                if legacy_stage == "planned"
                else legacy_stage
            ),
            "entities": dict(value.get("entities") or {}),
            "constraints": dict(value.get("constraints") or {}),
        }
    unknown = set(value) - _AGENT_WORKFLOW_KEYS
    if unknown:
        raise ValueError(f"unknown agent workflow fields: {', '.join(sorted(unknown))}")
    for required in ("activeWorkflow", "stage", "entities", "constraints"):
        if required not in value:
            raise ValueError(f"agent workflow requires {required}")
    workflow = str(value["activeWorkflow"])
    stage = str(value["stage"])
    if not _AGENT_WORKFLOW_NAME.fullmatch(workflow):
        raise ValueError("invalid active workflow")
    if not _AGENT_WORKFLOW_NAME.fullmatch(stage):
        raise ValueError("invalid workflow stage")
    if not isinstance(value["entities"], dict) or not isinstance(value["constraints"], dict):
        raise TypeError("agent workflow entities and constraints must be objects")
    normalized = {
        "version": 3,
        "activeWorkflow": workflow,
        "stage": stage,
        "entities": deepcopy(value["entities"]),
        "constraints": deepcopy(value["constraints"]),
    }
    for key in ("lastTool", "lastRenderer", "lastInterruptionTool"):
        if value.get(key) is None:
            continue
        item = str(value[key])
        if not _AGENT_WORKFLOW_NAME.fullmatch(item):
            raise ValueError(f"invalid {key}")
        normalized[key] = item
    paused = value.get("pausedWorkflow")
    if paused is not None:
        if depth >= 3:
            raise ValueError("agent workflow pause depth is too large")
        normalized["pausedWorkflow"] = validate_agent_workflow(paused, depth=depth + 1)
    return normalized


def complete_agent_workflow(value: Any) -> dict[str, Any]:
    """Complete the active capability and resume the most recently paused one."""

    workflow = validate_agent_workflow(value)
    paused = workflow.get("pausedWorkflow")
    return validate_agent_workflow(paused) if isinstance(paused, dict) else {}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgendaItem(StrictModel):
    intentId: str
    kind: str
    order: int = Field(ge=1, le=4)
    status: str
    resultIds: list[str] = Field(default_factory=list, max_length=8)


class LatestResultSets(StrictModel):
    vehicles: str | None = None
    offers: str | None = None
    dealerships: str | None = None
    services: str | None = None
    appointments: str | None = None


class TrustedEntity(StrictModel):
    type: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    reference: str = Field(pattern=r"^[a-z][a-z0-9_]*:[A-Za-z0-9][A-Za-z0-9_.:-]*$")

    @model_validator(mode="after")
    def matching_namespace(self) -> TrustedEntity:
        if not self.reference.startswith(f"{self.type}:"):
            raise ValueError("trusted entity type and reference namespace must match")
        return self


class PendingInteractionState(StrictModel):
    interactionId: str
    kind: str
    trustedEntity: TrustedEntity | None = None
    originatingMessageId: str
    createdAtStateVersion: int = Field(ge=0)
    status: str
    activeDraftId: str | None = None
    workflowKind: str | None = None
    supersededByInteractionId: str | None = None
    supersededAtTurnId: str | None = None

    @model_validator(mode="after")
    def valid_interaction(self) -> PendingInteractionState:
        valid_statuses = {
            "awaiting_confirmation",
            "confirmed",
            "cancelled",
            "superseded",
            "executed",
            "unavailable",
            "failed",
        }
        if self.status not in valid_statuses:
            raise ValueError("unknown protected interaction status")
        is_navigation = self.kind == "open_vehicle_detail"
        if is_navigation:
            if not self.trustedEntity or self.trustedEntity.type != "vehicle":
                raise ValueError("vehicle navigation requires a trusted vehicle")
            if self.activeDraftId or self.workflowKind:
                raise ValueError("navigation interactions cannot contain workflow draft metadata")
        elif not self.activeDraftId or not self.workflowKind:
            raise ValueError("transaction interactions require draft and workflow metadata")
        if self.status == "superseded" and not self.supersededAtTurnId:
            raise ValueError("superseded interactions require the superseding turn")
        return self


class DialogueQuestionState(StrictModel):
    """One open assistant question and the semantic scope of its answer."""

    questionId: str = Field(pattern=r"^question-[A-Za-z0-9_-]{1,64}$")
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{1,79}$")
    prompt: str = Field(min_length=1, max_length=800)
    goalIntent: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,79}$")
    candidateReferences: list[str] = Field(default_factory=list, max_length=12)
    candidateIntents: list[str] = Field(default_factory=list, max_length=4)
    expectedFields: list[str] = Field(default_factory=list, max_length=8)
    originatingMessageId: str
    createdAtStateVersion: int = Field(ge=0)

    @model_validator(mode="after")
    def valid_question(self) -> DialogueQuestionState:
        if self.kind == "reference_choice":
            if not self.candidateReferences or not self.goalIntent:
                raise ValueError("reference question requires candidates and an owning goal")
        elif self.kind == "intent_choice":
            if len(self.candidateIntents) < 2:
                raise ValueError("intent question requires candidate intents")
        elif self.kind == "input":
            if not self.expectedFields or not self.goalIntent:
                raise ValueError("input question requires expected fields and an owning goal")
        else:
            raise ValueError("unsupported dialogue question kind")
        return self


class DialogueResolutionState(StrictModel):
    turnId: str
    understanding: TurnUnderstanding


class DialogueState(StrictModel):
    activeQuestion: DialogueQuestionState | None = None
    lastResolution: DialogueResolutionState | None = None


class DialogueTurnResolvedData(StrictModel):
    """Application-compiled question effect plus the model's validated interpretation."""

    understanding: TurnUnderstanding
    questionDisposition: Literal["preserve", "consume", "close", "retain", "refresh"]


class ConversationState(StrictModel):
    schemaVersion: int = 1
    stateVersion: int = Field(default=0, ge=0)
    activeTopic: str | None = None
    agenda: list[AgendaItem] = Field(default_factory=list, max_length=4)
    latestResultSets: LatestResultSets = Field(default_factory=LatestResultSets)
    pendingInteraction: PendingInteractionState | None = None
    dialogue: DialogueState = Field(default_factory=DialogueState)
    agentWorkflow: dict = Field(default_factory=dict)

    @field_validator("agentWorkflow")
    @classmethod
    def closed_agent_workflow(cls, value: Any) -> dict[str, Any]:
        return validate_agent_workflow(value)

    @model_validator(mode="after")
    def supported_version(self) -> ConversationState:
        if self.schemaVersion != 1:
            raise ValueError("unsupported conversation state schema")
        return self


class StateEvent(StrictModel):
    """Validated application event. Payload shape is checked per event by the reducer."""

    type: str
    turnId: str
    data: dict = Field(default_factory=dict)


class ConversationStateReducer:
    """Pure reducer for state changes that have already passed policy/tool validation."""

    def apply(self, current: ConversationState, event: StateEvent) -> ConversationState:
        state = current.model_copy(deep=True)
        handler = getattr(self, f"_on_{event.type.casefold()}", None)
        if handler is None:
            raise ValueError(f"unsupported state event: {event.type}")
        handler(state, event)
        state.stateVersion += 1
        return ConversationState.model_validate(state.model_dump())

    @staticmethod
    def _on_plan_validated(state: ConversationState, event: StateEvent) -> None:
        agenda = event.data.get("agenda")
        if not isinstance(agenda, list):
            raise TypeError("PLAN_VALIDATED requires agenda")
        state.agenda = [AgendaItem.model_validate(item) for item in deepcopy(agenda)]
        if topic := event.data.get("activeTopic"):
            state.activeTopic = str(topic)

    @staticmethod
    def _on_tool_result_received(state: ConversationState, event: StateEvent) -> None:
        intent_id = str(event.data.get("intentId") or "")
        result_id = str(event.data.get("resultId") or "")
        result_kind = str(event.data.get("resultKind") or "")
        if not intent_id or not result_id:
            raise ValueError("TOOL_RESULT_RECEIVED requires intentId and resultId")
        matched = False
        for item in state.agenda:
            if item.intentId == intent_id:
                item.status = "completed"
                if result_id not in item.resultIds:
                    item.resultIds.append(result_id)
                matched = True
        if not matched:
            raise ValueError("tool result does not belong to the active agenda")
        field_by_kind = {
            "vehicles": "vehicles",
            "offers": "offers",
            "dealerships": "dealerships",
            "services": "services",
            "appointments": "appointments",
        }
        if result_kind:
            field = field_by_kind.get(result_kind)
            if not field:
                raise ValueError("unknown result kind")
            setattr(state.latestResultSets, field, result_id)

    @staticmethod
    def _on_agent_workflow_replaced(state: ConversationState, event: StateEvent) -> None:
        workflow = event.data.get("workflow")
        if not isinstance(workflow, dict):
            raise TypeError("AGENT_WORKFLOW_REPLACED requires workflow")
        state.agentWorkflow = deepcopy(workflow)

    @staticmethod
    def _on_dialogue_question_opened(state: ConversationState, event: StateEvent) -> None:
        state.dialogue.activeQuestion = DialogueQuestionState.model_validate(event.data)

    @staticmethod
    def _on_dialogue_turn_resolved(state: ConversationState, event: StateEvent) -> None:
        resolution = DialogueTurnResolvedData.model_validate(event.data)
        understanding = resolution.understanding
        disposition = resolution.questionDisposition
        active = state.dialogue.activeQuestion
        consumes_question = disposition == "consume"
        closes_question = disposition == "close"
        if consumes_question:
            if understanding.dialogueAct != "answer_open_question":
                raise ValueError("only an open-question answer can consume the question")
            if active is None or understanding.answeredQuestionId != active.questionId:
                raise ValueError("semantic turn does not answer the active dialogue question")
            if active.candidateReferences and not set(
                understanding.resolvedReferences
            ).issubset(active.candidateReferences):
                raise ValueError("semantic turn selected outside the active dialogue question")
            state.dialogue.activeQuestion = None
        elif closes_question:
            state.dialogue.activeQuestion = None
        state.dialogue.lastResolution = DialogueResolutionState(
            turnId=event.turnId,
            understanding=understanding,
        )

    @staticmethod
    def _on_interaction_proposed(state: ConversationState, event: StateEvent) -> None:
        proposed = PendingInteractionState.model_validate(event.data)
        if proposed.status != "awaiting_confirmation":
            raise ValueError("new interactions must await confirmation")
        if state.pendingInteraction and state.pendingInteraction.status == "awaiting_confirmation":
            state.pendingInteraction.status = "superseded"
            state.pendingInteraction.supersededByInteractionId = proposed.interactionId
            state.pendingInteraction.supersededAtTurnId = event.turnId
        state.pendingInteraction = proposed

    @staticmethod
    def _on_interaction_superseded(state: ConversationState, event: StateEvent) -> None:
        interaction = state.pendingInteraction
        if not interaction or interaction.interactionId != event.data.get("interactionId"):
            raise ValueError("interaction is no longer active")
        interaction.status = "superseded"
        interaction.supersededAtTurnId = event.turnId
        interaction.supersededByInteractionId = event.data.get("supersededByInteractionId")
        workflow = state.agentWorkflow
        if (
            interaction.kind != "open_vehicle_detail"
            and workflow.get("activeWorkflow") == interaction.workflowKind
        ):
            paused = deepcopy(workflow)
            paused["stage"] = "paused"
            paused_constraints = dict(paused.get("constraints") or {})
            paused_constraints.pop("draftId", None)
            paused["constraints"] = paused_constraints
            state.agentWorkflow = validate_agent_workflow(paused)

    @staticmethod
    def _on_interaction_confirmed(state: ConversationState, event: StateEvent) -> None:
        ConversationStateReducer._transition_interaction(state, event, "confirmed")

    @staticmethod
    def _on_interaction_cancelled(state: ConversationState, event: StateEvent) -> None:
        ConversationStateReducer._transition_interaction(state, event, "cancelled")

    @staticmethod
    def _on_interaction_unavailable(state: ConversationState, event: StateEvent) -> None:
        ConversationStateReducer._transition_interaction(state, event, "unavailable")

    @staticmethod
    def _on_interaction_executed(state: ConversationState, event: StateEvent) -> None:
        ConversationStateReducer._transition_interaction(state, event, "executed", {"confirmed"})

    @staticmethod
    def _on_client_action_issued(state: ConversationState, event: StateEvent) -> None:
        """Complete a confirmed client-side action such as protected navigation."""
        ConversationStateReducer._transition_interaction(state, event, "executed", {"confirmed"})

    @staticmethod
    def _on_client_action_failed(state: ConversationState, event: StateEvent) -> None:
        ConversationStateReducer._transition_interaction(
            state, event, "failed", {"confirmed", "executed"}
        )

    @staticmethod
    def _transition_interaction(
        state: ConversationState,
        event: StateEvent,
        target: str,
        allowed: set[str] | None = None,
    ) -> None:
        interaction = state.pendingInteraction
        if not interaction or interaction.interactionId != event.data.get("interactionId"):
            raise ValueError("interaction is no longer active")
        if interaction.status not in (allowed or {"awaiting_confirmation"}):
            raise ValueError("interaction state transition is stale")
        interaction.status = target
        if (
            interaction.kind != "open_vehicle_detail"
            and target in {"cancelled", "executed"}
        ):
            state.agentWorkflow = complete_agent_workflow(state.agentWorkflow)
