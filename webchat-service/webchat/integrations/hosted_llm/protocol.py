"""Typed Responses proposal protocol, parsing, and application-owned replies."""

from __future__ import annotations

import json
from typing import Any

from webchat.domain.interactions import single_action_interaction
from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.retrieval import KnowledgeEntry
from webchat.orchestration.retrieval.candidates import CandidateSet

from ..contracts import (
    ProposalReview,
    ProviderReply,
    ResponseProposal,
    ToolCall,
    TurnProposal,
)

ANSWER_KNOWLEDGE_TOOL = "answer_from_knowledge"
RESPOND_SOCIAL_TOOL = "respond_socially"
ACCEPT_PENDING_INTERACTION_TOOL = "accept_pending_interaction"
DECLINE_PENDING_INTERACTION_TOOL = "decline_pending_interaction"
SOCIAL_RESPONSES = {
    "greeting": "Hello! How can I help with Northstar vehicles, dealerships, offers, or servicing?",
    "thanks": "You're welcome. Is there anything else I can help you with?",
    "farewell": "Goodbye. Thanks for contacting Northstar Motors.",
    "capabilities": (
        "I can help you find vehicles, compare models, view offers and dealerships, "
        "check workshop services, or start an enquiry or booking."
    ),
}


def response_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        if message.get("tool_calls"):
            for call in message["tool_calls"]:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call["id"],
                        "name": call["name"],
                        "arguments": json.dumps(call["arguments"], separators=(",", ":")),
                    }
                )
        elif message.get("role") == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message["tool_call_id"],
                    "output": message["content"],
                }
            )
        else:
            items.append({"role": message["role"], "content": message["content"]})
    return items
def _parse_proposal(
    payload: dict[str, Any],
    candidates: CandidateSet,
    catalogue: UnifiedToolCatalog,
    pending_interaction: dict[str, Any] | None = None,
) -> TurnProposal:
    customer_knowledge = _customer_knowledge(candidates)
    allowed = {definition.id for definition in candidates.tools} | {RESPOND_SOCIAL_TOOL}
    if customer_knowledge:
        allowed.add(ANSWER_KNOWLEDGE_TOOL)
    if _actionable_interaction(pending_interaction):
        allowed.update(
            {ACCEPT_PENDING_INTERACTION_TOOL, DECLINE_PENDING_INTERACTION_TOOL}
        )
    calls = _function_arguments(payload, expected_names=allowed)
    tool_calls: list[ToolCall] = []
    response: ResponseProposal | None = None
    interaction_decision = None
    for call_id, name, arguments in calls:
        if name in {
            ACCEPT_PENDING_INTERACTION_TOOL,
            DECLINE_PENDING_INTERACTION_TOOL,
        }:
            if arguments or interaction_decision is not None:
                raise ValueError("provider returned an invalid interaction decision")
            interaction_decision = (
                "accept" if name == ACCEPT_PENDING_INTERACTION_TOOL else "decline"
            )
            continue
        if name in {ANSWER_KNOWLEDGE_TOOL, RESPOND_SOCIAL_TOOL}:
            if response is not None:
                raise ValueError("provider returned more than one customer response")
            response = (
                _knowledge_response(arguments, customer_knowledge)
                if name == ANSWER_KNOWLEDGE_TOOL
                else _social_response(arguments)
            )
            continue
        definition = catalogue.get(name)
        validated = definition.validate_arguments(arguments)
        tool_calls.append(ToolCall(call_id, name, validated))
    branches = bool(tool_calls) + (response is not None) + (interaction_decision is not None)
    if branches == 0:
        raise ValueError("provider must return a typed capability call")
    if branches > 1:
        raise ValueError("provider must choose exactly one response branch")
    return TurnProposal(tool_calls, response, interaction_decision)


def _knowledge_answer_tool_definition(
    evidence: dict[str, KnowledgeEntry],
) -> dict[str, Any]:
    return {
        "type": "function",
        "name": ANSWER_KNOWLEDGE_TOOL,
        "description": (
            "Answer a static Northstar question by selecting the exact retrieved customer "
            "knowledge entries that answer it. The application writes the answer from those "
            "entries. Never use for live or operational information."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["citationIds"],
            "properties": {
                "citationIds": {
                    "type": "array",
                    "items": {"type": "string", "enum": sorted(evidence)},
                    "minItems": 1,
                    "maxItems": 3,
                    "uniqueItems": True,
                },
            },
        },
        "strict": False,
    }


