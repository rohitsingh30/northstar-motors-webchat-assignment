"""Persisted contracts for resolving replies to application-owned assistant prompts."""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from webchat.domain.turn_actions import TurnAction


class ActionHandoff(BaseModel):
    """Server-owned context carried across an application-authored action chain."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    sourceWorkflow: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_]{1,79}$"
    )
    topic: Literal["finance", "privacy", "part_exchange", "general"] | None = None
    customerReason: str | None = Field(default=None, min_length=1, max_length=4_000)
    transition: Literal["handoff", "interrupt"] = "interrupt"


class PendingInteraction(BaseModel):
    """The structured meaning of an actionable assistant prompt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    kind: Literal[
        "single_action",
        "choice",
        "input",
        "protected_confirmation",
        "reference_choice",
        "intent_choice",
    ]
    prompt: str = Field(min_length=1, max_length=800)
    question_id: str | None = Field(
        default=None, pattern=r"^question-[A-Za-z0-9_-]{1,64}$"
    )
    goal_intent: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_]{1,79}$"
    )
    candidate_references: list[str] = Field(default_factory=list, max_length=12)
    candidate_intents: list[str] = Field(default_factory=list, max_length=4)
    actions: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    blocking_tool: str | None = Field(default=None, min_length=1, max_length=100)
    blocking_fields: list[str] = Field(default_factory=list, max_length=8)
    blocking_preconditions: list[str] = Field(default_factory=list, max_length=8)
    draft_id: str | None = Field(default=None, min_length=1, max_length=100)
    workflow_kind: str | None = Field(default=None, min_length=1, max_length=80)
    handoff: ActionHandoff | None = None

    @model_validator(mode="after")
    def valid_shape(self):
        for action in self.actions:
            try:
                TurnAction.model_validate(action)
            except ValueError as error:
                raise ValueError("interaction actions require a safe action type") from error
        if self.kind == "single_action" and len(self.actions) != 1:
            raise ValueError("single-action interaction requires exactly one action")
        if self.kind == "choice" and not self.actions:
            raise ValueError("choice interaction requires at least one typed action")
        if self.kind == "input":
            if not self.blocking_tool:
                raise ValueError("input interaction requires a blocking tool")
            if not self.blocking_fields and not self.blocking_preconditions:
                raise ValueError("input interaction requires a field or precondition")
        elif self.blocking_tool or self.blocking_fields or self.blocking_preconditions:
            raise ValueError("blocking metadata is reserved for input interactions")
        if self.kind in {"input", "protected_confirmation"} and self.actions:
            raise ValueError(f"{self.kind} interaction cannot execute a chat action")
        if self.kind == "protected_confirmation":
            if not self.draft_id or not self.workflow_kind:
                raise ValueError("protected confirmations require draft metadata")
        elif self.draft_id or self.workflow_kind:
            raise ValueError("draft metadata is reserved for protected confirmations")
        if self.handoff is not None and self.kind not in {"single_action", "choice"}:
            raise ValueError("action handoff context is reserved for executable actions")
        is_dialogue_choice = self.kind in {"reference_choice", "intent_choice"}
        if is_dialogue_choice:
            if not self.question_id:
                raise ValueError("dialogue choices require a question ID")
            if self.actions or self.blocking_tool or self.blocking_fields:
                raise ValueError("dialogue choices cannot contain executable actions or blockers")
            if self.kind == "reference_choice":
                if len(self.candidate_references) < 2 or self.candidate_intents:
                    raise ValueError("reference choices require candidate references only")
                if not self.goal_intent:
                    raise ValueError("reference choices require the owning goal intent")
            elif len(self.candidate_intents) < 2 or self.candidate_references:
                raise ValueError("intent choices require candidate intents only")
        elif (
            self.question_id
            or self.goal_intent
            or self.candidate_references
            or self.candidate_intents
        ):
            raise ValueError("dialogue-choice metadata is reserved for dialogue choices")
        return self

    def as_json(self) -> str:
        return self.model_dump_json(exclude_none=True)


