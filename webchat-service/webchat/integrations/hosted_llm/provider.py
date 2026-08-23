"""Hosted Responses transport coordinating proposal, review, and repair requests."""

from __future__ import annotations

import json
import logging
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.planning.prompt import REVIEWER_SYSTEM_POLICY, planner_instructions
from webchat.orchestration.planning.review import (
    TURN_REVIEW_TOOLS,
    apply_review,
    proposal_view,
    review_payload,
    turn_review_definitions,
)
from webchat.orchestration.retrieval import FullCatalogRetriever
from webchat.orchestration.retrieval.candidates import CandidateRetriever, CandidateSet

from ..contracts import (
    ProposalReview,
    ProviderReply,
    ReviewerContext,
    ReviewUnavailableError,
    SemanticMessages,
    TurnProposal,
)
from .candidates import _retrieve_candidates
from .protocol import (
    _customer_knowledge,
    _function_arguments,
    _interaction_decision_tool_definitions,
    _knowledge_answer_tool_definition,
    _parse_proposal,
    _provider_reply,
    _social_response_tool_definition,
    response_input,
)
from .review import (
    _review_repair_tools,
    _reviewer_context,
    _reviewer_context_view,
    _safe_review_feedback,
)

logger = logging.getLogger(__name__)

PLANNER_ATTEMPTS = 2
REVIEW_ATTEMPTS = 3

