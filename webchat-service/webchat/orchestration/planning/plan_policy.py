"""Application-owned policy gate for validated semantic plans."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from webchat.integrations.contracts import ProviderReply, TurnPlan

logger = logging.getLogger(__name__)

GateMode = Literal["off", "observe", "enforce"]
GateAction = Literal["allow", "replace", "replan"]

REPLAN_INSTRUCTION = (
    "The previous conversation.respond plan was rejected by application policy because the latest "
    "request appears to need a supported Northstar tool or workflow. Re-plan this same customer "
    "turn once. Select the most specific business domain and goal, or conversation.clarify when a "
    "genuinely required choice is missing. Do not use conversation.respond."
)
SAFE_CLARIFICATION = (
    "I want to make sure I use the right live Northstar information. Please tell me whether you "
    "want vehicles, a dealership, an offer, or a workshop service."
)

_DIRECT_BUSINESS_WORDS = {
    "appointment",
    "appointments",
    "availability",
    "book",
    "booking",
    "bookings",
    "callback",
    "car",
    "cars",
    "compare",
    "dealer",
    "dealers",
    "dealership",
    "dealerships",
    "mot",
    "offer",
    "offers",
    "servicing",
    "test-drive",
    "vehicle",
    "vehicles",
    "workshop",
}
_SERVICE_QUALIFIERS = {
    "book",
    "booking",
    "cost",
    "duration",
    "interim",
    "major",
    "price",
    "schedule",
    "tyre",
    "tyres",
}
_VEHICLE_FOLLOW_UP_WORDS = {
    "automatic",
    "diesel",
    "details",
    "electric",
    "fuel",
    "gearbox",
    "hybrid",
    "manual",
    "mileage",
    "petrol",
    "price",
    "reserved",
    "sold",
}


class ApplicationReplyRouter(Protocol):
    """Return a deterministic tool call/fact response, or None when no route is certain."""

    def route(self, messages: list[dict[str, Any]]) -> ProviderReply | None: ...


@dataclass(frozen=True)
class GateDecision:
    action: GateAction
    reply: ProviderReply
    reason: str


class SemanticPlanPolicy:
    """Validate provider plans and recover unsafe conversational fallbacks."""

    def __init__(
        self,
        mode: GateMode,
        application_router: ApplicationReplyRouter | None = None,
    ) -> None:
        self.mode = mode
        self.application_router = application_router

    def evaluate(
        self,
        reply: ProviderReply,
        history: list[dict[str, Any]],
        *,
        user_text: str,
        workflow_state: dict[str, Any],
        retry_used: bool,
        conversation_id: str,
    ) -> GateDecision:
        if reply.plan is None:
            return GateDecision("allow", reply, "missing_plan")
        if self.mode == "off" or not _is_conversation_response(reply):
            return GateDecision("allow", reply, "validated_business_plan")

        replacement = (
            self.application_router.route(history)
            if self.application_router is not None
            else None
        )
        if replacement is not None:
            return self._decision(
                "replace",
                replacement,
                "application_route",
                conversation_id,
                original_reply=reply,
            )

        if not _looks_like_supported_request(user_text, workflow_state):
            return self._decision(
                "allow", reply, "conversation_or_unsupported", conversation_id
            )

        if retry_used:
            return self._decision(
                "replace",
                ProviderReply(
                    "",
                    plan=TurnPlan(
                        "conversation", "clarify", response=SAFE_CLARIFICATION
                    ),
                ),
                "retry_exhausted",
                conversation_id,
                original_reply=reply,
            )
        return self._decision("replan", reply, "business_signal", conversation_id)

    def _decision(
        self,
        action: GateAction,
        reply: ProviderReply,
        reason: str,
        conversation_id: str,
        original_reply: ProviderReply | None = None,
    ) -> GateDecision:
        effective_action = "allow" if self.mode == "observe" else action
        logger.info(
            "semantic plan policy evaluated",
            extra={
                "context": {
                    "mode": self.mode,
                    "decision": action,
                    "effectiveDecision": effective_action,
                    "reason": reason,
                    "conversationId": conversation_id,
                }
            },
        )
        effective_reply = original_reply if self.mode == "observe" else reply
        return GateDecision(effective_action, effective_reply or reply, reason)


def _is_conversation_response(reply: ProviderReply) -> bool:
    return bool(
        reply.plan is not None
        and reply.plan.domain == "conversation"
        and reply.plan.goal == "respond"
    )


def _looks_like_supported_request(
    user_text: str, workflow_state: dict[str, Any]
) -> bool:
    normalized = user_text.lower().replace("test drive", "test-drive")
    words = set(re.findall(r"[a-z]+(?:-[a-z]+)?", normalized))
    if words & _DIRECT_BUSINESS_WORDS:
        return True
    if "part exchange" in normalized or "opening hours" in normalized:
        return True
    if "service" in words and words & _SERVICE_QUALIFIERS:
        return True
    return workflow_state.get("domain") == "vehicle" and bool(
        words & _VEHICLE_FOLLOW_UP_WORDS
    )
