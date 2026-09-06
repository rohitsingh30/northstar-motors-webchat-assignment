"""Typed Responses proposal protocol, parsing, and application-owned replies."""

from __future__ import annotations

import json
import re
from typing import Any

from webchat.domain.interactions import (
    intent_choice_interaction,
    reference_choice_interaction,
    single_action_interaction,
)
from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.contracts.plan import (
    AgentPlan,
    Clarification,
    InteractionProposal,
    PlanIntent,
    PlannedToolCall,
    WorkflowStart,
)
from webchat.orchestration.customer_language import validate_customer_language
from webchat.orchestration.intent_contracts import primary_intent, supported_intents
from webchat.orchestration.retrieval import KnowledgeEntry
from webchat.orchestration.retrieval.candidates import CandidateSet

from ..contracts import (
    ProviderReply,
    ResponseProposal,
    ToolCall,
    TurnProposal,
)

ANSWER_KNOWLEDGE_TOOL = "answer_from_knowledge"
RESPOND_SOCIAL_TOOL = "respond_socially"
ASK_CLARIFICATION_TOOL = "ask_conversational_clarification"
ASK_INTENT_CLARIFICATION_TOOL = "clarify_customer_intent"
ASK_REFERENCE_CLARIFICATION_TOOL = "clarify_customer_reference"
ACCEPT_PENDING_INTERACTION_TOOL = "accept_pending_interaction"
DECLINE_PENDING_INTERACTION_TOOL = "decline_pending_interaction"
PROPOSE_VEHICLE_NAVIGATION_TOOL = "propose_open_vehicle_detail"
SOCIAL_RESPONSES = {
    "greeting": "Hello! How can I help with Northstar vehicles, dealerships, offers, or servicing?",
    "thanks": "You're welcome. Is there anything else I can help you with?",
    "farewell": "Goodbye. Thanks for contacting Northstar Motors.",
    "capabilities": (
        "I can help you find vehicles, compare models, view offers and dealerships, "
        "check workshop services, or start an enquiry or booking. What would you like to do?"
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
    trusted_vehicle_ids: set[str] | None = None,
    trusted_entity_references: set[str] | None = None,
    authoritative_intent_candidates: tuple[str, ...] = (),
    authoritative_reference_candidates: tuple[str, ...] = (),
    authoritative_reference_intents: tuple[str, ...] = (),
    allow_generic_clarification: bool | None = None,
) -> TurnProposal:
    customer_knowledge = _customer_knowledge(candidates)
    allowed = {definition.id for definition in candidates.tools} | {RESPOND_SOCIAL_TOOL}
    if allow_generic_clarification is None:
        allow_generic_clarification = bool(_clarifiable_candidate_ids(candidates))
    if allow_generic_clarification:
        allowed.add(ASK_CLARIFICATION_TOOL)
    if _semantic_intent_candidates(candidates) or authoritative_intent_candidates:
        allowed.add(ASK_INTENT_CLARIFICATION_TOOL)
    if customer_knowledge:
        allowed.add(ANSWER_KNOWLEDGE_TOOL)
    if _actionable_interaction(pending_interaction):
        allowed.update(
            {ACCEPT_PENDING_INTERACTION_TOOL, DECLINE_PENDING_INTERACTION_TOOL}
        )
    trusted_vehicle_ids = trusted_vehicle_ids or set()
    trusted_references = {
        *(trusted_entity_references or set()),
        *(f"vehicle:{vehicle_id}" for vehicle_id in trusted_vehicle_ids),
    }
    if len(trusted_references) >= 2 or authoritative_reference_candidates:
        allowed.add(ASK_REFERENCE_CLARIFICATION_TOOL)
    if trusted_vehicle_ids:
        allowed.add(PROPOSE_VEHICLE_NAVIGATION_TOOL)
    calls = _function_arguments(payload, expected_names=allowed)
    tool_calls: list[ToolCall] = []
    response: ResponseProposal | None = None
    interaction_decision = None
    interaction_proposal = None
    for call_id, name, arguments in calls:
        if name == PROPOSE_VEHICLE_NAVIGATION_TOOL:
            reference = str(arguments.get("entityReference") or "")
            vehicle_id = reference.removeprefix("vehicle:")
            if set(arguments) != {"entityReference"} or vehicle_id not in trusted_vehicle_ids:
                raise ValueError("provider proposed an untrusted vehicle navigation target")
            if interaction_proposal is not None:
                raise ValueError("provider returned more than one interaction proposal")
            interaction_proposal = InteractionProposal(
                kind="open_vehicle_detail",
                entityReference=reference,
            )
            continue
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
        if name in {
            ANSWER_KNOWLEDGE_TOOL,
            RESPOND_SOCIAL_TOOL,
            ASK_CLARIFICATION_TOOL,
            ASK_INTENT_CLARIFICATION_TOOL,
            ASK_REFERENCE_CLARIFICATION_TOOL,
        }:
            if response is not None:
                raise ValueError("provider returned more than one customer response")
            response = (
                _knowledge_response(arguments, customer_knowledge)
                if name == ANSWER_KNOWLEDGE_TOOL
                else _intent_clarification_response(
                    arguments,
                    candidates,
                    authoritative_intent_candidates,
                )
                if name == ASK_INTENT_CLARIFICATION_TOOL
                else _reference_clarification_response(
                    arguments,
                    trusted_references,
                    set(_semantic_intent_candidates(candidates)),
                    authoritative_reference_candidates,
                    authoritative_reference_intents,
                )
                if name == ASK_REFERENCE_CLARIFICATION_TOOL
                else _clarification_response(arguments, candidates, catalogue)
                if name == ASK_CLARIFICATION_TOOL
                else _social_response(arguments)
            )
            continue
        definition = catalogue.get(name)
        compatible_intents = supported_intents(name)
        declared_value = arguments.pop("intentKind", None)
        declared_intent = (
            next(iter(compatible_intents))
            if declared_value is None and len(compatible_intents) == 1
            else str(declared_value or "")
        )
        if declared_intent not in compatible_intents:
            raise ValueError("tool call has a missing or incompatible intent declaration")
        validated = definition.validate_provider_arguments(arguments)
        tool_calls.append(ToolCall(call_id, name, validated, intent_kind=declared_intent))
    mixed_grounded_answer = bool(
        tool_calls
        and response is not None
        and response.mode == "answer"
        and response.grounding == "knowledge"
    )
    branches = (
        bool(tool_calls)
        + (response is not None and not mixed_grounded_answer)
        + (interaction_decision is not None)
        + (interaction_proposal is not None)
    )
    if branches == 0:
        raise ValueError("provider must return a typed capability call")
    if branches > 1:
        raise ValueError("provider must choose exactly one response branch")
    if len(tool_calls) + bool(response) > 4:
        raise ValueError("provider may plan at most four independent intents")
    if response is not None:
        validate_customer_language(response.text)
    tool_calls = _validate_internal_plan(
        tool_calls,
        response=response,
        interaction_proposal=interaction_proposal,
        catalogue=catalogue,
    )
    return TurnProposal(tool_calls, response, interaction_decision, interaction_proposal)


def _validate_internal_plan(
    calls: list[ToolCall],
    *,
    response: ResponseProposal | None,
    interaction_proposal: InteractionProposal | None,
    catalogue: UnifiedToolCatalog,
) -> list[ToolCall]:
    """Convert provider function calls into the versioned internal plan contract.

    Provider call IDs are transport identifiers and may not satisfy our stable-ID grammar, so the
    application creates canonical plan IDs and carries only the intent ID into execution.
    """
    if calls:
        has_knowledge = response is not None
        intents = ([
            PlanIntent(
                intentId="intent-1",
                kind="capability",
                order=1,
                goal="Answer from selected approved customer knowledge",
                status="ready",
            )
        ] if has_knowledge else []) + [
            PlanIntent(
                intentId=f"intent-{index + int(has_knowledge)}",
                kind=call.intent_kind or primary_intent(call.name),
                order=index + int(has_knowledge),
                goal=f"Execute the validated {call.name} capability",
                status="ready",
            )
            for index, call in enumerate(calls, start=1)
        ]
        tool_intents = intents[1:] if has_knowledge else intents
        planned_calls = [
            PlannedToolCall(
                callId=f"call-{index}",
                intentId=tool_intents[index - 1].intentId,
                tool=call.name,
                arguments=call.arguments,
            )
            for index, call in enumerate(calls, start=1)
        ]
        workflow_calls = [
            (call, intent)
            for call, intent in zip(calls, tool_intents, strict=True)
            if catalogue.get(call.name).result_mode == "workflow"
        ]
        workflow = (
            WorkflowStart(
                kind=_workflow_kind(workflow_calls[0][0].name),
                intentId=workflow_calls[0][1].intentId,
            )
            if workflow_calls
            else None
        )
        AgentPlan(
            mode="tool_plan",
            intents=intents,
            toolCalls=planned_calls,
            workflowStart=workflow,
        )
        return [
            ToolCall(call.id, call.name, call.arguments, intent.intentId, intent.kind)
            for call, intent in zip(calls, tool_intents, strict=True)
        ]
    if interaction_proposal is not None:
        AgentPlan(
            mode="tool_plan",
            intents=[
                PlanIntent(
                    intentId="intent-1",
                    kind="vehicle_navigation",
                    order=1,
                    goal="Confirm navigation to the selected trusted vehicle",
                    status="ready",
                )
            ],
            interactionProposal=interaction_proposal,
        )
    elif response is not None and response.mode == "clarify":
        semantic_ambiguity = bool(response.candidate_intents)
        entity_ambiguity = bool(response.candidate_references)
        AgentPlan(
            mode="clarification",
            intents=[
                PlanIntent(
                    intentId="intent-1",
                    kind="capability",
                    order=1,
                    goal=(
                        "Clarify the customer's intended capability"
                        if semantic_ambiguity
                        else "Clarify which trusted entity the customer means"
                        if entity_ambiguity
                        else f"Collect required input for {response.blocking_tool}"
                    ),
                    status="needs_clarification",
                )
            ],
            clarification=Clarification(
                clarificationId="clarification-1",
                intentId="intent-1",
                reason=response.clarification_reason or "declared_tool_blocker",
                blockingTool=response.blocking_tool,
                blockingFields=list(response.blocking_fields),
                blockingPreconditions=list(response.blocking_preconditions),
                question=response.text,
                candidateIntents=list(response.candidate_intents),
                candidateReferences=list(response.candidate_references),
            ),
        )
    elif response is not None:
        AgentPlan(
            mode="direct_response",
            intents=[
                PlanIntent(
                    intentId="intent-1",
                    kind="social" if response.mode == "conversation" else "capability",
                    order=1,
                    goal="Answer from approved static content",
                    status="ready",
                )
            ],
        )
    return calls


def _workflow_kind(tool: str) -> str:
    mapping = {
        "request_workshop_booking_lookup_form": "booking_lookup",
        "prepare_workshop_amendment": "booking_amendment",
        "prepare_workshop_cancellation": "booking_cancellation",
        "prepare_sales_enquiry": "sales_enquiry",
        "prepare_test_drive": "test_drive",
        "prepare_workshop_booking": "workshop_booking",
        "prepare_vehicle_interest": "vehicle_interest",
        "prepare_callback": "callback",
        "prepare_dealership_message": "dealership_message",
        "prepare_part_exchange": "part_exchange",
        "request_part_exchange_estimate_form": "part_exchange",
        "request_offer_enquiry_form": "sales_enquiry",
    }
    try:
        return mapping[tool]
    except KeyError as error:
        raise ValueError(f"workflow tool has no conversational kind: {tool}") from error


def _vehicle_navigation_tool_definition(vehicle_ids: set[str]) -> dict[str, Any] | None:
    if not vehicle_ids:
        return None
    return {
        "type": "function",
        "name": PROPOSE_VEHICLE_NAVIGATION_TOOL,
        "description": (
            "Propose opening one trusted vehicle in the host website only when the customer asks "
            "to open, follow, or view its full website details. This proposal never navigates; "
            "the application will ask a mandatory final confirmation. For facts in chat, use "
            "get_vehicle instead."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["entityReference"],
            "properties": {
                "entityReference": {
                    "type": "string",
                    "enum": [f"vehicle:{value}" for value in sorted(vehicle_ids)],
                }
            },
        },
        "strict": True,
    }


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


def _clarifiable_candidate_ids(candidates: CandidateSet) -> list[str]:
    return [
        definition.id
        for definition in candidates.tools
        if definition.input_schema.get("required") or definition.preconditions
    ]


def _semantic_intent_candidates(candidates: CandidateSet) -> list[str]:
    intents = sorted(
        {
            intent
            for definition in candidates.tools
            for intent in supported_intents(definition.id)
        }
    )
    return intents if len(intents) >= 2 else []


def _intent_clarification_tool_definition(
    candidates: CandidateSet,
    authoritative_intents: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    candidate_intents = list(authoritative_intents) or _semantic_intent_candidates(candidates)
    if not candidate_intents:
        return None
    properties: dict[str, Any] = {
        "question": {"type": "string", "minLength": 1, "maxLength": 500},
    }
    required = ["question"]
    if not authoritative_intents:
        required.append("candidateIntents")
        properties["candidateIntents"] = {
            "type": "array",
            "items": {"type": "string", "enum": candidate_intents},
            "minItems": 2,
            "maxItems": 4,
            "uniqueItems": True,
        }
    return {
        "type": "function",
        "name": ASK_INTENT_CLARIFICATION_TOOL,
        "description": (
            "Ask one concise question only when the customer's words genuinely support two or "
            "more different retrieved business intents and choosing either one would materially "
            "change the operation. List the two to four candidate intents represented by the "
            "question. Do not use this because of a spelling mistake, an optional field, or an "
            "entity that trusted context resolves uniquely. Do not include business facts, IDs, "
            "prices, availability claims, URLs, completion claims, or implementation context."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": required,
            "properties": properties,
        },
        "strict": False,
    }


def _clarification_tool_definition(candidates: CandidateSet) -> dict[str, Any] | None:
    blocking_tools = _clarifiable_candidate_ids(candidates)
    if not blocking_tools:
        return None
    return {
        "type": "function",
        "name": ASK_CLARIFICATION_TOOL,
        "description": (
            "Ask one concise conversational clarification only when the latest request cannot be "
            "resolved safely with an available tool. Name the retrieved business tool that is "
            "blocked and cite only its required schema fields or declared preconditions. Optional "
            "arguments and choices between valid search scopes are not blockers. Do not include business facts, IDs, prices, "
            "availability claims, URLs, claims that an operation completed, or references to page "
            "or application context. For test-drive availability, retrieve live vehicle options "
            "instead of asking a bare vehicle question."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "question",
                "blockingTool",
                "blockingFields",
                "blockingPreconditions",
            ],
            "properties": {
                "question": {"type": "string", "minLength": 1, "maxLength": 500},
                "blockingTool": {"type": "string", "enum": blocking_tools},
                "blockingFields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 8,
                },
                "blockingPreconditions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 8,
                },
            },
        },
        "strict": False,
    }


