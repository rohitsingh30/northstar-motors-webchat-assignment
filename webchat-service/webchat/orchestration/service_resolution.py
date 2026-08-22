from __future__ import annotations

import re
from typing import Any

_GENERIC_WORDS = {
    "a", "an", "appointment", "book", "booking", "can", "cost", "does", "do",
    "find", "for", "how", "i", "is", "it", "me", "much", "need", "please",
    "price", "schedule", "the", "times", "to", "want", "what", "workshop",
}


def match_live_service(items: list[dict[str, Any]], text: str) -> dict[str, Any] | None:
    """Resolve wording against current names/descriptions, without a local catalogue."""
    normalized_text = _normalized(text)
    target_tokens = _tokens(text) - _GENERIC_WORDS
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
        return None
    ranked.sort(key=lambda candidate: (candidate[0], candidate[1]), reverse=True)
    if len(ranked) > 1 and ranked[0][:2] == ranked[1][:2]:
        return None
    return ranked[0][2]


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
