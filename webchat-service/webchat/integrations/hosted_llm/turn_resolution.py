"""Hosted semantic resolution before capability planning.

This phase decides how a message relates to the conversation.  It cannot call business tools and
it cannot create trusted identifiers; it may only select references and context IDs supplied by
the application.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from difflib import SequenceMatcher
from typing import Any, get_args

from webchat.domain.capabilities import CapabilityRegistry
from webchat.integrations.contracts import PlanningContext
from webchat.orchestration.appointments import customer_choice_context
from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.contracts.plan import IntentKind
from webchat.orchestration.contracts.semantics import (
    REFERENCE_FIELD_NAMESPACES,
    TurnUnderstanding,
)
from webchat.orchestration.intent_contracts import supported_intents

RESOLVE_TURN_TOOL = "resolve_conversation_turn"

_MULTI_VALUE_VEHICLE_FIELDS = {
    "make",
    "model",
    "colour",
    "fuelType",
    "transmission",
    "bodyStyle",
}

_CATEGORICAL_REFERENCE_FIELDS = {
    "make",
    "model",
    "variant",
    "bodyStyle",
    "colour",
    "fuelType",
    "transmission",
}

_REFERENCE_DESCRIPTOR_ALIASES = {
    "vehicle": {
        "make": ("make",),
        "model": ("model",),
        "variant": ("variant",),
        "year": ("year",),
        "bodyStyle": ("bodyStyle",),
        "colour": ("colour",),
        "fuelType": ("fuelType",),
        "transmission": ("transmission",),
        "availability": ("availability",),
        "dealershipTown": ("dealershipTown", "town"),
        "town": ("town", "dealershipTown"),
        "minYear": ("year",),
        "maxYear": ("year",),
        "minPricePence": ("pricePence",),
        "maxPricePence": ("pricePence",),
        "minMileage": ("mileage",),
        "maxMileage": ("mileage",),
    },
    "offer": {
        "make": ("make",),
        "model": ("model",),
        "productType": ("productType",),
    },
    "dealership": {
        "dealershipTown": ("town", "dealershipTown"),
        "town": ("town", "dealershipTown"),
    },
    "service": {
        "serviceTypeName": ("serviceTypeName", "serviceName", "name", "label"),
    },
    "appointment": {
        "make": ("make",),
        "model": ("model",),
        "variant": ("variant",),
        "year": ("year",),
        "dealershipTown": ("dealershipTown", "town"),
        "town": ("town", "dealershipTown"),
        "serviceTypeName": ("serviceTypeName", "serviceName"),
    },
}

_FINITE_CHOICE_NUMBER_WORDS = {
    "one": 1,
    "first": 1,
    "two": 2,
    "second": 2,
    "three": 3,
    "third": 3,
    "four": 4,
    "fourth": 4,
    "five": 5,
    "fifth": 5,
    "six": 6,
    "sixth": 6,
    "seven": 7,
    "seventh": 7,
    "eight": 8,
    "eighth": 8,
    "nine": 9,
    "ninth": 9,
    "ten": 10,
    "tenth": 10,
    "eleven": 11,
    "eleventh": 11,
    "twelve": 12,
    "twelfth": 12,
}


def resolution_request(
    *,
    model: str,
    context: PlanningContext,
    input_fields: set[str] | dict[str, dict[str, Any]],
    input_catalogue: list[dict[str, Any]],
    intent_catalogue: list[dict[str, Any]],
    knowledge_catalogue: list[dict[str, str]] | None = None,
    feedback: str | None = None,
    outcome_disambiguation: bool = False,
) -> dict[str, Any]:
    references = sorted(trusted_context_references(context))
    knowledge_context_ids = {
        str(item["id"])
        for item in (knowledge_catalogue or [])
        if isinstance(item, dict) and str(item.get("id") or "").startswith("knowledge:")
    }
    context_ids = sorted(trusted_context_ids(context) | knowledge_context_ids)
    customer_context_ids = sorted(trusted_customer_context_ids(context))
    question = context.pending_interaction or None
    envelope = {
        "latestCustomerMessage": context.latest_customer_message,
        "activeGoal": context.workflow_state or None,
        "openQuestion": question,
        "displayedEntities": {
            "vehicles": context.displayed_vehicles,
            "offers": context.displayed_offers,
            "dealerships": context.displayed_dealerships,
            "choices": customer_choice_context(context.displayed_choices),
            "pageVehicles": context.page_vehicles,
        },
        "calendar": context.calendar,
        "intentCatalogue": intent_catalogue,
        "inputFieldCatalogue": input_catalogue,
        "rankedKnowledgeEvidence": list(knowledge_catalogue or []),
        "rankedConversationEvidence": context.context_evidence,
    }
    if feedback:
        envelope["previousValidationFailure"] = feedback
    outcome_disambiguation_instruction = (
        "This is a second semantic outcome pass because an earlier interpretation would "
        "consume a finite open choice from a natural sentence rather than a closed label, ordinal, "
        "or trusted action. Re-evaluate the latest message's requested outcome before relating its "
        "entity references to the open question. A displayed entity may be the subject of an "
        "information request without being selected for the open transaction. You cannot return "
        "answer_open_question in this pass. If the customer is genuinely choosing the displayed "
        "item, return the open question's goal intent and selected reference using another suitable "
        "dialogue act. If the customer explicitly requests information, a different transaction, "
        "or multiple outcomes, return those outcomes completely. Do not treat the mere presence of "
        "a candidate label as evidence of selection. "
        if outcome_disambiguation
        else ""
    )
    return {
        "model": model,
        "instructions": (
            outcome_disambiguation_instruction
            + "Resolve what the customer's latest message means in this conversation before any "
            "business operation is selected. Use the active goal and open question as primary "
            "state. Use ranked conversation evidence only as supporting context; structural "
            "relationships outrank raw recency. Interpret natural answers, corrections, references, "
            "interruptions, and topic changes semantically—never require the customer to repeat "
            "workflow keywords. A short reply may answer an open question in any wording. Select "
            "answer_open_question and goalRelation open_question only when openQuestion contains "
            "a non-null question_id supplied by the application. Otherwise interpret a short "
            "reply from the transcript as a new, continued, or switched goal and leave "
            "answeredQuestionId null. "
            "intentKinds by meaning from intentCatalogue, not by similarity between labels. "
            "intentKinds are requested outcomes, not a list of possibly related capabilities. "
            "A factual question remains a read-only information outcome even when its topic could "
            "later lead to a transaction. Select a transactional workflow only when the customer "
            "asks to create, send, book, call, register, value, amend, cancel, or otherwise carry "
            "out that business action. Verbs such as get, receive, give, show, tell, explain, or "
            "understand describe an information outcome when their object is information, details, "
            "terms, policy, price, or an answer; they do not authorize a workflow. A transactional "
            "topic such as finance, sales, service, offers, or part exchange does not by itself "
            "change an information request into an enquiry. Do not turn a question about whether something is possible, "
            "available, supported, or true into a sales enquiry or contact workflow unless the "
            "customer explicitly asks for dealership follow-up. "
            "rankedKnowledgeEvidence contains customer-safe static policies retrieved for the "
            "latest wording. When one directly addresses a factual question, resolve the turn as "
            "business_information and include that evidence's supplied knowledge: context ID in "
            "supportingContextIds so the application can cite it exactly. Include no knowledge: "
            "context ID when none directly answers the question or live operational data is "
            "required. Do not convert a factual answer into a related transaction. "
            "Set resultPresentation to explicit_request whenever the latest message directly asks "
            "to receive, see, list, show, or repeat a concrete business result, including natural "
            "questions such as asking what the opening hours or contact details are. Use default "
            "when a read-only result is only incidental context for another goal or answer. "
            "Several acceptable values for one filter are one outcome: use the corresponding "
            "plural input field when supplied (for example makes instead of repeating make), "
            "and never repeat a resolvedInputs field. "
            "Resolve customer constraints and requested constraint changes using "
            "inputFieldCatalogue; its descriptions and allowed values define their meaning. A "
            "customer-authored entity name or identity description is a resolved q input only "
            "within the scope declared by that field's catalogue description, even when the "
            "identity wording is misspelled, ambiguous, or absent from the live catalogue. q is "
            "not a catch-all for preferences represented by another declared field. Resolve "
            "every explicitly supplied filter, range, and ranking preference in resolvedInputs; "
            "do not leave the planning phase to reinterpret it from raw wording. Preserve valid "
            "descriptive q wording with message provenance; the business operation owns matched, "
            "ambiguous, and unavailable identity outcomes. Do not mark the intent as missing "
            "required input merely because a valid identity description is not a known canonical "
            "value. A "
            "request to edit a field without supplying its replacement belongs in "
            "requestedInputChanges, with the exact customer-authored words as evidence. This "
            "includes natural phrasing and spelling mistakes; do not require a fixed command. "
            "Do not put a field in requestedInputChanges when its replacement value is already "
            "present in resolvedInputs. A "
            "displayed choice with inputField and value is an allowed scalar answer: return that "
            "value in resolvedInputs for inputField, never in resolvedReferences. A "
            "request to see actual availability after a failed preference may answer an open "
            "schedule question by changing its preference mode instead of inventing a new date. "
            "intentStructure describes only the relationship among intentKinds; it never describes "
            "entity or reference ambiguity. Set it to single_outcome for one requested outcome "
            "even when that outcome has several possible entity references; compound_outcomes only "
            "when the customer explicitly requests multiple independent outcomes; and "
            "uncertain_intent only when several intentKinds are possible interpretations. An operation such as a "
            "booking that happens to concern an entity is still one outcome, not a compound intent. "
            "For compound outcomes, resolve each referenced entity against the same displayed "
            "context and customer-supplied descriptors; do not drop a reference merely because it "
            "belongs to only one of the requested outcomes. "
            "Reference completeness is independent of tool arguments and intent selection. Every "
            "entity that the customer refers to must appear in resolvedReferences or "
            "referenceCandidates, including pronouns, deictic references, and phrases that attach "
            "information, details, cost, availability, or offers to an entity. A read-only "
            "capability that does not accept an entity ID does not make the customer's reference "
            "optional: resolve it as conversational context. When such wording can denote several "
            "current trusted entities, preserve all plausible entities as referenceCandidates and "
            "set reference ambiguity instead of silently answering a more generic question. "
            "For every described entity reference, each customer-supplied identifying attribute "
            "must also appear as a provenance-backed resolvedInput and every candidate must satisfy "
            "it; never return all displayed entities when only some match. Select finite answers "
            "only from the open question. Pronouns and deictic wording such as 'this vehicle' do "
            "not themselves supply a make, model, location, service, or other descriptor. "
            "For reference ambiguity, include only candidates semantically compatible with every "
            "customer-supplied descriptor; exclude contradictory entities. If exactly one supplied "
            "candidate matches, put it in resolvedReferences without ambiguity. resolvedReferences "
            "contains only selected entities; referenceCandidates contains two or more plausible "
            "entities only when ambiguity is reference. When an open reference question has "
            "one candidate and the customer indicates that it is acceptable, answer that question "
            "and select the sole candidate. "
            "A resolved transactional goal is not required-input ambiguity merely because fields "
            "needed later in its workflow are missing; its workflow starts and collects them. Use "
            "required_input only when no operation can begin until the customer supplies a value. "
            "Select only supplied entity references and context IDs. Do not invent facts, identifiers, "
            "availability, or business outcomes. If meaning is genuinely ambiguous, report the "
            "ambiguity and low or medium confidence; do not guess. Return exactly one resolution "
            "function call and do not call a business tool."
        ),
        "input": [{"role": "user", "content": json.dumps(envelope, separators=(",", ":"))}],
        "tools": [
            resolution_tool_definition(
                references,
                context_ids,
                input_fields,
                customer_context_ids,
                allow_open_question_answer=not outcome_disambiguation,
            )
        ],
        "tool_choice": {"type": "function", "name": RESOLVE_TURN_TOOL},
        "parallel_tool_calls": False,
        "max_output_tokens": 700,
        "store": False,
    }


def resolution_tool_definition(
    trusted_references: list[str],
    trusted_context_ids: list[str],
    input_fields: list[str] | set[str] | dict[str, dict[str, Any]],
    customer_context_ids: list[str],
    *,
    allow_open_question_answer: bool = True,
) -> dict[str, Any]:
    open_question_id = next(
        (value for value in trusted_context_ids if value.startswith("question-")),
        None,
    )
    dialogue_acts = list(get_args(TurnUnderstanding.model_fields["dialogueAct"].annotation))
    goal_relations = list(get_args(TurnUnderstanding.model_fields["goalRelation"].annotation))
    if open_question_id is None or not allow_open_question_answer:
        dialogue_acts.remove("answer_open_question")
        goal_relations.remove("open_question")
    reference_items: dict[str, Any] = {"type": "string"}
    if trusted_references:
        reference_items["enum"] = trusted_references
    context_items: dict[str, Any] = {"type": "string"}
    if trusted_context_ids:
        context_items["enum"] = trusted_context_ids
    input_contracts = (
        input_fields
        if isinstance(input_fields, dict)
        else {field: _generic_resolution_value_schema() for field in sorted(input_fields)}
    )
    resolved_input_items = {
        "anyOf": [
            _resolved_input_schema(
                field,
                schema,
                customer_context_ids,
            )
            for field, schema in sorted(input_contracts.items())
        ]
    }
    if not input_contracts:
        resolved_input_items = _resolved_input_schema(
            "unavailable",
            _generic_resolution_value_schema(),
            customer_context_ids,
        )
    return {
        "type": "function",
        "name": RESOLVE_TURN_TOOL,
        "description": "Return the typed conversational meaning of the latest customer turn.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "schemaVersion",
                "dialogueAct",
                "goalRelation",
                "intentStructure",
                "intentKinds",
                "resultPresentation",
                "answeredQuestionId",
                "resolvedReferences",
                "referenceCandidates",
                "resolvedInputs",
                "requestedInputChanges",
                "ambiguity",
                "confidence",
                "supportingContextIds",
            ],
            "properties": {
                "schemaVersion": {"type": "integer", "enum": [1]},
                "dialogueAct": {
                    "type": "string",
                    "enum": dialogue_acts,
                },
                "goalRelation": {
                    "type": "string",
                    "enum": goal_relations,
                },
                "intentStructure": {
                    "type": "string",
                    "enum": [
                        "single_outcome",
                        "compound_outcomes",
                        "uncertain_intent",
                    ],
                },
                "intentKinds": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(get_args(IntentKind))},
                    "maxItems": 4,
                },
                "resultPresentation": {
                    "type": "string",
                    "enum": ["default", "explicit_request"],
                },
                "answeredQuestionId": {
                    "type": ["string", "null"],
                    "enum": (
                        [None, open_question_id]
                        if open_question_id and allow_open_question_answer
                        else [None]
                    ),
                },
                "resolvedReferences": {
                    "type": "array",
                    "items": reference_items,
                    "maxItems": 12,
                },
                "referenceCandidates": {
                    "type": "array",
                    "items": reference_items,
                    "maxItems": 12,
                },
                "resolvedInputs": {
                    "type": "array",
                    "maxItems": 24,
                    "items": resolved_input_items,
                },
                "requestedInputChanges": {
                    "type": "array",
                    "maxItems": 8,
                    "items": _requested_input_change_schema(
                        sorted(input_contracts), customer_context_ids
                    ),
                },
                "ambiguity": {
                    "type": "string",
                    "enum": ["none", "intent", "reference", "required_input"],
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                },
                "supportingContextIds": {
                    "type": "array",
                    "items": context_items,
                    "maxItems": 12,
                },
            },
        },
        "strict": True,
    }


def _resolved_input_schema(
    field: str,
    value_schema: dict[str, Any],
    customer_context_ids: list[str],
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["field", "value", "sourceContextId", "sourceText"],
        "properties": {
            "field": {"type": "string", "enum": [field]},
            "value": value_schema,
            "sourceContextId": {
                "type": "string",
                "enum": customer_context_ids or ["message:none"],
            },
            "sourceText": {"type": "string", "minLength": 1, "maxLength": 500},
        },
    }


def _requested_input_change_schema(
    fields: list[str], customer_context_ids: list[str]
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["field", "sourceContextId", "sourceText"],
        "properties": {
            "field": {"type": "string", "enum": fields or ["unavailable"]},
            "sourceContextId": {
                "type": "string",
                "enum": customer_context_ids or ["message:none"],
            },
            "sourceText": {"type": "string", "minLength": 1, "maxLength": 500},
        },
    }


def _generic_resolution_value_schema() -> dict[str, Any]:
    return {
        "anyOf": [
            {"type": "string"},
            {"type": "integer"},
            {"type": "number"},
            {"type": "boolean"},
            {"type": "array", "items": {"type": "string"}},
            {"type": "array", "items": {"type": "integer"}},
        ]
    }


def validate_turn_understanding(
    arguments: dict[str, Any],
    context: PlanningContext,
    input_fields: set[str],
    *,
    trusted_supporting_context_ids: set[str] | None = None,
) -> TurnUnderstanding:
    understanding = TurnUnderstanding.model_validate(
        _normalise_resolution_arguments(arguments, context)
    )
    trusted_references = trusted_context_references(context)
    referenced = set(understanding.resolvedReferences).union(understanding.referenceCandidates)
    if not referenced.issubset(trusted_references):
        raise ValueError("turn resolution contains an untrusted entity reference")
    trusted_ids = trusted_context_ids(context) | (trusted_supporting_context_ids or set())
    if not set(understanding.supportingContextIds).issubset(trusted_ids):
        raise ValueError("turn resolution cites unknown context evidence")
    selected_knowledge = {
        item for item in understanding.supportingContextIds if item.startswith("knowledge:")
    }
    if selected_knowledge and (
        understanding.intentKinds != ["business_information"]
        or understanding.intentStructure != "single_outcome"
        or understanding.ambiguity != "none"
    ):
        raise ValueError("knowledge evidence can only resolve one factual information outcome")
    if len(selected_knowledge) > 3:
        raise ValueError("a factual answer can cite at most three knowledge records")
    evidence = {
        str(item.get("contextId")): str(item.get("text") or "")
        for item in context.context_evidence
        if isinstance(item, dict) and item.get("role") == "user" and item.get("contextId")
    }
    for item in understanding.resolvedInputs:
        if item.field not in input_fields:
            raise ValueError("turn resolution contains an unknown input field")
        if item.field in REFERENCE_FIELD_NAMESPACES:
            raise ValueError("entity identifiers must be resolved as trusted references")
        source = evidence.get(item.sourceContextId)
        if source is None or _normalise(item.sourceText) not in _normalise(source):
            raise ValueError("turn resolution input lacks customer-authored evidence")
    resolved_fields = {item.field for item in understanding.resolvedInputs}
    for item in understanding.requestedInputChanges:
        if item.field not in input_fields:
            raise ValueError("turn resolution contains an unknown requested input change")
        source = evidence.get(item.sourceContextId)
        if source is None or _normalise(item.sourceText) not in _normalise(source):
            raise ValueError("requested input change lacks customer-authored evidence")
        if item.field in resolved_fields:
            raise ValueError("a supplied replacement cannot also be only a requested change")
    _validate_reference_descriptors(understanding, context)
    pending = context.pending_interaction or {}
    expected_question = str(pending.get("question_id") or "")
    if understanding.answeredQuestionId and understanding.answeredQuestionId != expected_question:
        raise ValueError("turn resolution answered a question that is not open")
    if understanding.dialogueAct == "answer_open_question" and not expected_question:
        raise ValueError("turn resolution answered a question when none is open")
    if understanding.dialogueAct == "answer_open_question":
        candidates = set(pending.get("candidate_references") or ())
        answered_references = set(understanding.resolvedReferences).union(
            understanding.referenceCandidates
        )
        if candidates and not answered_references.issubset(candidates):
            raise ValueError("turn resolution used an entity outside the open question")
        if (
            candidates
            and understanding.ambiguity == "none"
            and len(set(understanding.resolvedReferences)) != 1
        ):
            raise ValueError("an unambiguous choice answer must resolve exactly one candidate")
        expected_fields = set(pending.get("expected_fields") or ())
        resolved_fields = {item.field for item in understanding.resolvedInputs}
        if (
            not candidates
            and expected_fields
            and understanding.ambiguity == "none"
            and not expected_fields.intersection(resolved_fields)
        ):
            raise ValueError("an open input answer must resolve an expected field")
        goal = str(pending.get("goal_intent") or "")
        if goal and goal not in understanding.intentKinds:
            raise ValueError("turn resolution does not continue the open question's goal")
    return understanding


def _normalise_resolution_arguments(
    arguments: dict[str, Any], context: PlanningContext
) -> dict[str, Any]:
    """Repair representation-only resolver mistakes before semantic validation.

    This never invents an intent or input. It removes duplicates, combines repeated
    customer-authored vehicle-filter values from the same evidence record, and reconciles
    references and metadata mechanically implied by trusted entity attributes and the resulting
    ambiguity cardinality. References already owned by the active workflow may also be removed when
    the resolver merely reaffirms that workflow without supplying any requested input.
    """

    if not isinstance(arguments, dict):
        return arguments
    value = dict(arguments)
    for field in (
        "intentKinds",
        "resolvedReferences",
        "referenceCandidates",
        "supportingContextIds",
    ):
        items = value.get(field)
        if isinstance(items, list):
            value[field] = list(dict.fromkeys(items))

    knowledge_can_answer = (
        value.get("intentKinds") == ["business_information"]
        and value.get("intentStructure") == "single_outcome"
        and value.get("ambiguity") == "none"
    )
    if not knowledge_can_answer:
        value["supportingContextIds"] = [
            item
            for item in value.get("supportingContextIds") or []
            if not str(item).startswith("knowledge:")
        ]

    requested_changes = []
    requested_change_fields = set()
    for item in value.get("requestedInputChanges") or []:
        if not isinstance(item, dict) or not isinstance(item.get("field"), str):
            requested_changes.append(item)
            continue
        if item["field"] in requested_change_fields:
            continue
        requested_change_fields.add(item["field"])
        requested_changes.append(dict(item))
    value["requestedInputChanges"] = requested_changes

    evidence = {
        str(item.get("contextId")): str(item.get("text") or "")
        for item in context.context_evidence
        if isinstance(item, dict) and item.get("role") == "user" and item.get("contextId")
    }
    inputs: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for item in value.get("resolvedInputs") or []:
        if not isinstance(item, dict) or not isinstance(item.get("field"), str):
            inputs.append(item)
            continue
        field = str(item["field"])
        previous_index = positions.get(field)
        if previous_index is None:
            positions[field] = len(inputs)
            inputs.append(dict(item))
            continue
        previous = inputs[previous_index]
        if previous == item or previous.get("value") == item.get("value"):
            continue
        if field not in _MULTI_VALUE_VEHICLE_FIELDS or previous.get("sourceContextId") != item.get(
            "sourceContextId"
        ):
            inputs.append(dict(item))
            continue
        combined = _same_type_unique_values(previous.get("value"), item.get("value"))
        if combined is None:
            inputs.append(dict(item))
            continue
        merged = dict(previous)
        merged["value"] = combined
        source_id = str(merged.get("sourceContextId") or "")
        if evidence.get(source_id):
            merged["sourceText"] = evidence[source_id]
        inputs[previous_index] = merged
    value["resolvedInputs"] = inputs

    _reconcile_finite_choice_answer(value, context, inputs, evidence)
    inputs = [item for item in value.get("resolvedInputs") or [] if isinstance(item, dict)]
    _reconcile_descriptor_references(value, context, inputs)

    pending = context.pending_interaction or {}
    expected_fields = set(pending.get("expected_fields") or ())
    resolved_fields = {
        str(item.get("field")) for item in inputs if isinstance(item, dict) and item.get("field")
    }
    candidates = list(pending.get("candidate_references") or ())
    goal = str(pending.get("goal_intent") or "")
    intents = value.get("intentKinds") if isinstance(value.get("intentKinds"), list) else []
    resolved_references = set(value.get("resolvedReferences") or ())
    workflow_entities = dict((context.workflow_state or {}).get("entities") or {})
    active_entity_references = {
        f"{namespace}:{workflow_entities[field]}"
        for field, namespace in REFERENCE_FIELD_NAMESPACES.items()
        if workflow_entities.get(field)
    }
    if (
        value.get("dialogueAct") == "answer_open_question"
        and not candidates
        and expected_fields
        and not expected_fields.intersection(resolved_fields)
        and resolved_references.issubset(active_entity_references)
        and value.get("ambiguity") in {"none", "required_input"}
        and goal
        and goal in intents
    ):
        # This typed output reaffirms the active goal but supplies none of the pending fields.
        # Preserve it as a continuation instead of exhausting retries on a representation error.
        value["dialogueAct"] = "continue_goal"
        value["goalRelation"] = "active"
        value["answeredQuestionId"] = None
        value["resolvedReferences"] = []
        value["ambiguity"] = "none"

    workflow_entry_acts = {
        "start_goal",
        "continue_goal",
        "modify_goal",
        "switch_goal",
        "resume_goal",
    }
    if (
        value.get("ambiguity") == "required_input"
        and value.get("dialogueAct") in workflow_entry_acts
        and intents
        and all(intent in CapabilityRegistry.SPECS for intent in intents)
    ):
        # A resolved transactional goal is itself executable: its capability owns collection of
        # every missing public or protected field. Treating those fields as semantic ambiguity
        # prevents the workflow from starting and leaves the planner with no legal operation.
        # Open-question answers remain excluded because their pending field still owns the turn.
        value["ambiguity"] = "none"

    ambiguity = value.get("ambiguity")
    if ambiguity != "none" and value.get("confidence") == "high":
        value["confidence"] = "medium"
    if ambiguity == "intent" and len(intents) >= 2:
        value["intentStructure"] = "uncertain_intent"
    elif value.get("intentStructure") == "compound_outcomes" and len(intents) < 2:
        value["intentStructure"] = "single_outcome"
    elif value.get("intentStructure") == "single_outcome" and len(intents) > 1:
        value["intentStructure"] = "compound_outcomes"
    return value


def _reconcile_descriptor_references(
    value: dict[str, Any],
    context: PlanningContext,
    inputs: list[dict[str, Any]],
) -> None:
    """Compile entity selections from trusted attributes before schema validation.

    Resolver retries cannot reliably repair the representation where one trusted reference is both
    selected and marked ambiguous, or where a make/model descriptor is paired with a contradictory
    displayed entity. The application can resolve that representation independently for every
    referenced namespace, including in a compound turn, without interpreting prose: descriptors
    were extracted from customer-authored evidence and candidate records are trusted display
    context. List-valued descriptors are left to strict validation because they can intentionally
    identify several entities in the same namespace.
    """

    resolved = [
        str(reference)
        for reference in value.get("resolvedReferences") or ()
        if isinstance(reference, str)
    ]
    candidates = [
        str(reference)
        for reference in value.get("referenceCandidates") or ()
        if isinstance(reference, str)
    ]
    supplied = list(dict.fromkeys([*resolved, *candidates]))
    namespaces = list(
        dict.fromkeys(reference.partition(":")[0] for reference in supplied if ":" in reference)
    )
    if not supplied or not namespaces:
        return

    # Repair the reference partition itself before applying entity metadata. One reference cannot
    # be both selected and unresolved. This is safe for compound turns because references selected
    # for other namespaces remain untouched.
    partition_repaired = False
    if value.get("ambiguity") == "reference" and len(candidates) >= 2:
        resolved = [reference for reference in resolved if reference not in set(candidates)]
        partition_repaired = True
    elif value.get("ambiguity") == "none" and len(supplied) == 1:
        resolved = list(supplied)
        candidates = []
        partition_repaired = True

    all_descriptors = {
        str(item["field"]): item.get("value")
        for item in inputs
        if isinstance(item, dict) and isinstance(item.get("field"), str)
    }
    if not all_descriptors:
        if partition_repaired:
            value["resolvedReferences"] = list(dict.fromkeys(resolved))
            value["referenceCandidates"] = list(dict.fromkeys(candidates))
        return
    records = _trusted_reference_records(context)
    repaired_resolved = list(resolved)
    repaired_candidates = list(candidates)
    repaired = partition_repaired
    for namespace in namespaces:
        aliases = _REFERENCE_DESCRIPTOR_ALIASES.get(namespace, {})
        descriptors = {
            field: expected for field, expected in all_descriptors.items() if field in aliases
        }
        if not descriptors or any(isinstance(expected, list) for expected in descriptors.values()):
            continue
        matching = [
            reference
            for reference, record in records.items()
            if reference.startswith(f"{namespace}:")
            and _record_matches_descriptors(record, descriptors, aliases)
        ]
        if not matching:
            # The customer can deliberately name an entity outside the displayed set. Their
            # extracted descriptor remains valid evidence; the contradictory display reference
            # is merely a bad selection and must not invalidate the entire genuine request.
            if any(reference.startswith(f"{namespace}:") for reference in supplied):
                repaired = True
                repaired_resolved = [
                    reference
                    for reference in repaired_resolved
                    if not reference.startswith(f"{namespace}:")
                ]
                repaired_candidates = [
                    reference
                    for reference in repaired_candidates
                    if not reference.startswith(f"{namespace}:")
                ]
            continue
        repaired = True
        repaired_resolved = [
            reference
            for reference in repaired_resolved
            if not reference.startswith(f"{namespace}:")
        ]
        repaired_candidates = [
            reference
            for reference in repaired_candidates
            if not reference.startswith(f"{namespace}:")
        ]
        if len(matching) == 1:
            repaired_resolved.extend(matching)
        else:
            repaired_candidates.extend(matching)

    if not repaired:
        return
    value["resolvedReferences"] = list(dict.fromkeys(repaired_resolved))
    value["referenceCandidates"] = list(dict.fromkeys(repaired_candidates))
    if len(value["referenceCandidates"]) >= 2:
        value["ambiguity"] = "reference"
        value["confidence"] = "medium"
    elif not value["referenceCandidates"]:
        value["ambiguity"] = "none"


def _reconcile_finite_choice_answer(
    value: dict[str, Any],
    context: PlanningContext,
    inputs: list[dict[str, Any]],
    evidence: dict[str, str],
) -> None:
    """Canonicalize a short answer against the application-owned finite answer set.

    The model still owns free-form intent interpretation. A finite question is different: its
    possible answers and target field are already trusted application state. A unique exact,
    explicit ordinal, conservative one-token typo match, or unique match from AI-resolved
    customer-evidenced descriptors can therefore be compiled without a phrase table even if the
    model omitted the application reference or mislabeled that reply as a new goal. Descriptors
    that match zero or several candidates do not answer the question, so interruptions, starts,
    switches, ambiguous descriptions, and resumes remain model-owned.
    """

    pending = context.pending_interaction or {}
    question_id = str(pending.get("question_id") or "")
    if not question_id:
        _reconcile_visible_ordinal_reference(value, context, inputs)
        return
    latest = _normalise(context.latest_customer_message)
    if not latest:
        return
    selected_position = _finite_choice_position(latest)
    if selected_position is not None:
        matches = [
            choice
            for choice in context.displayed_choices
            if isinstance(choice, dict) and choice.get("position") == selected_position
        ]
    else:
        matches = [
            choice
            for choice in context.displayed_choices
            if isinstance(choice, dict)
            and isinstance(choice.get("label"), str)
            and _finite_choice_matches(latest, _normalise(str(choice["label"])))
        ]
    candidate_references = set(pending.get("candidate_references") or ())
    if len(matches) != 1 and candidate_references:
        matches = _finite_choice_descriptor_matches(
            candidate_references,
            context,
            inputs,
            evidence,
        )
    if len(matches) != 1:
        return
    choice = matches[0]
    reference = str(choice.get("entityReference") or "")
    goal = str(pending.get("goal_intent") or "")
    source_id = next(
        (
            context_id
            for context_id, source_text in evidence.items()
            if _normalise(context.latest_customer_message) in _normalise(source_text)
        ),
        None,
    )

    if candidate_references:
        if reference not in candidate_references:
            return
        # This closed one-token/ordinal grammar identifies exactly one application-owned answer.
        # Any additional field interpretation attached to the same tiny utterance is necessarily
        # redundant or speculative (for example treating `Bolton` as a callback reason). The
        # selected trusted record carries its real metadata and later workflow questions own their
        # own fields, so do not let a provider's extra extraction poison this finite answer.
        inputs.clear()
        value["resolvedInputs"] = inputs
        value["requestedInputChanges"] = []
        value["resolvedReferences"] = [reference]
        value["referenceCandidates"] = []
    else:
        field = str(choice.get("inputField") or "")
        expected_fields = set(pending.get("expected_fields") or ())
        if not field or field not in expected_fields or source_id is None:
            return
        # A unique scalar answer likewise owns the whole closed utterance. Preserve only the value
        # declared by the current choice contract, not unrelated provider guesses.
        inputs = []
        inputs.append(
            {
                "field": field,
                "value": choice.get("value"),
                "sourceContextId": source_id,
                "sourceText": context.latest_customer_message,
            }
        )
        value["resolvedInputs"] = inputs
        value["requestedInputChanges"] = []

    value["dialogueAct"] = "answer_open_question"
    value["goalRelation"] = "open_question"
    value["answeredQuestionId"] = question_id
    value["intentKinds"] = [goal] if goal else list(value.get("intentKinds") or ())
    value["intentStructure"] = "single_outcome"
    value["ambiguity"] = "none"
    value["confidence"] = "high"


def _reconcile_visible_ordinal_reference(
    value: dict[str, Any],
    context: PlanningContext,
    inputs: list[dict[str, Any]],
) -> None:
    """Bind an explicit ordinal to the latest trusted visible surface without choosing its goal."""

    selected_position = _finite_choice_position(_normalise(context.latest_customer_message))
    if selected_position is None:
        return
    matches = [
        choice
        for choice in context.displayed_choices
        if isinstance(choice, dict) and choice.get("position") == selected_position
    ]
    if len(matches) != 1:
        return
    reference = str(matches[0].get("entityReference") or "")
    namespace, separator, _identifier = reference.partition(":")
    if not separator:
        return

    def outside_namespace(candidate: object) -> bool:
        return not isinstance(candidate, str) or not candidate.startswith(f"{namespace}:")

    resolved = [
        candidate
        for candidate in value.get("resolvedReferences") or []
        if outside_namespace(candidate)
    ]
    resolved.append(reference)
    candidates = [
        candidate
        for candidate in value.get("referenceCandidates") or []
        if outside_namespace(candidate)
    ]
    identity_fields = {
        field
        for field, field_namespace in REFERENCE_FIELD_NAMESPACES.items()
        if field_namespace == namespace
    }
    identity_fields.update(f"{field}s" for field in tuple(identity_fields))
    inputs[:] = [item for item in inputs if item.get("field") not in identity_fields]
    requested_changes = [
        item
        for item in value.get("requestedInputChanges") or []
        if not isinstance(item, dict) or item.get("field") not in identity_fields
    ]
    value["resolvedInputs"] = inputs
    value["requestedInputChanges"] = requested_changes
    value["resolvedReferences"] = list(dict.fromkeys(resolved))
    value["referenceCandidates"] = list(dict.fromkeys(candidates))
    if value.get("ambiguity") == "reference" and not candidates:
        value["ambiguity"] = "none"
        value["confidence"] = "high"


def _finite_choice_descriptor_matches(
    candidate_references: set[str],
    context: PlanningContext,
    inputs: list[dict[str, Any]],
    evidence: dict[str, str],
) -> list[dict[str, str]]:
    """Resolve one open candidate from AI semantics and trusted candidate metadata.

    The application does not interpret free-form customer text here. It accepts only semantic
    descriptors already extracted by the resolver, proves each descriptor came from customer
    evidence, and intersects those descriptors with the exact persisted finite set. Returning a
    match only at cardinality one keeps larger pages and compound descriptions safe without making
    an entity-, phrase-, workflow-, or option-count-specific parser.
    """

    namespaces = {
        reference.partition(":")[0] for reference in candidate_references if ":" in reference
    }
    if len(namespaces) != 1:
        return []
    namespace = next(iter(namespaces))
    aliases = _REFERENCE_DESCRIPTOR_ALIASES.get(namespace, {})
    descriptors: dict[str, Any] = {}
    for item in inputs:
        field = item.get("field")
        if not isinstance(field, str) or field not in aliases:
            continue
        source_id = item.get("sourceContextId")
        source_text = item.get("sourceText")
        source = evidence.get(str(source_id))
        if (
            source is None
            or not isinstance(source_text, str)
            or not _normalise(source_text)
            or _normalise(source_text) not in _normalise(source)
        ):
            return []
        value = item.get("value")
        if isinstance(value, list):
            return []
        descriptors[field] = value
    if not descriptors:
        return []

    records = _trusted_reference_records(context)
    matching = [
        reference
        for reference in candidate_references
        if (record := records.get(reference)) is not None
        and _record_matches_descriptors(record, descriptors, aliases)
    ]
    if len(matching) != 1:
        return []
    return [{"entityReference": matching[0]}]


def requires_explicit_outcome_disambiguation(
    understanding: TurnUnderstanding,
    context: PlanningContext,
) -> bool:
    """Identify a contested natural-language consumption of a finite choice.

    Closed labels, ordinals, and conservative one-token typo matches are application-owned answer
    protocols. A longer natural sentence remains AI-owned language: when it is initially resolved
    as an answer, a separate outcome pass must establish whether the displayed entity is actually
    being selected or is only the subject of an information request. The trigger is entirely
    structural and applies to every reference namespace and workflow.
    """

    pending = context.pending_interaction or {}
    active_workflow = str(context.workflow_state.get("activeWorkflow") or "")
    goal = str(pending.get("goal_intent") or "")
    if (
        understanding.dialogueAct != "answer_open_question"
        or pending.get("kind") != "reference_choice"
        or not pending.get("question_id")
        or not CapabilityRegistry.is_transactional_workflow(active_workflow)
        or CapabilityRegistry.goal_intent_for_active_workflow(active_workflow) != goal
    ):
        return False
    candidates = set(pending.get("candidate_references") or ())
    choices = [
        choice
        for choice in context.displayed_choices
        if isinstance(choice, dict)
        and str(choice.get("entityReference") or "") in candidates
    ]
    latest = _normalise(context.latest_customer_message)
    position = _finite_choice_position(latest)
    if position is not None:
        matches = [choice for choice in choices if choice.get("position") == position]
        return len(matches) != 1
    matches = [
        choice
        for choice in choices
        if isinstance(choice.get("label"), str)
        and _finite_choice_matches(latest, _normalise(str(choice["label"])))
    ]
    return len(matches) != 1


def _finite_choice_matches(customer_text: str, label: str) -> bool:
    if customer_text == label:
        return True
    customer_tokens = customer_text.split()
    label_tokens = label.split()
    if len(customer_tokens) != 1:
        return False
    token = customer_tokens[0]
    return any(
        len(candidate) >= 4 and SequenceMatcher(None, token, candidate).ratio() >= 0.78
        for candidate in label_tokens
    )


def _finite_choice_position(customer_text: str) -> int | None:
    """Return one explicitly requested UI option position, without interpreting the goal.

    Position is application-owned metadata on every finite choice.  This parser recognizes only
    the small, closed ordinal grammar that refers to that metadata; it does not classify business
    intent, entity descriptions, or arbitrary numbers in free-form text.
    """

    explicit = re.search(
        r"\b(?:option|choice|number|no)\s*(?:number\s*)?#?"
        r"(?P<value>\d{1,2}(?:st|nd|rd|th)?|" + "|".join(_FINITE_CHOICE_NUMBER_WORDS) + r")\b",
        customer_text,
    )
    if explicit is None:
        explicit = re.search(
            r"(?:^|\bthe\s+)(?P<value>first|second|third|fourth|fifth|sixth|seventh|"
            r"eighth|ninth|tenth|eleventh|twelfth)(?:\s+(?:one|option|choice))?\b$",
            customer_text,
        )
    if explicit is None:
        explicit = re.fullmatch(r"#(?P<value>\d{1,2})", customer_text)
    if explicit is None:
        return None
    value = explicit.group("value")
    if value in _FINITE_CHOICE_NUMBER_WORDS:
        return _FINITE_CHOICE_NUMBER_WORDS[value]
    return int(re.sub(r"(?:st|nd|rd|th)$", "", value))


def _record_matches_descriptors(
    record: dict[str, Any],
    descriptors: dict[str, Any],
    aliases: dict[str, tuple[str, ...]],
) -> bool:
    compared = False
    for field, expected in descriptors.items():
        actual = next(
            (record[name] for name in aliases[field] if record.get(name) is not None),
            None,
        )
        if actual is None:
            continue
        compared = True
        if not _descriptor_value_matches(field, actual, expected):
            return False
    return compared


def _descriptor_value_matches(field: str, actual: Any, expected: Any) -> bool:
    """Match trusted entity metadata against exact or numeric-range semantic descriptors."""

    if field.startswith(("min", "max")):
        try:
            actual_number = int(actual)
            expected_number = int(expected)
        except (TypeError, ValueError):
            return False
        return (
            actual_number >= expected_number
            if field.startswith("min")
            else actual_number <= expected_number
        )
    expected_values = expected if isinstance(expected, list) else [expected]
    actual_normalized = _normalise(str(actual))
    if field in _CATEGORICAL_REFERENCE_FIELDS:
        actual_tokens = set(actual_normalized.split())
        return any(
            bool(expected_tokens := set(_normalise(str(value)).split()))
            and expected_tokens.issubset(actual_tokens)
            for value in expected_values
        )
    return actual_normalized in {_normalise(str(value)) for value in expected_values}


def _same_type_unique_values(first: Any, second: Any) -> list[str] | list[int] | None:
    values = [
        *(first if isinstance(first, list) else [first]),
        *(second if isinstance(second, list) else [second]),
    ]
    if all(isinstance(item, str) for item in values):
        return list(dict.fromkeys(values))
    if all(isinstance(item, int) and not isinstance(item, bool) for item in values):
        return list(dict.fromkeys(values))
    return None


def intent_resolution_catalogue(catalogue: UnifiedToolCatalog) -> list[dict[str, Any]]:
    """Build the semantic intent ontology from executable capability metadata.

    The resolver receives capability meanings, boundaries, and examples maintained by the same
    catalogue used for retrieval and execution. There is no separate utterance-routing table.
    """

    definitions = catalogue.planner_tools()
    return [
        {
            "intentKind": intent,
            "capabilities": [
                {
                    "title": definition.title,
                    "purpose": definition.description,
                    "examples": list(definition.retrieval_examples),
                }
                for definition in definitions
                if intent in supported_intents(definition.id)
            ],
        }
        for intent in get_args(IntentKind)
    ]


def input_resolution_catalogue(catalogue: UnifiedToolCatalog) -> list[dict[str, Any]]:
    """Describe semantic inputs from the same executable schemas used by planning."""

    fields: dict[str, dict[str, Any]] = {}
    for definition in catalogue.definitions():
        properties = definition.provider_input_schema.get("properties") or {}
        for name, schema in properties.items():
            if name == "intentKind" or name in REFERENCE_FIELD_NAMESPACES:
                continue
            entry = fields.setdefault(
                str(name),
                {
                    "field": str(name),
                    "descriptions": [],
                    "allowedValues": [],
                    "valueSchemas": [],
                },
            )
            description = str(schema.get("description") or "").strip()
            if description and description not in entry["descriptions"]:
                entry["descriptions"].append(description)
            for variant in [schema, *(schema.get("anyOf") or [])]:
                for value in variant.get("enum") or []:
                    if value is not None and value not in entry["allowedValues"]:
                        entry["allowedValues"].append(value)
            value_schema = _semantic_value_schema(schema)
            if value_schema not in entry["valueSchemas"]:
                entry["valueSchemas"].append(value_schema)
    result = []
    for name in sorted(fields):
        entry = fields[name]
        schemas = entry.pop("valueSchemas")
        entry["valueSchema"] = schemas[0] if len(schemas) == 1 else {"anyOf": schemas}
        result.append(entry)
    return result


def input_resolution_contracts(catalogue: UnifiedToolCatalog) -> dict[str, dict[str, Any]]:
    return {
        str(entry["field"]): dict(entry["valueSchema"])
        for entry in input_resolution_catalogue(catalogue)
    }


def _semantic_value_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Project a provider argument schema to the non-null customer-authored value."""

    projected = deepcopy(schema)
    for metadata in ("default", "description", "title"):
        projected.pop(metadata, None)
    if isinstance(projected.get("anyOf"), list):
        choices = [
            _semantic_value_schema(choice)
            for choice in projected["anyOf"]
            if not (isinstance(choice, dict) and choice.get("type") == "null")
        ]
        return choices[0] if len(choices) == 1 else {"anyOf": choices}
    return projected