def _reference_clarification_tool_definition(
    trusted_references: set[str],
    candidate_intents: set[str] | None = None,
    authoritative_references: tuple[str, ...] = (),
    authoritative_intents: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    if authoritative_references:
        if (
            not 2 <= len(authoritative_references) <= 12
            or len(authoritative_references) != len(set(authoritative_references))
            or not set(authoritative_references).issubset(trusted_references)
            or len({reference.split(":", 1)[0] for reference in authoritative_references}) != 1
            or len(authoritative_intents) != 1
        ):
            return None
        return {
            "type": "function",
            "name": ASK_REFERENCE_CLARIFICATION_TOOL,
            "description": (
                "Ask one concise question that helps the customer identify which entity they "
                "mean. The application already owns and retains the complete trusted candidate "
                "set and continuation intent. Ask only the question; do not enumerate choices, "
                "expose identifiers, invent entity facts, run a new search, or choose for the "
                "customer. For a large set, invite a useful descriptor such as make and model, "
                "location, service name, or offer title as appropriate."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["question"],
                "properties": {
                    "question": {"type": "string", "minLength": 1, "maxLength": 500},
                },
            },
            "strict": False,
        }
    intents = sorted(candidate_intents or ())
    if len(trusted_references) < 2 or not intents:
        return None
    return {
        "type": "function",
        "name": ASK_REFERENCE_CLARIFICATION_TOOL,
        "description": (
            "Ask one concise question when the customer's business intent is clear but their "
            "entity description matches two or more trusted displayed entities. Preserve the "
            "displayed option numbering by citing every matching candidate reference; the "
            "application renders their ordered labels as a bold bullet list. Ask only the lead-in "
            "question and do not enumerate candidate names in its text. Do not run a new search, "
            "reorder the options, choose for the customer, or claim a selection."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": ["question", "candidateReferences", "continuationIntent"],
            "properties": {
                "question": {"type": "string", "minLength": 1, "maxLength": 500},
                "candidateReferences": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": sorted(trusted_references),
                    },
                    "minItems": 2,
                    "maxItems": 4,
                    "uniqueItems": True,
                },
                "continuationIntent": {
                    "type": "string",
                    "enum": intents,
                    "description": (
                        "The already-understood customer goal that will continue after the "
                        "entity is resolved."
                    ),
                },
            },
        },
        "strict": False,
    }


