"""Semantic candidate retrieval for the latest request and bounded follow-up context."""

from __future__ import annotations

import json
from typing import Any

from webchat.integrations.contracts import PlanningContext
from webchat.orchestration.retrieval.candidates import CandidateRetriever, CandidateSet


def _retrieval_query(context: PlanningContext) -> str:
    """Combine the latest request with bounded conversational and operation context."""
    state = context.workflow_state
    parts = [f"Customer request: {context.latest_customer_message}"]
    if context.turn_understanding is not None:
        parts.append(
            "Validated conversational meaning: "
            + context.turn_understanding.model_dump_json(exclude_none=True)
        )
    if context.previous_turn:
        parts.append(
            "Immediately preceding exchange (context for follow-up references): "
            + json.dumps(context.previous_turn, separators=(",", ":"))
        )
    if context.pending_interaction:
        parts.append(
            "Pending application interaction: "
            + json.dumps(context.pending_interaction, separators=(",", ":"))
        )
    if state:
        parts.extend(
            (
                f"Active operation: {state.get('activeWorkflow', 'conversation')}",
                f"Operation stage: {state.get('stage', 'active')}",
                "Current operation state: "
                + json.dumps(
                    {
                        "entities": state.get("entities") or {},
                        "constraints": state.get("constraints") or {},
                        "lastTool": state.get("lastTool"),
                        "lastRenderer": state.get("lastRenderer"),
                    },
                    separators=(",", ":"),
                ),
            )
        )
    return "\n".join(parts)


def _retrieve_candidates(
    retriever: CandidateRetriever, context: PlanningContext
) -> CandidateSet:
    """Preserve latest-request recall while adding a separate contextual channel."""
    semantic_queries = list(dict.fromkeys([
        context.latest_customer_message,
        *(
            context.turn_understanding.intentKinds
            if context.turn_understanding is not None
            else []
        ),
    ]))
    semantic_results = [
        retriever.retrieve(query, tool_limit=8, knowledge_limit=5)
        for query in semantic_queries
    ]
    direct = CandidateSet(
        _unique_candidates(
            tuple(tool for result in semantic_results for tool in result.tools), key="id"
        ),
        _unique_candidates(
            tuple(item for result in semantic_results for item in result.knowledge), key="id"
        ),
    )
    if (
        not context.previous_turn
        and not context.workflow_state
        and not context.pending_interaction
    ):
        return direct
    contextual = retriever.retrieve(_retrieval_query(context))
    return CandidateSet(
        _unique_candidates((*direct.tools, *contextual.tools), key="id"),
        _unique_candidates((*direct.knowledge, *contextual.knowledge), key="id"),
    )

def _unique_candidates(items: tuple[Any, ...], *, key: str) -> tuple[Any, ...]:
    seen: set[str] = set()
    unique: list[Any] = []
    for item in items:
        identifier = str(getattr(item, key))
        if identifier in seen:
            continue
        seen.add(identifier)
        unique.append(item)
    return tuple(unique)
