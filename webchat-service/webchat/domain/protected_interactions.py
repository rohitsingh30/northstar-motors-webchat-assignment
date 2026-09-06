"""Deterministic confirmation boundary for writes and client navigation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .conversation_state import PendingInteractionState

Decision = Literal["confirm", "cancel"]

_AFFIRMATIVE = re.compile(
    r"^(?:yes(?: please)?|yep|yeah|ok|okay|sure|go ahead|please do|"
    r"(?:please\s+)?confirm(?:\s+(?:it|(?:this|that|the)\s+(?:request|booking)))?|"
    r"i(?:'d| would) like to confirm(?:\s+(?:it|(?:this|the)\s+(?:request|booking)))?|"
    r"i (?:want|need) to confirm(?:\s+(?:it|(?:this|the)\s+(?:request|booking)))?|"
    r"book it|send it|do it)[.! ]*$",
    re.IGNORECASE,
)
_CANCELLATION = re.compile(
    r"^(?:no|nope|(?:please\s+)?cancel(?:\s+(?:it|(?:this|that|the)\s+(?:request|booking)))?|"
    r"i(?:'d| would) like to cancel(?:\s+(?:it|(?:this|the)\s+(?:request|booking)))?|"
    r"i (?:want|need) to cancel(?:\s+(?:it|(?:this|the)\s+(?:request|booking)))?|"
    r"stop(?:\s+(?:this|that|the request|the booking))?|never mind|don['’]?t|do not|"
    r"don['’]?t open it|do not open it|stay in (?:the )?chat|keep (?:it|the booking))[.! ]*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class InteractionResolution:
    decision: Decision
    interaction_id: str


def explicit_decision(text: str, action_type: str | None = None) -> Decision | None:
    """Classify only an isolated decision; questions, corrections and conditions fail closed."""
    if action_type in {"confirm_active_interaction", "confirm_active_draft"}:
        return "confirm"
    if action_type in {"cancel_active_interaction", "cancel_active_draft"}:
        return "cancel"
    cleaned = " ".join(str(text or "").strip().split())
    if _AFFIRMATIVE.fullmatch(cleaned):
        return "confirm"
    if _CANCELLATION.fullmatch(cleaned):
        return "cancel"
    return None


def resolve_latest(
    interaction: PendingInteractionState | None,
    *,
    state_version: int,
    text: str,
    action_type: str | None = None,
) -> InteractionResolution | None:
    decision = explicit_decision(text, action_type)
    if decision is None or interaction is None:
        return None
    if interaction.status != "awaiting_confirmation":
        return None
    if interaction.createdAtStateVersion != state_version:
        return None
    return InteractionResolution(decision, interaction.interactionId)
