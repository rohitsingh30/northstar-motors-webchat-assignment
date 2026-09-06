"""Versioned, strict output contract for the AI planning phase."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


IntentKind = Literal[
    "social",
    "capability",
    "vehicle_search",
    "vehicle_refinement",
    "vehicle_detail",
    "vehicle_availability",
    "vehicle_comparison",
    "offer_discovery",
    "offer_detail",
    "dealership_discovery",
    "dealership_detail",
    "opening_hours",
    "holiday_opening_hours",
    "business_information",
    "service_discovery",
    "service_detail",
    "workshop_availability",
    "test_drive",
    "workshop_booking",
    "booking_lookup",
    "booking_amendment",
    "booking_cancellation",
    "sales_enquiry",
    "vehicle_interest",
    "callback",
    "dealership_message",
    "part_exchange",
    "vehicle_navigation",
]


class PlanIntent(StrictModel):
    intentId: str = Field(pattern=r"^intent-[A-Za-z0-9_-]{1,48}$")
    kind: IntentKind
    order: int = Field(ge=1, le=4)
    goal: str = Field(min_length=1, max_length=240)
    status: Literal["ready", "needs_clarification"]


class PlannedToolCall(StrictModel):
    callId: str = Field(pattern=r"^call-[A-Za-z0-9_-]{1,48}$")
    intentId: str = Field(pattern=r"^intent-[A-Za-z0-9_-]{1,48}$")
    tool: str = Field(pattern=r"^[a-z][a-z0-9_]{1,99}$")
    arguments: dict[str, Any] = Field(default_factory=dict)


class Clarification(StrictModel):
    clarificationId: str = Field(pattern=r"^clarification-[A-Za-z0-9_-]{1,48}$")
    intentId: str = Field(pattern=r"^intent-[A-Za-z0-9_-]{1,48}$")
    reason: str = Field(pattern=r"^[a-z][a-z0-9_]{1,79}$")
    blockingTool: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,99}$")
    blockingFields: list[str] = Field(default_factory=list, max_length=8)
    blockingPreconditions: list[str] = Field(default_factory=list, max_length=8)
    question: str = Field(min_length=1, max_length=500)
    candidateReferences: list[str] = Field(default_factory=list, max_length=12)
    candidateIntents: list[IntentKind] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def has_declared_basis(self) -> Clarification:
        has_blocker = bool(
            self.blockingTool
            and (self.blockingFields or self.blockingPreconditions)
        )
        has_semantic_ambiguity = bool(
            self.reason == "semantic_ambiguity"
            and len(self.candidateIntents) >= 2
            and len(self.candidateIntents) == len(set(self.candidateIntents))
        )
        has_entity_ambiguity = bool(
            self.reason == "entity_ambiguity"
            and len(self.candidateReferences) >= 2
            and len(self.candidateReferences) == len(set(self.candidateReferences))
        )
        if sum((has_blocker, has_semantic_ambiguity, has_entity_ambiguity)) != 1:
            raise ValueError(
                "clarification requires exactly one declared blocker, semantic ambiguity, or "
                "entity ambiguity"
            )
        if not self.blockingTool and (self.blockingFields or self.blockingPreconditions):
            raise ValueError("clarification blocker details require a blocking tool")
        return self


class WorkflowStart(StrictModel):
    kind: Literal[
        "test_drive",
        "workshop_booking",
        "booking_lookup",
        "booking_amendment",
        "booking_cancellation",
        "sales_enquiry",
        "vehicle_interest",
        "callback",
        "dealership_message",
        "part_exchange",
    ]
    intentId: str = Field(pattern=r"^intent-[A-Za-z0-9_-]{1,48}$")
    trustedEntityReferences: list[str] = Field(default_factory=list, max_length=4)


class InteractionProposal(StrictModel):
    kind: Literal["open_vehicle_detail"]
    entityReference: str = Field(pattern=r"^vehicle:veh-[0-9]{3}$")


class AgentPlan(StrictModel):
    schemaVersion: Literal[1] = 1
    mode: Literal["tool_plan", "clarification", "direct_response"]
    intents: list[PlanIntent] = Field(min_length=1, max_length=4)
    toolCalls: list[PlannedToolCall] = Field(default_factory=list, max_length=4)
    clarification: Clarification | None = None
    workflowStart: WorkflowStart | None = None
    interactionProposal: InteractionProposal | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> AgentPlan:
        intent_ids = [intent.intentId for intent in self.intents]
        if len(set(intent_ids)) != len(intent_ids):
            raise ValueError("intent IDs must be unique")
        orders = [intent.order for intent in self.intents]
        if sorted(orders) != list(range(1, len(orders) + 1)):
            raise ValueError("intent order must be unique and contiguous from one")
        call_ids = [call.callId for call in self.toolCalls]
        if len(set(call_ids)) != len(call_ids):
            raise ValueError("tool call IDs must be unique")
        if any(call.intentId not in intent_ids for call in self.toolCalls):
            raise ValueError("every tool call must belong to a declared intent")
        if self.workflowStart and self.workflowStart.intentId not in intent_ids:
            raise ValueError("workflow start must belong to a declared intent")
        if self.mode == "clarification":
            if self.clarification is None or self.toolCalls or self.workflowStart:
                raise ValueError("clarification mode cannot execute tools or start a workflow")
            if self.clarification.intentId not in intent_ids:
                raise ValueError("clarification must belong to a declared intent")
        elif self.clarification is not None:
            raise ValueError("clarification is allowed only in clarification mode")
        if self.mode == "direct_response" and (
            self.toolCalls or self.workflowStart or self.interactionProposal
        ):
            raise ValueError("direct responses cannot execute application behavior")
        if self.mode == "tool_plan" and not (
            self.toolCalls or self.workflowStart or self.interactionProposal
        ):
            raise ValueError("tool plans require a tool, workflow start, or interaction proposal")
        return self
