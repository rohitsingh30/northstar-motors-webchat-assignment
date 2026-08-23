"""Resolve customer wording against the live workshop service catalogue."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

ServiceResolutionStatus = Literal["matched", "ambiguous", "unsupported"]


@dataclass(frozen=True)
class ServiceResolution:
    status: ServiceResolutionStatus
    query: str
    service: dict[str, Any] | None = None
    candidates: tuple[dict[str, Any], ...] = ()


def resolve_live_service(items: list[dict[str, Any]], text: str) -> ServiceResolution:
    """Return an explicit resolution outcome using only the current catalogue."""

    normalized_text = _normalized(text)
    target_tokens = _tokens(text)
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        normalized_name = _normalized(name)
        exact = bool(normalized_name and f" {normalized_name} " in f" {normalized_text} ")
        name_overlap = target_tokens & _tokens(name)
        description_overlap = target_tokens & _tokens(str(item.get("description") or ""))
        score = (100 if exact else 0) + len(name_overlap) * 10 + len(description_overlap) * 3
        if score:
            ranked.append((score, len(normalized_name), item))
    if not ranked:
        return ServiceResolution("unsupported", text)
    ranked.sort(key=lambda candidate: (candidate[0], candidate[1]), reverse=True)
    best_rank = ranked[0][:2]
    best = tuple(item for score, length, item in ranked if (score, length) == best_rank)
    if len(best) > 1:
        return ServiceResolution("ambiguous", text, candidates=best)
    return ServiceResolution("matched", text, service=ranked[0][2])


def _tokens(value: str) -> set[str]:
    return {_singular(token) for token in re.findall(r"[a-z0-9]+", value.lower())}


def _normalized(value: str) -> str:
    return " ".join(_singular(token) for token in re.findall(r"[a-z0-9]+", value.lower()))


def _singular(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token