def _clarification_response(
    arguments: dict[str, Any],
    candidates: CandidateSet,
    catalogue: UnifiedToolCatalog,
) -> ResponseProposal:
    question = " ".join(str(arguments.get("question") or "").split())
    if not question or len(question) > 500:
        raise ValueError("clarification question is invalid")
    expected = {"question", "blockingTool", "blockingFields", "blockingPreconditions"}
    if set(arguments) != expected:
        raise ValueError("clarification has invalid arguments")
    if re.search(r"(?:https?://|£|\b(?:veh|offer|booking|enquiry)-[A-Za-z0-9-]+\b)", question, re.IGNORECASE):
        raise ValueError("clarification question contains ungrounded business data")
    blocking_tool = str(arguments["blockingTool"])
    candidate_ids = {definition.id for definition in candidates.tools}
    if blocking_tool not in candidate_ids:
        raise ValueError("clarification cites an unretrieved tool")
    definition = catalogue.get(blocking_tool)
    blocking_fields = tuple(str(value) for value in arguments["blockingFields"])
    blocking_preconditions = tuple(
        str(value) for value in arguments["blockingPreconditions"]
    )
    if not blocking_fields and not blocking_preconditions:
        raise ValueError("clarification requires a schema field or catalogue precondition")
    required_fields = set(definition.input_schema.get("required") or [])
    if not set(blocking_fields).issubset(required_fields):
        raise ValueError("clarification cites an optional tool field")
    if not set(blocking_preconditions).issubset(definition.preconditions):
        raise ValueError("clarification cites an undeclared tool precondition")
    validate_customer_language(question)
    return ResponseProposal(
        "clarify",
        question,
        blocking_tool=blocking_tool,
        blocking_fields=blocking_fields,
        blocking_preconditions=blocking_preconditions,
        clarification_reason="declared_tool_blocker",
    )


