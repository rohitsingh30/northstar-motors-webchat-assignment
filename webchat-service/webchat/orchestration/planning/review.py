from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from webchat.domain.interactions import input_interaction
from webchat.integrations.contracts import ProposalReview, ResponseProposal, ToolCall, TurnProposal
from webchat.orchestration.catalogue import UnifiedToolCatalog

ACCEPT_REVIEW_TOOL = "accept_customer_turn_proposal"
CORRECT_REVIEW_TOOL = "correct_customer_turn_proposal"
CLARIFY_REVIEW_TOOL = "clarify_customer_turn_proposal"
REJECT_REVIEW_TOOL = "reject_customer_turn_proposal"
TURN_REVIEW_TOOLS = frozenset(
    {ACCEPT_REVIEW_TOOL, CORRECT_REVIEW_TOOL, CLARIFY_REVIEW_TOOL, REJECT_REVIEW_TOOL}
)
SAFE_REVIEW_FAILURE_RESPONSE = (
    "I couldn't safely determine the right Northstar action. Please clarify what you need."
)

ReasonCode = Literal[
    "CANDIDATE_VALID",
    "WRONG_TOOL",
    "READ_WRITE_MISMATCH",
    "MISSING_OPERATION",
    "EXTRA_OPERATION",
    "MISSING_CONSTRAINT",
    "UNSUPPORTED_CONSTRAINT",
    "ENTITY_OMITTED",
    "ENTITY_UNGROUNDED",
    "ENTITY_AMBIGUOUS",
    "CONTEXT_REUSE_UNSUPPORTED",
    "COMPOUND_REQUEST_INCOMPLETE",
    "UNNECESSARY_CLARIFICATION",
    "CLARIFICATION_REQUIRED",
    "UNSUPPORTED_REQUEST",
    "UNGROUNDED_ANSWER",
    "INVALID_CITATION",
    "CONFIRMATION_OR_OUTCOME_CLAIM",
]


class ReviewValidationError(ValueError):
    """A deterministic review failure with an explicit admissible repair path."""

    def __init__(self, code: str, message: str, repair_tools: frozenset[str]):
        super().__init__(message)
        self.code = code
        self.repair_tools = repair_tools


class ProposedToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ProposedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["answer"] = "answer"
    grounding: Literal["knowledge"] = "knowledge"
    citationIds: list[str] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def citations_are_unique(self):
        if len(self.citationIds) != len(set(self.citationIds)):
            raise ValueError("citationIds must be unique")
        return self


class CorrectedProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    toolCalls: list[ProposedToolCall] = Field(default_factory=list, max_length=4)
    response: ProposedResponse | None = None
    interactionDecision: Literal["accept", "decline"] | None = None

    @model_validator(mode="after")
    def not_empty(self):
        branches = bool(self.toolCalls) + (self.response is not None) + (
            self.interactionDecision is not None
        )
        if branches == 0:
            raise ValueError("proposal cannot be empty")
        if branches > 1:
            raise ValueError("proposal must choose one response branch")
        return self


class TurnReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[3] = 3
    decision: Literal["accept", "correct", "clarify", "reject"]
    reasonCodes: list[ReasonCode] = Field(min_length=1, max_length=8)
    correctedProposal: CorrectedProposal | None = None
    clarification: str | None = Field(default=None, min_length=1, max_length=600)
    blockingTool: str | None = Field(default=None, min_length=1, max_length=100)
    blockingFields: list[str] = Field(default_factory=list, max_length=8)
    blockingPreconditions: list[str] = Field(default_factory=list, max_length=8)
    options: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def outcome_contract(self):
        if self.decision == "accept" and self.reasonCodes != ["CANDIDATE_VALID"]:
            raise ValueError("accept requires only CANDIDATE_VALID")
        if self.decision != "accept" and "CANDIDATE_VALID" in self.reasonCodes:
            raise ValueError("CANDIDATE_VALID is only valid for accept")
        if self.decision == "correct" and self.correctedProposal is None:
            raise ValueError("correct requires correctedProposal")
        if self.decision == "clarify":
            if not self.clarification or not self.blockingTool:
                raise ValueError("clarify requires clarification and blockingTool")
            if not self.blockingFields and not self.blockingPreconditions:
                raise ValueError("clarify requires a schema field or catalogue precondition")
            _validate_clarification_options(self.options)
        elif self.blockingTool or self.blockingFields or self.blockingPreconditions or self.options:
            raise ValueError("blocking evidence is only valid for clarification")
        return self


TURN_REVIEW_ADAPTER = TypeAdapter(TurnReviewPayload)


class EmptyReviewArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CorrectReviewArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reasonCodes: list[ReasonCode] = Field(min_length=1, max_length=8)
    correctedProposal: CorrectedProposal


class ClarifyReviewArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reasonCodes: list[ReasonCode] = Field(min_length=1, max_length=8)
    clarification: str = Field(min_length=1, max_length=600)
    blockingTool: str = Field(min_length=1, max_length=100)
    blockingFields: list[str] = Field(default_factory=list, max_length=8)
    blockingPreconditions: list[str] = Field(default_factory=list, max_length=8)
    options: list[str] = Field(
        default_factory=list,
        max_length=4,
        description=(
            "Two to four concise customer-visible choices when the clarification has a finite "
            "set of options in trusted context. Omit for free-form input."
        ),
    )

    @model_validator(mode="after")
    def has_real_blocker(self):
        if not self.blockingFields and not self.blockingPreconditions:
            raise ValueError("clarification requires a schema field or catalogue precondition")
        _validate_clarification_options(self.options)
        return self


class RejectReviewArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reasonCodes: list[ReasonCode] = Field(min_length=1, max_length=8)


def _review_definition(name: str, description: str, model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.pop("title", None)
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": schema,
        "strict": False,
    }


def turn_review_definitions() -> list[dict[str, Any]]:
    """Expose one small function per outcome instead of one error-prone union payload."""
    return [
        _review_definition(
            ACCEPT_REVIEW_TOOL,
            "Accept the candidate exactly as proposed because it fully satisfies the request.",
            EmptyReviewArguments,
        ),
        _review_definition(
            CORRECT_REVIEW_TOOL,
            "Replace an incorrect candidate with one complete safe proposal.",
            CorrectReviewArguments,
        ),
        _review_definition(
            CLARIFY_REVIEW_TOOL,
            (
                "Ask for genuinely required customer input and identify its exact schema blocker. "
                "Include options when trusted context contains a small finite choice set."
            ),
            ClarifyReviewArguments,
        ),
        _review_definition(
            REJECT_REVIEW_TOOL,
            "Reject unsupported or unsafe work when no correction or valid clarification exists.",
            RejectReviewArguments,
        ),
    ]