def trusted_context_references(context: PlanningContext) -> set[str]:
    # A conversational result set supersedes the broader host page as the current reference
    # scope. The page is a fallback when the conversation has not displayed vehicle choices.
    vehicle_scope = context.displayed_vehicles or context.page_vehicles
    references = {
        f"vehicle:{item['vehicleId']}"
        for item in vehicle_scope
        if isinstance(item, dict) and isinstance(item.get("vehicleId"), str)
    }
    references.update(
        f"offer:{item['offerId']}"
        for item in context.displayed_offers
        if isinstance(item, dict) and isinstance(item.get("offerId"), str)
    )
    references.update(
        f"dealership:{item['dealershipId']}"
        for item in context.displayed_dealerships
        if isinstance(item, dict) and isinstance(item.get("dealershipId"), str)
    )
    references.update(
        str(item["entityReference"])
        for item in context.displayed_choices
        if isinstance(item, dict)
        and isinstance(item.get("entityReference"), str)
        and not str(item["entityReference"]).startswith("workflow_option:")
    )
    entities = dict(context.workflow_state.get("entities") or {})
    namespaces = {
        "vehicleId": "vehicle",
        "offerId": "offer",
        "dealershipId": "dealership",
        "serviceTypeId": "service",
        "slotId": "appointment",
    }
    references.update(
        f"{namespace}:{entities[field]}"
        for field, namespace in namespaces.items()
        if entities.get(field)
    )
    pending = context.pending_interaction or {}
    references.update(str(value) for value in pending.get("candidate_references") or ())
    return references


