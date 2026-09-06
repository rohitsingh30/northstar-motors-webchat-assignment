"""Trusted normalized facts produced after deterministic tool execution."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from webchat.domain.turn_actions import TurnAction

from .presentation import CollectionPresentation


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResultEntity(StrictModel):
    reference: str = Field(pattern=r"^[a-z][a-z0-9_]*:[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    type: Literal["vehicle", "offer", "dealership", "service", "appointment", "booking"]

    @model_validator(mode="after")
    def type_matches_reference(self) -> ResultEntity:
        if not self.reference.startswith(f"{self.type}:"):
            raise ValueError("entity type must match its reference namespace")
        return self


class AtomicFact(StrictModel):
    factId: str = Field(pattern=r"^fact-[A-Za-z0-9_.:-]{1,160}$")
    entityReference: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]*:[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    field: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")
    value: Any
    displayValue: str = Field(min_length=1, max_length=2_000)
    source: Literal["dealership_platform", "approved_content", "application_derived"]
    sensitivity: Literal["public", "customer_safe", "customer_private", "internal"]
    observedAt: datetime

    @field_validator("value")
    @classmethod
    def json_value_only(cls, value: Any) -> Any:
        def valid(item: Any) -> bool:
            if item is None or isinstance(item, str | int | float | bool):
                return True
            if isinstance(item, list):
                return len(item) <= 100 and all(valid(child) for child in item)
            if isinstance(item, dict):
                return len(item) <= 100 and all(
                    isinstance(key, str) and valid(child) for key, child in item.items()
                )
            return False

        if not valid(value):
            raise ValueError("fact value must be bounded JSON data")
        return value


class AvailableCard(StrictModel):
    reference: str = Field(pattern=r"^card:[A-Za-z0-9_.:-]{1,180}$")
    type: Literal[
        "vehicle_preview",
        "vehicle_comparison",
        "offer",
        "dealership",
        "opening_hours",
        "service",
        "valuation",
        "confirmation",
        "receipt",
        "booking",
    ]
    entityReference: str | None = None
    optionNumber: int | None = Field(default=None, ge=1, le=100)
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("data")
    @classmethod
    def visual_data_only(cls, value: dict[str, Any]) -> dict[str, Any]:
        forbidden = {
            "href",
            "url",
            "action",
            "actions",
            "button",
            "buttons",
            "handler",
            "onclick",
            "quickReplies",
            "suggestions",
        }
        normalized_forbidden = {item.casefold() for item in forbidden}

        def visit(item: Any) -> None:
            if isinstance(item, dict):
                for key, child in item.items():
                    if str(key).casefold() in normalized_forbidden:
                        raise ValueError("card data must be visual-only")
                    visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)

        visit(value)
        if "collectionPresentation" in value:
            CollectionPresentation.model_validate(value["collectionPresentation"])
        return value


class AvailableSuggestion(StrictModel):
    reference: str = Field(pattern=r"^suggestion:[A-Za-z0-9_.:-]{1,180}$")
    label: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=500)
    action: dict[str, Any] | None = None

    @field_validator("action")
    @classmethod
    def strict_application_action(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return TurnAction.model_validate(value).model_dump(exclude_none=True)


class AvailableLink(StrictModel):
    reference: str = Field(pattern=r"^link:[A-Za-z0-9_.:-]{1,180}$")
    label: str = Field(min_length=1, max_length=160)
    href: str = Field(min_length=1, max_length=1_000)
    source: Literal["dealership_platform", "approved_content"]
    destinationKind: Literal["telephone", "email", "directions", "website"]


class AvailableCollection(StrictModel):
    """A trusted visual collection that the response agent must place in one message."""

    reference: str = Field(pattern=r"^collection:[A-Za-z0-9_.:-]{1,180}$")
    viewType: str = Field(pattern=r"^[a-z][a-z0-9_]{1,79}$")
    presentation: CollectionPresentation


class AlternativeChange(StrictModel):
    """One customer-visible difference between the requested and offered outcome."""

    dimension: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9 _-]{0,79}$")
    requested: str = Field(min_length=1, max_length=500)
    offered: str = Field(min_length=1, max_length=500)


class AlternativeOffer(StrictModel):
    """Trusted provenance for a system-proposed substitute.

    This is presentation authority, not conversational prose.  It ensures every capability can
    disclose what failed, what is being offered instead, and what accepting it will change.
    """

    reasonCode: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    requestedOutcome: str = Field(min_length=1, max_length=1_000)
    failureReason: str = Field(min_length=1, max_length=1_000)
    offeredOutcome: str = Field(min_length=1, max_length=1_000)
    changes: list[AlternativeChange] = Field(min_length=1, max_length=12)
    preserved: list[str] = Field(default_factory=list, max_length=12)
    candidateReferences: list[str] = Field(default_factory=list, max_length=20)
    requiresCustomerAcceptance: Literal[True] = True

    @field_validator("preserved")
    @classmethod
    def bounded_preserved_values(cls, value: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 240 for item in value):
            raise ValueError("preserved alternative values must be short customer-facing text")
        return value


class ResponseObligation(StrictModel):
    """Machine-checkable evidence that a grounded answer must provide for one result."""

    kind: Literal[
        "vehicle_comparison",
        "vehicle_resolution",
        "location_resolution",
        "guided_discovery",
        "catalogue_results",
        "service_detail",
        "informational_next_steps",
        "workflow_transition",
    ]
    subjectReferences: list[str] = Field(default_factory=list, min_length=1, max_length=4)
    requiredCardType: (
        Literal["vehicle_preview", "vehicle_comparison", "offer", "dealership"] | None
    ) = None
    minimumComparedDimensions: int = Field(default=0, ge=0, le=8)
    allowedComparisonFields: list[str] = Field(default_factory=list, max_length=12)
    requiredFactIds: list[str] = Field(default_factory=list, max_length=8)
    forbiddenSuggestionActions: list[str] = Field(default_factory=list, max_length=8)
    suggestionMode: Literal["complete"] | None = None
    suggestionReferences: list[str] = Field(default_factory=list, max_length=4)
    choiceMode: Literal["confirm_single", "choose_multiple"] | None = None
    candidateReferences: list[str] = Field(default_factory=list, max_length=12)
    workflowCode: (
        Literal[
            "request_schedule_preferences",
            "request_slot_selection",
            "offer_vehicle_alternatives",
            "request_alternative_schedule",
            "request_vehicle_selection",
            "choose_alternative_location",
            "offer_workshop_contact",
            "offer_schedule_alternatives",
            "offer_location_alternatives",
            "offer_location_and_schedule_alternatives",
            "offer_equivalent_vehicle_slots",
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def workflow_transition_has_code(self) -> ResponseObligation:
        if (self.kind == "workflow_transition") != (self.workflowCode is not None):
            raise ValueError("workflow transition obligations require exactly one workflow code")
        if self.choiceMode == "confirm_single" and len(self.candidateReferences) != 1:
            raise ValueError("single confirmation requires exactly one candidate")
        if self.choiceMode == "choose_multiple" and len(self.candidateReferences) < 2:
            raise ValueError("multiple choice requires at least two candidates")
        if (self.choiceMode is None) != (not self.candidateReferences):
            raise ValueError("finite choice mode and candidates must be declared together")
        if (self.suggestionMode is None) != (not self.suggestionReferences):
            raise ValueError("complete suggestion mode and references must be declared together")
        if self.suggestionMode == "complete" and len(self.suggestionReferences) not in {2, 4}:
            raise ValueError("a complete chip set must contain two or four suggestions")
        return self


class ToolResultEnvelope(StrictModel):
    schemaVersion: Literal[1] = 1
    resultId: str = Field(pattern=r"^result-[A-Za-z0-9_-]{1,80}$")
    callId: str = Field(pattern=r"^call-[A-Za-z0-9_-]{1,80}$")
    intentId: str = Field(pattern=r"^intent-[A-Za-z0-9_-]{1,80}$")
    tool: str = Field(pattern=r"^[a-z][a-z0-9_]{1,99}$")
    status: Literal["success", "ambiguous", "empty", "unavailable"]
    entities: list[ResultEntity] = Field(default_factory=list, max_length=100)
    facts: list[AtomicFact] = Field(default_factory=list, max_length=500)
    availableCards: list[AvailableCard] = Field(default_factory=list, max_length=100)
    availableSuggestions: list[AvailableSuggestion] = Field(default_factory=list, max_length=20)
    availableLinks: list[AvailableLink] = Field(default_factory=list, max_length=20)
    availableCollections: list[AvailableCollection] = Field(default_factory=list, max_length=4)
    alternativeOffer: AlternativeOffer | None = None
    responseObligation: ResponseObligation | None = None
    observedAt: datetime
    expiresAt: datetime | None = None

    @model_validator(mode="after")
    def unique_references(self) -> ToolResultEnvelope:
        groups = (
            [fact.factId for fact in self.facts],
            [card.reference for card in self.availableCards],
            [item.reference for item in self.availableSuggestions],
            [link.reference for link in self.availableLinks],
            [collection.reference for collection in self.availableCollections],
        )
        if any(len(values) != len(set(values)) for values in groups):
            raise ValueError("fact and presentation references must be unique within a result")
        if self.alternativeOffer is not None:
            known_candidates = {
                entity.reference for entity in self.entities
            } | {
                suggestion.reference for suggestion in self.availableSuggestions
            }
            unknown = set(self.alternativeOffer.candidateReferences) - known_candidates
            if unknown:
                raise ValueError("alternative candidates must resolve to trusted result references")
            dimensions = [change.dimension.casefold() for change in self.alternativeOffer.changes]
            if len(dimensions) != len(set(dimensions)):
                raise ValueError("alternative changes must name each dimension once")
        if self.responseObligation is not None:
            known_suggestions = {
                suggestion.reference for suggestion in self.availableSuggestions
            }
            unknown_suggestions = (
                set(self.responseObligation.suggestionReferences) - known_suggestions
            )
            if unknown_suggestions:
                raise ValueError(
                    "response obligation suggestions must resolve to trusted result references"
                )
        if self.expiresAt and self.expiresAt <= self.observedAt:
            raise ValueError("result expiry must be after observation")
        return self

    def ai_safe_facts(self) -> tuple[AtomicFact, ...]:
        return tuple(fact for fact in self.facts if fact.sensitivity == "public")

    def ai_projection(self) -> dict[str, Any]:
        """Return the full result contract with only model-visible facts.

        Entity and action references remain available for grounding, while internal and customer
        data cannot leak through a caller that serializes the envelope wholesale.
        """

        return {
            "schemaVersion": self.schemaVersion,
            "resultId": self.resultId,
            "tool": self.tool,
            "status": self.status,
            "entities": [entity.model_dump() for entity in self.entities],
            "facts": [fact.model_dump(mode="json") for fact in self.ai_safe_facts()],
            "availableCards": [card.model_dump() for card in self.availableCards],
            "availableSuggestions": [item.model_dump() for item in self.availableSuggestions],
            "availableLinks": [link.model_dump() for link in self.availableLinks],
            "availableCollections": [
                collection.model_dump() for collection in self.availableCollections
            ],
            "alternativeOffer": (
                self.alternativeOffer.model_dump()
                if self.alternativeOffer is not None
                else None
            ),
            "responseObligation": (
                self.responseObligation.model_dump()
                if self.responseObligation is not None
                else None
            ),
        }
