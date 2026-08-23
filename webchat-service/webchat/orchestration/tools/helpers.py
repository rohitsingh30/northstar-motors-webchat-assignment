"""Shared helpers for validated tool handlers."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_MIN_LOCATION_SIMILARITY = 0.84
_MIN_LOCATION_MARGIN = 0.08


def normalized_location(value: object) -> str:
    """Normalize human-entered locations before exact or fuzzy town matching."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def dealership_in_town(dealerships: list[dict], town: str) -> dict | None:
    requested = normalized_location(town)
    if not requested:
        return None

    candidates = [
        (dealership, normalized_location(dealership.get("town")))
        for dealership in dealerships
        if normalized_location(dealership.get("town"))
    ]
    exact = next(
        (dealership for dealership, candidate in candidates if candidate == requested),
        None,
    )
    if exact is not None:
        return exact

    ranked = sorted(
        (
            (SequenceMatcher(None, requested, candidate).ratio(), dealership)
            for dealership, candidate in candidates
        ),
        key=lambda match: match[0],
        reverse=True,
    )
    if not ranked or ranked[0][0] < _MIN_LOCATION_SIMILARITY:
        return None
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < _MIN_LOCATION_MARGIN:
        return None
    return ranked[0][1]


def dealership_towns(dealerships: list[dict]) -> str:
    return ", ".join(
        str(dealership.get("town"))
        for dealership in dealerships
        if dealership.get("town")
    )
