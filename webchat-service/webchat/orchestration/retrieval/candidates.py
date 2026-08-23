from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from webchat.orchestration.catalogue import ToolDefinition, UnifiedToolCatalog

from .knowledge import KnowledgeEntry, KnowledgeIndex


@dataclass(frozen=True)
class CandidateSet:
    tools: tuple[ToolDefinition, ...]
    knowledge: tuple[KnowledgeEntry, ...]


class CandidateRetriever(Protocol):
    def retrieve(
        self, query: str, *, tool_limit: int = 8, knowledge_limit: int = 5
    ) -> CandidateSet: ...


class FullCatalogRetriever:
    """Deterministic test/fallback retriever with no keyword routing decisions."""

    def __init__(self, catalogue: UnifiedToolCatalog, knowledge: KnowledgeIndex | None = None):
        self.catalogue = catalogue
        self.knowledge = knowledge or KnowledgeIndex()

    def retrieve(
        self, query: str, *, tool_limit: int = 8, knowledge_limit: int = 5
    ) -> CandidateSet:
        del query
        return CandidateSet(
            tuple(self.catalogue.planner_tools()[:tool_limit]),
            tuple(self.knowledge.entries[:knowledge_limit]),
        )


class FastEmbedCandidateRetriever:
    """Local semantic retrieval over the unified catalogue and curated knowledge."""

    MODEL = "BAAI/bge-small-en-v1.5"

    def __init__(self, catalogue: UnifiedToolCatalog, knowledge: KnowledgeIndex | None = None):
        self.catalogue = catalogue
        self.knowledge = knowledge or KnowledgeIndex()
        self._model = None
        self._tool_vectors = None
        self._tool_example_vectors = None
        self._knowledge_vectors = None

    def retrieve(
        self, query: str, *, tool_limit: int = 8, knowledge_limit: int = 5
    ) -> CandidateSet:
        self._ensure_index()
        query_vector = next(iter(self._model.query_embed([query])))
        tools = self.catalogue.planner_tools()
        tool_scores = _rank_tools(query_vector, self._tool_vectors, self._tool_example_vectors)
        knowledge_scores = _rank(query_vector, self._knowledge_vectors)
        return CandidateSet(
            tuple(tools[index] for index in tool_scores[:tool_limit]),
            tuple(self.knowledge.entries[index] for index in knowledge_scores[:knowledge_limit]),
        )

    def _ensure_index(self) -> None:
        if self._model is not None:
            return
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=self.MODEL)
        tools = self.catalogue.planner_tools()
        self._tool_vectors = list(
            self._model.passage_embed([f"{tool.title}. {tool.description}" for tool in tools])
        )
        examples = [
            (index, example)
            for index, tool in enumerate(tools)
            for example in tool.retrieval_examples
        ]
        example_vectors = (
            list(self._model.passage_embed([example for _, example in examples]))
            if examples
            else []
        )
        self._tool_example_vectors = [[] for _ in tools]
        for (tool_index, _), vector in zip(examples, example_vectors, strict=True):
            self._tool_example_vectors[tool_index].append(vector)
        self._knowledge_vectors = list(
            self._model.passage_embed([entry.searchable_text for entry in self.knowledge.entries])
        )


def _rank(query_vector, vectors) -> list[int]:
    return sorted(
        range(len(vectors)),
        key=lambda index: float(query_vector @ vectors[index]),
        reverse=True,
    )


def _rank_tools(query_vector, vectors, example_vectors) -> list[int]:
    return sorted(
        range(len(vectors)),
        key=lambda index: max(
            float(query_vector @ vector) for vector in (vectors[index], *example_vectors[index])
        ),
        reverse=True,
    )