def review_payload(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate the selected branch before constructing the internal review contract."""
    if name == ACCEPT_REVIEW_TOOL:
        EmptyReviewArguments.model_validate(arguments)
        return {
            "version": 3,
            "decision": "accept",
            "reasonCodes": ["CANDIDATE_VALID"],
        }
    models: dict[str, tuple[str, type[BaseModel]]] = {
        CORRECT_REVIEW_TOOL: ("correct", CorrectReviewArguments),
        CLARIFY_REVIEW_TOOL: ("clarify", ClarifyReviewArguments),
        REJECT_REVIEW_TOOL: ("reject", RejectReviewArguments),
    }
    if name not in models:
        raise ValueError("reviewer selected an unknown outcome")
    decision, model = models[name]
    validated = model.model_validate(arguments)
    return {"version": 3, "decision": decision, **validated.model_dump()}


def apply_review(
    arguments: dict[str, Any],
    candidate: TurnProposal,
    catalogue: UnifiedToolCatalog,
    customer_evidence: set[str] | Mapping[str, str],
    has_trusted_tool_facts: bool = False,
    pending_interaction: dict[str, Any] | None = None,
) -> tuple[TurnProposal, ProposalReview]:
    allowed_citations = set(customer_evidence)
    payload = TURN_REVIEW_ADAPTER.validate_python(arguments)
    review = ProposalReview(payload.decision, tuple(payload.reasonCodes))
    if payload.decision == "accept":
        _validate_proposal(
            candidate,
            catalogue,
            allowed_citations,
            has_trusted_tool_facts,
            pending_interaction,
        )
        return candidate, review
    if payload.decision == "correct":
        assert payload.correctedProposal is not None
        corrected = _proposal_from_payload(payload.correctedProposal, customer_evidence)
        _validate_proposal(
            corrected,
            catalogue,
            allowed_citations,
            has_trusted_tool_facts,
            pending_interaction,
        )
        return corrected, review
    if payload.decision == "clarify":
        _validate_clarification(payload, catalogue)
        return TurnProposal(
            response=ResponseProposal(
                "clarify",
                str(payload.clarification),
                interaction=input_interaction(
                    str(payload.clarification),
                    str(payload.blockingTool),
                    list(payload.blockingFields),
                    list(payload.blockingPreconditions),
                ),
                suggestions=tuple(payload.options),
            )
        ), review
    return TurnProposal(response=ResponseProposal("clarify", SAFE_REVIEW_FAILURE_RESPONSE)), review


def proposal_view(proposal: TurnProposal) -> dict[str, Any]:
    value: dict[str, Any] = {
        "toolCalls": [
            {"name": call.name, "arguments": call.arguments} for call in proposal.tool_calls
        ]
    }
    if proposal.interaction_decision:
        value["interactionDecision"] = proposal.interaction_decision
    if proposal.response:
        value["response"] = {
            "mode": proposal.response.mode,
            "text": proposal.response.text,
            "grounding": proposal.response.grounding,
            "citationIds": list(proposal.response.citation_ids),
            "suggestions": list(proposal.response.suggestions),
            **(
                {"interaction": proposal.response.interaction.model_dump(exclude_none=True)}
                if proposal.response.interaction
                else {}
            ),
        }
    return value


def _proposal_from_payload(
    payload: CorrectedProposal,
    customer_evidence: set[str] | Mapping[str, str],
) -> TurnProposal:
    calls = [
        ToolCall(f"reviewed-{index}", call.name, call.arguments)
        for index, call in enumerate(payload.toolCalls, start=1)
    ]
    response = None
    if payload.response:
        if not isinstance(customer_evidence, Mapping):
            raise ValueError("reviewer cannot synthesize an answer without evidence text")
        citations = tuple(payload.response.citationIds)
        if not set(citations).issubset(customer_evidence):
            raise ValueError("response contains an invalid citation")
        response = ResponseProposal(
            "answer",
            "\n\n".join(customer_evidence[item] for item in citations),
            citations,
            "knowledge",
        )
    return TurnProposal(calls, response, payload.interactionDecision)


def _validate_proposal(
    proposal: TurnProposal,
    catalogue: UnifiedToolCatalog,
    allowed_citations: set[str],
    has_trusted_tool_facts: bool,
    pending_interaction: dict[str, Any] | None,
) -> None:
    branches = bool(proposal.tool_calls) + (proposal.response is not None) + (
        proposal.interaction_decision is not None
    )
    if branches == 0:
        raise ValueError("proposal cannot be empty")
    if branches > 1:
        raise ValueError("proposal must choose one response branch")
    if len(proposal.tool_calls) > 4:
        raise ValueError("proposal contains too many tool calls")
    if proposal.interaction_decision:
        if not pending_interaction:
            raise ValueError("interaction decision requires a pending interaction")
        if pending_interaction.get("kind") == "input":
            raise ValueError("input interactions require a concrete customer answer")
        return
    definitions = [catalogue.get(call.name) for call in proposal.tool_calls]
    if sum(definition.result_mode != "evidence" for definition in definitions) > 1:
        raise ValueError("proposal contains more than one terminal tool")
    for call, definition in zip(proposal.tool_calls, definitions, strict=True):
        if definition.risk == "confirmed_write":
            raise ValueError("confirmed write is not planner callable")
        definition.validate_arguments(call.arguments)
    if not proposal.response:
        return
    response = proposal.response
    citations = set(response.citation_ids)
    if not citations.issubset(allowed_citations):
        raise ValueError("response contains an invalid citation")
    if response.mode != "answer":
        if response.suggestions and response.mode != "clarify":
            raise ValueError("only clarification responses can contain suggestions")
        _validate_clarification_options(list(response.suggestions))
        if response.grounding != "none" or citations:
            raise ValueError(
                "conversation and clarification responses cannot claim factual evidence"
            )
        return
    if response.grounding == "knowledge":
        if not citations:
            raise ValueError("knowledge answer requires at least one retrieved customer citation")
        return
    if response.grounding == "tool_facts":
        if not has_trusted_tool_facts:
            raise ValueError("tool-fact answer requires facts from a tool executed in this turn")
        if citations:
            raise ValueError("tool-fact answer cannot claim knowledge citations")
        return
    raise ValueError("factual answer requires retrieved knowledge or current trusted tool facts")


def _validate_clarification(payload: TurnReviewPayload, catalogue: UnifiedToolCatalog) -> None:
    assert payload.blockingTool is not None
    definition = catalogue.get(payload.blockingTool)
    required_fields = set(definition.input_schema.get("required") or [])
    if not set(payload.blockingFields).issubset(required_fields):
        raise ReviewValidationError(
            "OPTIONAL_FIELD_IS_NOT_A_BLOCKER",
            "clarification cites fields that are not required by the blocking tool",
            frozenset({CORRECT_REVIEW_TOOL}),
        )
    if not set(payload.blockingPreconditions).issubset(definition.preconditions):
        raise ReviewValidationError(
            "UNDECLARED_PRECONDITION_IS_NOT_A_BLOCKER",
            "clarification cites undeclared tool preconditions",
            frozenset({CORRECT_REVIEW_TOOL}),
        )


def _validate_clarification_options(options: list[str]) -> None:
    if not options:
        return
    normalized = [option.strip() for option in options]
    if len(normalized) < 2:
        raise ValueError("clarification options require at least two choices")
    if any(not option or len(option) > 120 for option in normalized):
        raise ValueError("clarification options must be concise non-empty choices")
    if len({option.casefold() for option in normalized}) != len(normalized):
        raise ValueError("clarification options must be unique")