def _intent_clarification_response(
    arguments: dict[str, Any],
    candidates: CandidateSet,
    authoritative_intents: tuple[str, ...] = (),
) -> ResponseProposal:
    question = " ".join(str(arguments.get("question") or "").split())
    if not question or len(question) > 500:
        raise ValueError("intent clarification question is invalid")
    expected_arguments = {"question"} if authoritative_intents else {
        "question",
        "candidateIntents",
    }
    if set(arguments) != expected_arguments:
        raise ValueError("intent clarification has invalid arguments")
    if re.search(
        r"(?:https?://|£|\b(?:veh|offer|booking|enquiry)-[A-Za-z0-9-]+\b)",
        question,
        re.IGNORECASE,
    ):
        raise ValueError("intent clarification question contains ungrounded business data")
    candidate_intents = authoritative_intents or tuple(
        str(value) for value in arguments["candidateIntents"]
    )
    available = set(authoritative_intents) or set(_semantic_intent_candidates(candidates))
    if not 2 <= len(candidate_intents) <= 4 or (
        len(candidate_intents) != len(set(candidate_intents))
        or not set(candidate_intents).issubset(available)
    ):
        raise ValueError("intent clarification cites invalid candidate intents")
    validate_customer_language(question)
    return ResponseProposal(
        "clarify",
        question,
        clarification_reason="semantic_ambiguity",
        candidate_intents=candidate_intents,
    )


