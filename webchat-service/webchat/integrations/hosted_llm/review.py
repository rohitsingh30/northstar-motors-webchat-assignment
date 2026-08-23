"""Reviewer context projection and deterministic repair feedback."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from webchat.orchestration.planning.review import TURN_REVIEW_TOOLS, ReviewValidationError
from webchat.orchestration.references import page_vehicle_identities

from ..contracts import ReviewerContext, SemanticMessages


def _reviewer_context(messages: SemanticMessages) -> ReviewerContext:
    context = getattr(messages, "reviewer_context", None)
    if isinstance(context, ReviewerContext):
        return context
    latest = next(
        (
            str(message.get("content") or "")
            for message in reversed(messages)
            if message.get("role") == "user"
        ),
        "",
    )
    return ReviewerContext(latest_customer_message=latest)


def _reviewer_context_view(context: ReviewerContext) -> dict[str, Any]:
    return {
        "previousTurn": context.previous_turn,
        "recentCustomerMessages": context.recent_customer_messages,
        "workflowState": context.workflow_state,
        "pendingOperation": context.pending_operation,
        "pendingInteraction": context.pending_interaction,
        "calendar": context.calendar,
        "page": context.page,
        "displayedVehicles": context.displayed_vehicles,
        "displayedOffers": context.displayed_offers,
        "displayedDealerships": context.displayed_dealerships,
        "pageVehicles": page_vehicle_identities(context.page_vehicles),
        "vehicleSearchState": context.vehicle_search_state,
        "trustedToolFacts": context.trusted_tool_facts,
    }


def _safe_review_feedback(error: Exception) -> str:
    """Give the retry actionable policy feedback without echoing model/user payloads."""
    if isinstance(error, ReviewValidationError):
        feedback = {
            "OPTIONAL_FIELD_IS_NOT_A_BLOCKER": (
                "Clarification was rejected because the cited field is optional. Correct to the "
                "earliest safe chooser, form, or business tool that can proceed now."
            ),
            "UNDECLARED_PRECONDITION_IS_NOT_A_BLOCKER": (
                "Clarification was rejected because its blocker is not a declared tool "
                "precondition. Correct to a safe executable proposal."
            ),
        }
        return feedback[error.code]
    message = str(error)
    if "requires at least one retrieved customer citation" in message:
        return "The answer claimed knowledge grounding without a retrieved customer citation."
    if "requires retrieved knowledge or current trusted tool facts" in message:
        return (
            "The factual answer had no allowed evidence; correct it to an authoritative tool call."
        )
    if "requires facts from a tool executed in this turn" in message:
        return "The answer claimed tool grounding before an evidence tool ran in this turn."
    if "invalid citation" in message:
        return "The answer cited evidence that was not retrieved for this customer turn."
    if "clarification requires" in message:
        return (
            "A clarification must identify a genuinely required schema field or declared "
            "precondition. Otherwise correct to a safe tool that can collect the choice."
        )
    if isinstance(error, ValidationError):
        locations = sorted(
            {
                ".".join(str(part) for part in item.get("loc", ())) or "review"
                for item in error.errors(
                    include_url=False,
                    include_context=False,
                    include_input=False,
                )
            }
        )
        fields = ", ".join(locations[:4])
        return (
            f"The selected review function had invalid fields at {fields}. Use exactly one of "
            "the provided review functions and match only its schema."
        )
    return (
        "The previous review failed deterministic validation. Use exactly one review function; "
        "accept with {}, or correct, clarify, or reject using only that function's fields."
    )


def _review_repair_tools(error: Exception) -> frozenset[str]:
    """Constrain repair to the outcome required by deterministic validation."""
    if isinstance(error, ReviewValidationError):
        return error.repair_tools
    return TURN_REVIEW_TOOLS