def _validate_reference_descriptors(
    understanding: TurnUnderstanding,
    context: PlanningContext,
) -> None:
    """Reject entity candidates that contradict AI-extracted customer descriptors.

    This validates structured fields against structured entity metadata. It never interprets the
    customer's words; extracting their meaning remains the resolver model's responsibility.
    """

    descriptors = {item.field: item.value for item in understanding.resolvedInputs}
    references = [
        *understanding.resolvedReferences,
        *understanding.referenceCandidates,
    ]
    if not descriptors or not references:
        return
    records = _trusted_reference_records(context)
    for reference in references:
        record = records.get(reference)
        if record is None:
            continue
        namespace = reference.partition(":")[0]
        for field, aliases in _REFERENCE_DESCRIPTOR_ALIASES.get(namespace, {}).items():
            if field not in descriptors:
                continue
            actual = next(
                (record[name] for name in aliases if record.get(name) is not None),
                None,
            )
            if actual is None:
                continue
            if not _descriptor_value_matches(field, actual, descriptors[field]):
                raise ValueError("resolved entity contradicts a customer-supplied descriptor")


def _trusted_reference_records(context: PlanningContext) -> dict[str, dict[str, Any]]:
    vehicle_scope = context.displayed_vehicles or context.page_vehicles
    records = {
        f"vehicle:{item['vehicleId']}": item
        for item in vehicle_scope
        if isinstance(item, dict) and isinstance(item.get("vehicleId"), str)
    }
    records.update(
        {
            f"offer:{item['offerId']}": item
            for item in context.displayed_offers
            if isinstance(item, dict) and isinstance(item.get("offerId"), str)
        }
    )
    records.update(
        {
            f"dealership:{item['dealershipId']}": item
            for item in context.displayed_dealerships
            if isinstance(item, dict) and isinstance(item.get("dealershipId"), str)
        }
    )
    for item in context.displayed_choices:
        if not isinstance(item, dict) or not isinstance(item.get("entityReference"), str):
            continue
        reference = str(item["entityReference"])
        # Choice projection supplies position and customer-facing labels, while the underlying
        # displayed record supplies the entity descriptors used for safe semantic resolution.
        # They are two views of one trusted entity, so projection metadata must enrich rather than
        # replace the record and erase make/model, offer, dealership, or appointment attributes.
        records[reference] = {**records.get(reference, {}), **item}
    return records


def trusted_context_ids(context: PlanningContext) -> set[str]:
    identifiers = {
        str(item.get("contextId"))
        for item in context.context_evidence
        if isinstance(item, dict) and item.get("contextId")
    }
    pending = context.pending_interaction or {}
    if pending.get("question_id"):
        identifiers.add(str(pending["question_id"]))
    return identifiers


def trusted_customer_context_ids(context: PlanningContext) -> set[str]:
    return {
        str(item.get("contextId"))
        for item in context.context_evidence
        if isinstance(item, dict)
        and item.get("role") == "user"
        and str(item.get("contextId") or "").startswith("message:")
    }


def _normalise(value: str) -> str:
    return " ".join(str(value).casefold().split())