def composition_continuation(
    interaction: PendingInteraction | None,
) -> dict[str, Any] | None:
    """Project only the public, typed next move needed by response composition."""

    if interaction is None:
        return None
    return {
        "kind": interaction.kind,
        "prompt": interaction.prompt,
        "goalIntent": interaction.goal_intent,
        "candidateReferences": interaction.candidate_references,
        "candidateIntents": interaction.candidate_intents,
        "expectedFields": interaction.blocking_fields,
    }


def single_action_interaction(
    prompt: str,
    action: dict[str, Any],
    *,
    handoff: ActionHandoff | None = None,
) -> PendingInteraction:
    return PendingInteraction(
        kind="single_action",
        prompt=prompt,
        actions=[dict(action)],
        handoff=handoff,
    )


def choice_interaction(
    prompt: str,
    suggestions: list[dict[str, Any]],
    *,
    handoff: ActionHandoff | None = None,
) -> PendingInteraction | None:
    """Describe a chooser only when every visible option has a typed action."""
    actions = [
        dict(suggestion["action"])
        for suggestion in suggestions
        if isinstance(suggestion, dict) and isinstance(suggestion.get("action"), dict)
    ]
    if not suggestions or len(actions) != len(suggestions):
        return None
    if len(actions) == 1:
        return single_action_interaction(prompt, actions[0], handoff=handoff)
    return PendingInteraction(kind="choice", prompt=prompt, actions=actions, handoff=handoff)


def interaction_from_view(
    prompt: str,
    view_type: str | None,
    view_payload: dict[str, Any] | None,
) -> PendingInteraction | None:
    """Derive prompts only from closed chooser views, never arbitrary response prose."""
    if view_type == "confirmation":
        if not view_payload or not view_payload.get("draftId") or not view_payload.get("kind"):
            return None
        return PendingInteraction(
            kind="protected_confirmation",
            prompt=prompt,
            draft_id=str(view_payload["draftId"]),
            workflow_kind=str(view_payload["kind"]),
        )
    if view_type not in {"service_list", "suggestion_list"} or not view_payload:
        return None
    suggestions = view_payload.get("suggestions")
    if not isinstance(suggestions, list):
        return None
    return choice_interaction(prompt, suggestions)


def input_interaction(
    prompt: str,
    blocking_tool: str,
    blocking_fields: list[str],
    blocking_preconditions: list[str],
) -> PendingInteraction:
    return PendingInteraction(
        kind="input",
        prompt=prompt,
        blocking_tool=blocking_tool,
        blocking_fields=blocking_fields,
        blocking_preconditions=blocking_preconditions,
    )


def reference_choice_interaction(
    prompt: str,
    candidate_references: tuple[str, ...],
    goal_intent: str,
) -> PendingInteraction:
    """Persist an AI-authored entity question as model-visible dialogue state."""

    return PendingInteraction(
        kind="reference_choice",
        prompt=prompt,
        question_id=f"question-{uuid.uuid4().hex[:16]}",
        goal_intent=goal_intent,
        candidate_references=list(candidate_references),
    )


def intent_choice_interaction(
    prompt: str,
    candidate_intents: tuple[str, ...],
) -> PendingInteraction:
    """Persist an AI-authored intent question as model-visible dialogue state."""

    return PendingInteraction(
        kind="intent_choice",
        prompt=prompt,
        question_id=f"question-{uuid.uuid4().hex[:16]}",
        candidate_intents=list(candidate_intents),
    )


def parse_interaction(value: str | None) -> PendingInteraction | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
        return PendingInteraction.model_validate(payload)
    except (TypeError, ValueError):
        return None


def preceding_interaction(messages) -> tuple[PendingInteraction, Any] | None:
    """Return metadata only from the assistant message preceding the latest user turn."""
    passed_latest_user = False
    for message in reversed(messages):
        if message.role == "user" and not passed_latest_user:
            passed_latest_user = True
            continue
        if not passed_latest_user or message.role != "assistant":
            continue
        interaction = parse_interaction(getattr(message, "interaction_json", None))
        return (interaction, message) if interaction else None
    return None
