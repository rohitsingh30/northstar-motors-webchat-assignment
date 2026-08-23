from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any


@dataclass(frozen=True)
class KnowledgeEntry:
    id: str
    title: str
    text: str
    source: str
    audience: str = "customer"
    follow_up_action: dict[str, Any] | None = None

    @property
    def searchable_text(self) -> str:
        return f"{self.title}. {self.text}"


class KnowledgeIndex:
    """Curated answer evidence; operational data belongs in tools, not this index."""

    def __init__(self, entries: tuple[KnowledgeEntry, ...] | None = None):
        self.entries = entries or _load_entries()


def _load_entries() -> tuple[KnowledgeEntry, ...]:
    resource = files("webchat.orchestration.retrieval").joinpath("knowledge.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    return tuple(KnowledgeEntry(**item) for item in payload)
