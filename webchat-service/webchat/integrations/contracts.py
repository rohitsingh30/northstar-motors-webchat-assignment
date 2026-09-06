from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from webchat.domain.interactions import PendingInteraction
from webchat.orchestration.contracts.plan import InteractionProposal
from webchat.orchestration.contracts.response import GroundedResponseDraft
from webchat.orchestration.contracts.semantics import TurnUnderstanding


class PlanningValidationError(RuntimeError):
    """Hosted planning remained structurally invalid after bounded repair."""


class ProviderUnavailableError(RuntimeError):
    """The hosted model transport was unavailable after bounded retries."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    intent_id: str | None = None
    intent_kind: str | None = None


@dataclass(frozen=True)
class ResponseProposal:
    """A non-tool response proposed by a provider."""

    mode: Literal["answer", "conversation", "clarify"]
    text: str
    citation_ids: tuple[str, ...] = ()
    grounding: Literal["none", "knowledge", "tool_facts"] = "none"
    interaction: PendingInteraction | None = None
    suggestions: tuple[str, ...] = ()
    blocking_tool: str | None = None
    blocking_fields: tuple[str, ...] = ()
    blocking_preconditions: tuple[str, ...] = ()
    clarification_reason: Literal[
        "declared_tool_blocker", "semantic_ambiguity", "entity_ambiguity"
    ] | None = None
    candidate_intents: tuple[str, ...] = ()
    candidate_references: tuple[str, ...] = ()
    continuation_intent: str | None = None


@dataclass(frozen=True)
class TurnProposal:
    """Concrete executable calls and/or a grounded customer response."""

    tool_calls: list[ToolCall] = field(default_factory=list)
    response: ResponseProposal | None = None
    interaction_decision: Literal["accept", "decline"] | None = None
    interaction_proposal: InteractionProposal | None = None


@dataclass(frozen=True)
class PlanningContext:
    """Deterministic context used by semantic resolution, planning, and composition."""

    latest_customer_message: str
    previous_turn: list[dict[str, str]] = field(default_factory=list)
    recent_customer_messages: list[str] = field(default_factory=list)
    workflow_state: dict[str, Any] = field(default_factory=dict)
    pending_operation: dict[str, Any] | None = None
    pending_interaction: dict[str, Any] | None = None
    calendar: dict[str, str] = field(default_factory=dict)
    page: dict[str, Any] = field(default_factory=dict)
    displayed_vehicles: list[dict[str, Any]] = field(default_factory=list)
    displayed_offers: list[dict[str, Any]] = field(default_factory=list)
    displayed_dealerships: list[dict[str, Any]] = field(default_factory=list)
    displayed_choices: list[dict[str, Any]] = field(default_factory=list)
    page_vehicles: list[dict[str, Any]] = field(default_factory=list)
    vehicle_search_state: dict[str, Any] | None = None
    trusted_tool_facts: dict[str, Any] | None = None
    context_evidence: list[dict[str, Any]] = field(default_factory=list)
    turn_understanding: TurnUnderstanding | None = None


class SemanticMessages(list[dict[str, Any]]):
    """Planner history carrying a separate structured planning snapshot."""

    def __init__(
        self,
        messages: list[dict[str, Any]],
        planning_context: PlanningContext,
    ) -> None:
        super().__init__(messages)
        self.planning_context = planning_context


@dataclass(frozen=True)
class ProviderReply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    response_mode: Literal["answer", "conversation", "clarify"] | None = None
    citation_ids: tuple[str, ...] = ()
    interaction: PendingInteraction | None = None
    interaction_decision: Literal["accept", "decline"] | None = None
    suggestions: tuple[str, ...] = ()
    response_draft: GroundedResponseDraft | None = None
    interaction_proposal: InteractionProposal | None = None
    approved_content: tuple[dict[str, str], ...] = ()
    turn_understanding: TurnUnderstanding | None = None


class LlmProvider(Protocol):
    async def generate_turn(self, messages: SemanticMessages) -> ProviderReply: ...