def _social_response_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "name": RESPOND_SOCIAL_TOOL,
        "description": (
            "Select a fixed response only for a greeting, thanks, farewell, or a direct question "
            "about assistant capabilities. Never use for a Northstar information request, "
            "customer preference, operation, search, follow-up, or clarification."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["act"],
            "properties": {"act": {"type": "string", "enum": sorted(SOCIAL_RESPONSES)}},
        },
        "strict": False,
    }


def _interaction_decision_tool_definitions(
    pending_interaction: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not _actionable_interaction(pending_interaction):
        return []
    shared = {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }
    return [
        {
            "type": "function",
            "name": ACCEPT_PENDING_INTERACTION_TOOL,
            "description": (
                "Use only when the latest customer message semantically accepts the immediately "
                "preceding pending application interaction. Do not use for a new request, an "
                "ambiguous reply, a choice of one option, or requested free-form input."
            ),
            "parameters": shared,
            "strict": False,
        },
        {
            "type": "function",
            "name": DECLINE_PENDING_INTERACTION_TOOL,
            "description": (
                "Use only when the latest customer message semantically declines the immediately "
                "preceding pending application interaction. Do not use for a new request, an "
                "ambiguous reply, a choice of one option, or requested free-form input."
            ),
            "parameters": shared,
            "strict": False,
        },
    ]


def _actionable_interaction(pending_interaction: dict[str, Any] | None) -> bool:
    return bool(
        pending_interaction
        and pending_interaction.get("kind")
        in {"single_action", "choice", "protected_confirmation"}
    )


def _customer_knowledge(candidates: CandidateSet) -> dict[str, KnowledgeEntry]:
    return {entry.id: entry for entry in candidates.knowledge if entry.audience == "customer"}


def _knowledge_response(
    arguments: dict[str, Any], evidence: dict[str, KnowledgeEntry]
) -> ResponseProposal:
    citations = arguments.get("citationIds")
    if (
        not isinstance(citations, list)
        or not 1 <= len(citations) <= 3
        or len(citations) != len(set(citations))
        or not all(isinstance(item, str) and item in evidence for item in citations)
    ):
        raise ValueError("provider returned invalid customer knowledge citations")
    selected = [evidence[item] for item in citations]
    text = selected[0].text if len(selected) == 1 else "\n".join(
        f"- {entry.title}: {entry.text}" for entry in selected
    )
    return ResponseProposal("answer", text, tuple(citations), "knowledge")


def _social_response(arguments: dict[str, Any]) -> ResponseProposal:
    act = str(arguments.get("act") or "")
    if act not in SOCIAL_RESPONSES:
        raise ValueError("provider returned an invalid social response")
    return ResponseProposal("conversation", SOCIAL_RESPONSES[act])


def _provider_reply(
    proposal: TurnProposal,
    review: ProposalReview,
    candidates: CandidateSet,
) -> ProviderReply:
    response = proposal.response
    interaction = response.interaction if response else None
    if response and interaction is None:
        interaction = _knowledge_follow_up(response, candidates)
    return ProviderReply(
        text=response.text if response else "",
        tool_calls=proposal.tool_calls,
        review=review,
        response_mode=response.mode if response else None,
        citation_ids=response.citation_ids if response else (),
        interaction=interaction,
        interaction_decision=proposal.interaction_decision,
        suggestions=response.suggestions if response else (),
    )


def _knowledge_follow_up(
    response: ResponseProposal, candidates: CandidateSet
):
    actions = {
        entry.id: entry.follow_up_action
        for entry in candidates.knowledge
        if entry.follow_up_action is not None
    }
    selected = [actions[citation] for citation in response.citation_ids if citation in actions]
    unique = {json.dumps(action, sort_keys=True) for action in selected}
    if len(unique) != 1:
        return None
    return single_action_interaction(response.text, selected[0])


def _function_arguments(
    payload: dict[str, Any], expected_names: set[str]
) -> list[tuple[str, str, dict[str, Any]]]:
    parsed: list[tuple[str, str, dict[str, Any]]] = []
    for item in payload.get("output", []):
        if item.get("type") != "function_call":
            continue
        name = str(item.get("name") or "")
        call_id = str(item.get("call_id") or "")
        if name not in expected_names:
            raise ValueError(f"hosted LLM returned an unknown function: {name}")
        if not call_id:
            raise ValueError("hosted LLM returned a function call without an ID")
        try:
            arguments = json.loads(item.get("arguments", "{}"))
        except json.JSONDecodeError as error:
            raise ValueError("hosted LLM returned invalid function arguments") from error
        if not isinstance(arguments, dict):
            raise TypeError("hosted LLM returned non-object function arguments")
        parsed.append((call_id, name, arguments))
    return parsed
