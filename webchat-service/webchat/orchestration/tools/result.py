"""Shared result returned by every business-tool handler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from webchat.domain.interactions import PendingInteraction


@dataclass(frozen=True)
class ToolResult:
    text: str
    view_type: str | None
    view_payload: dict[str, Any] | None
    facts: dict[str, Any]
    interaction: PendingInteraction | None = None
    alternative_offer: dict[str, Any] | None = None


def alternative_offer(
    *,
    reason_code: str,
    requested_outcome: str,
    failure_reason: str,
    offered_outcome: str,
    changes: list[dict[str, str]],
    preserved: list[str] | None = None,
    candidate_references: list[str] | None = None,
) -> dict[str, Any]:
    """Build one shared alternative disclosure at the business-result boundary."""

    return {
        "reasonCode": reason_code,
        "requestedOutcome": requested_outcome,
        "failureReason": failure_reason,
        "offeredOutcome": offered_outcome,
        "changes": changes,
        "preserved": preserved or [],
        "candidateReferences": candidate_references or [],
        "requiresCustomerAcceptance": True,
    }
