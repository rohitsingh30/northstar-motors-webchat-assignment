from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from webchat.domain.interactions import PendingInteraction


class ReviewUnavailableError(RuntimeError):
    """The independent reviewer could not produce a valid safety decision."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ResponseProposal:
    """A non-tool response proposed by a provider."""

    mode: Literal["answer", "conversation", "clarify"]
    text: str
    citation_ids: tuple[str, ...] = ()
    grounding: Literal["none", "knowledge", "tool_facts"] = "none"
    interaction: PendingInteraction | None = None
    suggestions: tuple[str, ...] = ()


@dataclass(frozen=True)
class TurnProposal:
    """Concrete executable calls and/or a grounded customer response."""

    tool_calls: list[ToolCall] = field(default_factory=list)
    response: ResponseProposal | None = None
    interaction_decision: Literal["accept", "decline"] | None = None


@dataclass(frozen=True)
class ProposalReview:
    """Proof that an independent hosted review accepted the executable proposal."""

    outcome: Literal["accept", "correct", "clarify", "reject"]
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class ReviewerContext:
    """Deterministic, bounded context supplied to the independent reviewer."""

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
    page_vehicles: list[dict[str, Any]] = field(default_factory=list)
    vehicle_search_state: dict[str, Any] | None = None
    trusted_tool_facts: dict[str, Any] | None = None


class SemanticMessages(list[dict[str, Any]]):
    """Planner history carrying a separate structured reviewer snapshot."""

    def __init__(
        self,
        messages: list[dict[str, Any]],
        reviewer_context: ReviewerContext,
    ) -> None:
        super().__init__(messages)
        self.reviewer_context = reviewer_context


@dataclass(frozen=True)
class ProviderReply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    review: ProposalReview | None = None
    response_mode: Literal["answer", "conversation", "clarify"] | None = None
    citation_ids: tuple[str, ...] = ()
    interaction: PendingInteraction | None = None
    interaction_decision: Literal["accept", "decline"] | None = None
    suggestions: tuple[str, ...] = ()


class LlmProvider(Protocol):
    requires_review: bool

    async def generate_turn(self, messages: SemanticMessages) -> ProviderReply: ...
