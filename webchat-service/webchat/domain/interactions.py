"""Persisted contracts for resolving replies to application-owned assistant prompts."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PendingInteraction(BaseModel):
    """The structured meaning of an actionable assistant prompt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    kind: Literal["single_action", "choice", "input", "protected_confirmation"]
    prompt: str = Field(min_length=1, max_length=800)
    actions: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    blocking_tool: str | None = Field(default=None, min_length=1, max_length=100)
    blocking_fields: list[str] = Field(default_factory=list, max_length=8)
    blocking_preconditions: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def valid_shape(self):
        for action in self.actions:
            action_type = action.get("type")
            if not isinstance(action_type, str) or not re.fullmatch(
                r"[a-z][a-z0-9_]{0,79}", action_type
            ):
                raise ValueError("interaction actions require a safe action type")
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
        return self

    def as_json(self) -> str:
        return self.model_dump_json(exclude_none=True)


def single_action_interaction(prompt: str, action: dict[str, Any]) -> PendingInteraction:
    return PendingInteraction(kind="single_action", prompt=prompt, actions=[dict(action)])


def choice_interaction(
    prompt: str, suggestions: list[dict[str, Any]]
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
        return single_action_interaction(prompt, actions[0])
    return PendingInteraction(kind="choice", prompt=prompt, actions=actions)


def interaction_from_view(
    prompt: str,
    view_type: str | None,
    view_payload: dict[str, Any] | None,
) -> PendingInteraction | None:
    """Derive prompts only from closed chooser views, never arbitrary response prose."""
    if view_type == "confirmation":
        return PendingInteraction(kind="protected_confirmation", prompt=prompt)
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
