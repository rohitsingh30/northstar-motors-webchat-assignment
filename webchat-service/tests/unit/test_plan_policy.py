from webchat.integrations.contracts import ProviderReply, ToolCall, TurnPlan
from webchat.orchestration.planning.plan_policy import (
    SAFE_CLARIFICATION,
    SemanticPlanPolicy,
)


class StubApplicationRouter:
    def __init__(self, reply: ProviderReply | None) -> None:
        self.reply = reply

    def route(self, messages):
        del messages
        return self.reply


def conversation_reply(text: str = "Generated prose") -> ProviderReply:
    return ProviderReply(
        "", plan=TurnPlan("conversation", "respond", response=text)
    )


def evaluate(
    gate: SemanticPlanPolicy,
    reply: ProviderReply,
    *,
    text: str,
    retry_used: bool = False,
):
    return gate.evaluate(
        reply,
        [{"role": "user", "content": text}],
        user_text=text,
        workflow_state={},
        retry_used=retry_used,
        conversation_id="conversation-1",
    )


def test_enforcement_replaces_conversation_response_with_application_route() -> None:
    routed = ProviderReply(
        "", [ToolCall("local-search", "search_vehicles", {"maxPricePence": 3_000_000})]
    )
    gate = SemanticPlanPolicy("enforce", StubApplicationRouter(routed))

    decision = evaluate(gate, conversation_reply(), text="show me cars under £30,000")

    assert decision.action == "replace"
    assert decision.reply == routed


def test_observe_mode_records_but_does_not_replace_response() -> None:
    original = conversation_reply()
    routed = ProviderReply("", [ToolCall("local-search", "search_vehicles", {})])
    gate = SemanticPlanPolicy("observe", StubApplicationRouter(routed))

    decision = evaluate(gate, original, text="show me cars")

    assert decision.action == "allow"
    assert decision.reply is original
    assert decision.reason == "application_route"


def test_supported_request_without_certain_route_gets_one_replan() -> None:
    gate = SemanticPlanPolicy("enforce", StubApplicationRouter(None))

    decision = evaluate(
        gate,
        conversation_reply(),
        text="I want to book an MOT appointment",
    )

    assert decision.action == "replan"
    assert decision.reason == "business_signal"


def test_repeated_conversation_response_uses_server_owned_clarification() -> None:
    gate = SemanticPlanPolicy("enforce", StubApplicationRouter(None))

    decision = evaluate(
        gate,
        conversation_reply("Invented workshop answer"),
        text="I want to book an MOT appointment",
        retry_used=True,
    )

    assert decision.action == "replace"
    assert decision.reply.plan is not None
    assert (decision.reply.plan.domain, decision.reply.plan.goal) == (
        "conversation",
        "clarify",
    )
    assert decision.reply.plan.response == SAFE_CLARIFICATION


def test_genuine_conversation_can_still_use_conversation_response() -> None:
    original = conversation_reply("You're welcome.")
    gate = SemanticPlanPolicy("enforce", StubApplicationRouter(None))

    decision = evaluate(gate, original, text="thanks")

    assert decision.action == "allow"
    assert decision.reply is original
