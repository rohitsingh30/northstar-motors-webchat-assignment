"""Resolve customer wording against the live workshop service catalogue."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

ServiceResolutionStatus = Literal["matched", "ambiguous", "unavailable"]

MATCH_THRESHOLD = 0.40
AMBIGUITY_MARGIN = 0.12
ALIASES = {
    "tyres": "tyre",
    "tire": "tyre",
    "tires": "tyre",
    "fitted": "fitting",
    "fit": "fitting",
    "diagnosis": "diagnostic",
    "diagnose": "diagnostic",
    "maintenance": "service",
    "annual": "full",
}
STOP_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "car",
    "do",
    "for",
    "have",
    "how",
    "i",
    "is",
    "it",
    "long",
    "much",
    "my",
    "of",
    "service",
    "the",
    "to",
    "vehicle",
    "what",
    "would",
    "you",
}


@dataclass(frozen=True)
class ServiceResolution:
    status: ServiceResolutionStatus
    query: str
    service: dict[str, Any] | None = None
    candidates: tuple[dict[str, Any], ...] = ()


def resolve_live_service(items: list[dict[str, Any]], text: str) -> ServiceResolution:
    """Return an explicit resolution outcome using only the current catalogue."""

    normalized_text = _normalized(text)
    target_tokens = _tokens(text, keep_service=True)
    ranked: list[tuple[float, int, dict[str, Any]]] = []
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        normalized_name = _normalized(name)
        exact = bool(normalized_name and f" {normalized_name} " in f" {normalized_text} ")
        name_tokens = _tokens(name, keep_service=True)
        description_tokens = _tokens(str(item.get("description") or ""))
        name_overlap = target_tokens & name_tokens
        description_overlap = target_tokens & description_tokens
        coverage = len(name_overlap) / max(len(name_tokens), 1)
        evidence = len(name_overlap | description_overlap) / max(len(target_tokens), 1)
        score = 1.0 if exact else (coverage * 0.72) + (evidence * 0.28)
        if score >= MATCH_THRESHOLD:
            ranked.append((score, len(normalized_name), item))
    if not ranked:
        return ServiceResolution("unavailable", text)
    ranked.sort(key=lambda candidate: (candidate[0], candidate[1]), reverse=True)
    top_score = ranked[0][0]
    plausible = tuple(item for score, _length, item in ranked if top_score - score <= AMBIGUITY_MARGIN)
    if len(plausible) > 1:
        return ServiceResolution("ambiguous", text, candidates=plausible[:4])
    return ServiceResolution("matched", text, service=ranked[0][2])


def service_query_tokens(text: str) -> set[str]:
    """Expose the resolver's canonical vocabulary to isolated fake-provider tests."""

    return _tokens(text, keep_service=True)


def _tokens(value: str, *, keep_service: bool = False) -> set[str]:
    values = {_canonical(token) for token in re.findall(r"[a-z0-9]+", value.lower())}
    blocked = STOP_WORDS - ({"service"} if keep_service else set())
    return values - blocked


def _normalized(value: str) -> str:
    return " ".join(_canonical(token) for token in re.findall(r"[a-z0-9]+", value.lower()))


def _canonical(token: str) -> str:
    return ALIASES.get(token, _singular(token))


def _singular(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token