def _reference_clarification_response(
    arguments: dict[str, Any],
    trusted_references: set[str],
    candidate_intents: set[str],
    authoritative_references: tuple[str, ...] = (),
    authoritative_intents: tuple[str, ...] = (),
) -> ResponseProposal:
    question = " ".join(str(arguments.get("question") or "").split())
    if not question or len(question) > 500:
        raise ValueError("reference clarification question is invalid")
    expected_arguments = (
        {"question"}
        if authoritative_references
        else {"question", "candidateReferences", "continuationIntent"}
    )
    if set(arguments) != expected_arguments:
        raise ValueError("reference clarification has invalid arguments")
    references = authoritative_references or tuple(
        str(value) for value in arguments["candidateReferences"]
    )
    namespaces = {reference.split(":", 1)[0] for reference in references if ":" in reference}
    if (
        not 2 <= len(references) <= 12
        or len(references) != len(set(references))
        or not set(references).issubset(trusted_references)
        or len(namespaces) != 1
    ):
        raise ValueError("reference clarification cites invalid trusted entities")
    continuation_intent = (
        authoritative_intents[0]
        if authoritative_references and len(authoritative_intents) == 1
        else str(arguments.get("continuationIntent") or "")
    )
    available_intents = set(authoritative_intents) or candidate_intents
    if continuation_intent not in available_intents:
        raise ValueError("reference clarification cites an unavailable customer intent")
    if re.search(
        r"(?:https?://|£|\b(?:veh|offer|booking|enquiry)-[A-Za-z0-9-]+\b)",
        question,
        re.IGNORECASE,
    ):
        raise ValueError("reference clarification exposes raw business identifiers")
    validate_customer_language(question)
    return ResponseProposal(
        "clarify",
        question,
        clarification_reason="entity_ambiguity",
        candidate_references=references,
        continuation_intent=continuation_intent,
    )


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
    candidates: CandidateSet,
    *,
    customer_reason: str | None = None,
) -> ProviderReply:
    response = proposal.response
    interaction = response.interaction if response else None
    if response and interaction is None and response.candidate_references:
        interaction = reference_choice_interaction(
            response.text,
            response.candidate_references,
            str(response.continuation_intent),
        )
    elif response and interaction is None and response.candidate_intents:
        interaction = intent_choice_interaction(
            response.text,
            response.candidate_intents,
        )
    if response and interaction is None:
        interaction = _knowledge_follow_up(
            response,
            candidates,
            customer_reason=customer_reason,
        )
    mixed_grounded_answer = bool(response and proposal.tool_calls)
    approved_content = tuple(
        {
            "id": entry.id,
            "title": entry.title,
            "text": entry.text,
            "source": entry.source,
        }
        for entry in candidates.knowledge
        if response and entry.id in response.citation_ids
    ) if mixed_grounded_answer else ()
    return ProviderReply(
        text=response.text if response and not mixed_grounded_answer else "",
        tool_calls=proposal.tool_calls,
        response_mode=response.mode if response else None,
        citation_ids=response.citation_ids if response else (),
        interaction=interaction,
        interaction_decision=proposal.interaction_decision,
        suggestions=response.suggestions if response else (),
        interaction_proposal=proposal.interaction_proposal,
        approved_content=approved_content,
    )


def _knowledge_follow_up(
    response: ResponseProposal,
    candidates: CandidateSet,
    *,
    customer_reason: str | None,
):
    entries = {
        entry.id: entry
        for entry in candidates.knowledge
        if entry.follow_up_action is not None
    }
    selected = [entries[citation] for citation in response.citation_ids if citation in entries]
    unique_actions = {
        json.dumps(entry.follow_up_action, sort_keys=True) for entry in selected
    }
    if len(unique_actions) != 1:
        return None
    handoff_values = {
        entry.follow_up_handoff.model_dump_json(exclude_none=True)
        if entry.follow_up_handoff is not None
        else ""
        for entry in selected
    }
    if len(handoff_values) != 1:
        return None
    handoff = selected[0].follow_up_handoff
    reason = str(customer_reason or "").strip()
    if handoff is not None and reason:
        handoff = handoff.model_copy(update={"customerReason": reason})
    return single_action_interaction(
        response.text,
        selected[0].follow_up_action,
        handoff=handoff,
    )


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