class HostedLlmProvider:
    """Provider-neutral Responses adapter with independent proposal review."""

    requires_review = True

    def __init__(
        self,
        provider_url: str,
        api_key: str,
        model: str,
        catalogue: UnifiedToolCatalog,
        retriever: CandidateRetriever | None = None,
        client: httpx.AsyncClient | None = None,
        request_timeout_seconds: float = 44.0,
    ):
        self.model = _required_value("model", model)
        self._api_key = _required_value("API key", api_key)
        self.catalogue = catalogue
        self.retriever = retriever or FullCatalogRetriever(catalogue)
        if request_timeout_seconds <= 0:
            raise ValueError("request timeout must be positive")
        self._client = client or httpx.AsyncClient(
            base_url=_provider_base_url(provider_url),
            timeout=httpx.Timeout(request_timeout_seconds, connect=min(5, request_timeout_seconds)),
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def generate_turn(self, messages: SemanticMessages) -> ProviderReply:
        turn_started = perf_counter()
        context = _reviewer_context(messages)
        retrieval_started = perf_counter()
        candidates = _retrieve_candidates(self.retriever, context)
        retrieval_ms = _elapsed_ms(retrieval_started)
        planner_started = perf_counter()
        candidate = await self._plan_with_recovery(messages, candidates, context)
        planner_ms = _elapsed_ms(planner_started)
        review_started = perf_counter()
        try:
            reviewed, review = await self._review(candidate, context, candidates)
        except (httpx.HTTPError, TypeError, ValueError, KeyError) as error:
            logger.warning("hosted proposal review failed closed", exc_info=True)
            raise ReviewUnavailableError("hosted proposal review unavailable") from error
        logger.info(
            "hosted turn proposal reviewed",
            extra={
                "context": {
                    "retrieval_ms": retrieval_ms,
                    "planner_ms": planner_ms,
                    "reviewer_ms": _elapsed_ms(review_started),
                    "total_ms": _elapsed_ms(turn_started),
                    "review_outcome": review.outcome,
                }
            },
        )
        return _provider_reply(reviewed, review, candidates)

    async def _plan_with_recovery(
        self,
        messages: SemanticMessages,
        candidates: CandidateSet,
        context: ReviewerContext,
    ) -> TurnProposal:
        last_error: Exception | None = None
        for attempt in range(1, PLANNER_ATTEMPTS + 1):
            request_started = perf_counter()
            try:
                response = await self._client.post(
                    "/responses",
                    headers=self._headers(),
                    json=self._planner_request_payload(messages, candidates, context),
                )
                response.raise_for_status()
                return _parse_proposal(
                    response.json(), candidates, self.catalogue, context.pending_interaction
                )
            except httpx.TimeoutException as error:
                logger.warning(
                    "hosted planner request timed out",
                    extra={
                        "context": {
                            "stage": "planner",
                            "attempt": attempt,
                            "duration_ms": _elapsed_ms(request_started),
                            "candidate_tools": [
                                definition.id for definition in candidates.tools
                            ],
                            "candidate_knowledge_count": len(candidates.knowledge),
                        }
                    },
                )
                raise TimeoutError("hosted planner request timed out") from error
            except (httpx.HTTPError, TypeError, ValueError, KeyError) as error:
                last_error = error
                retryable = _retryable_planner_error(error)
                logger.warning(
                    "hosted planner response failed validation",
                    extra={
                        "context": {
                            "attempt": attempt,
                            "retrying": retryable and attempt < PLANNER_ATTEMPTS,
                            "error_type": type(error).__name__,
                        }
                    },
                )
                if not retryable or attempt == PLANNER_ATTEMPTS:
                    raise
        assert last_error is not None
        raise last_error

    async def _review(
        self,
        candidate: TurnProposal,
        context: ReviewerContext,
        candidates: CandidateSet,
    ) -> tuple[TurnProposal, ProposalReview]:
        last_error: Exception | None = None
        validation_feedback: str | None = None
        allowed_review_tools = TURN_REVIEW_TOOLS
        customer_evidence = {
            entry.id: entry.text for entry in candidates.knowledge if entry.audience == "customer"
        }
        for attempt in range(1, REVIEW_ATTEMPTS + 1):
            selected_review_tool: str | None = None
            try:
                response = await self._client.post(
                    "/responses",
                    headers=self._headers(),
                    json=self._reviewer_request_payload(
                        candidate,
                        context,
                        candidates,
                        validation_feedback,
                        allowed_review_tools,
                    ),
                )
                response.raise_for_status()
                decisions = _function_arguments(
                    response.json(), expected_names=allowed_review_tools
                )
                if len(decisions) != 1:
                    raise ValueError("reviewer must return exactly one decision")
                selected_review_tool = decisions[0][1]
                return apply_review(
                    review_payload(decisions[0][1], decisions[0][2]),
                    candidate,
                    self.catalogue,
                    customer_evidence,
                    has_trusted_tool_facts=context.trusted_tool_facts is not None,
                    pending_interaction=context.pending_interaction,
                )
            except (httpx.HTTPError, TypeError, ValueError, KeyError) as error:
                last_error = error
                validation_feedback = _safe_review_feedback(error)
                allowed_review_tools = _review_repair_tools(error)
                logger.warning(
                    "hosted review decision failed validation",
                    extra={
                        "context": {
                            "validation_feedback": validation_feedback,
                            "attempt": attempt,
                            "review_function": selected_review_tool,
                            "candidate_tools": [call.name for call in candidate.tool_calls],
                            "repair_functions": sorted(allowed_review_tools),
                        }
                    },
                )
        assert last_error is not None
        raise last_error

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _planner_request_payload(
        self,
        messages: list[dict[str, Any]],
        candidates: CandidateSet,
        context: ReviewerContext,
    ) -> dict[str, Any]:
        customer_knowledge = _customer_knowledge(candidates)
        return {
            "model": self.model,
            "instructions": planner_instructions(candidates),
            "input": response_input(messages),
            "tools": [
                *(definition.provider_tool() for definition in candidates.tools),
                *(
                    [_knowledge_answer_tool_definition(customer_knowledge)]
                    if customer_knowledge
                    else []
                ),
                _social_response_tool_definition(),
                *(_interaction_decision_tool_definitions(context.pending_interaction)),
            ],
            "tool_choice": "required",
            "parallel_tool_calls": True,
            "max_output_tokens": 1_200,
            "store": False,
        }

    def _reviewer_request_payload(
        self,
        candidate: TurnProposal,
        context: ReviewerContext,
        candidates: CandidateSet,
        validation_feedback: str | None = None,
        allowed_review_tools: frozenset[str] = TURN_REVIEW_TOOLS,
    ) -> dict[str, Any]:
        envelope = {
            "latestCustomerMessage": context.latest_customer_message,
            "context": _reviewer_context_view(context),
            "candidateProposal": proposal_view(candidate),
            "retrievedEvidence": [
                {
                    "id": item.id,
                    "title": item.title,
                    "text": item.text,
                    "source": item.source,
                    "audience": item.audience,
                }
                for item in candidates.knowledge
            ],
            "executableToolCatalogue": [
                definition.reviewer_view() for definition in self.catalogue.planner_tools()
            ],
        }
        if validation_feedback:
            envelope["deterministicValidationFeedback"] = validation_feedback
        return {
            "model": self.model,
            "instructions": REVIEWER_SYSTEM_POLICY,
            "input": [{"role": "user", "content": json.dumps(envelope, separators=(",", ":"))}],
            "tools": [
                definition
                for definition in turn_review_definitions()
                if definition["name"] in allowed_review_tools
            ],
            "tool_choice": "required",
            "parallel_tool_calls": False,
            "max_output_tokens": 1_400,
            "store": False,
        }

def _required_value(label: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized


def _elapsed_ms(started: float) -> int:
    return round((perf_counter() - started) * 1_000)


def _retryable_planner_error(error: Exception) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in {408, 409, 425, 429} or (
            error.response.status_code >= 500
        )
    return isinstance(error, (httpx.TransportError, TypeError, ValueError, KeyError))


def _provider_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("LLM_PROVIDER_URL must be an absolute HTTP(S) URL")
    if parsed.query or parsed.fragment:
        raise ValueError("LLM_PROVIDER_URL must not contain a query or fragment")
    path = f"{parsed.path.rstrip('/')}/"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
