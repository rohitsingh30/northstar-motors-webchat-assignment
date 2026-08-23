"""Application-owned conformance rules for semantically valid provider plans."""

from __future__ import annotations

import re
from typing import Any

_VEHICLE_TARGET = (
    r"(?:cars?|vehicles?|stock|inventory|suvs?|hatchbacks?|saloons?|estates?)"
)
_DISCOVERY_ACTION = r"(?:find|show|search|browse|list|see|recommend|suggest)"
_RANKING = (
    r"(?:available|cheapest|lowest|least|fewest|newest|latest|oldest|"
    r"most expensive|highest)"
)
_RESULT_REFERENCE = (
    r"(?:these|those|them|results?|ones?|current page|this page|shown|displayed)"
)

# These fields express an actual stock constraint or identity. Pagination,
# context flags, result limits, and a default sort do not prove that the customer
# asked to search inventory.
_SUBSTANTIVE_SEARCH_FIELDS = frozenset(
    {
        "query",
        "q",
        "make",
        "model",
        "fuelType",
        "transmission",
        "bodyStyle",
        "dealershipId",
        "town",
        "dealershipTown",
        "minPricePence",
        "maxPricePence",
        "maxMileage",
        "minYear",
        "preferenceDimension",
        "preferenceValue",
    }
)

_CONCRETE_SEARCH_CONSTRAINT_FIELDS = _SUBSTANTIVE_SEARCH_FIELDS - {
    "preferenceDimension",
    "preferenceValue",
}


def vehicle_plan_has_concrete_constraints(arguments: dict[str, Any]) -> bool:
    """Return whether typed arguments already define an executable stock filter."""

    return any(
        arguments.get(field) not in (None, "")
        for field in _CONCRETE_SEARCH_CONSTRAINT_FIELDS
    )


def vehicle_discovery_has_evidence(
    user_text: str,
    arguments: dict[str, Any],
) -> bool:
    """Return whether a vehicle discovery plan is grounded in the current turn.

    A provider can emit a schema-valid ``vehicle.search`` for an unrelated
    operational question simply because it contains the noun "car". Requiring
    either a substantive typed constraint or explicit discovery language keeps
    that routing error from executing the stock tool. The rule describes the
    discovery capability; it does not enumerate unsupported customer questions.
    """

    if any(
        arguments.get(field) is not None for field in _SUBSTANTIVE_SEARCH_FIELDS
    ):
        return True

    normalized = " ".join(user_text.casefold().split())
    explicit_discovery = bool(
        re.search(
            rf"\b{_DISCOVERY_ACTION}\b.*\b{_VEHICLE_TARGET}\b"
            rf"|\b{_VEHICLE_TARGET}\b.*\b{_DISCOVERY_ACTION}\b",
            normalized,
        )
    )
    ranked_or_filtered_set = bool(
        re.search(
            rf"\b{_RANKING}\b.*\b{_VEHICLE_TARGET}\b"
            rf"|\b{_VEHICLE_TARGET}\b.*\b{_RANKING}\b"
            rf"|\b{_VEHICLE_TARGET}\b.*\b(?:under|below|over|above|between|from)\b"
            r"\s*£?\s*[0-9]",
            normalized,
        )
    )
    scoped_result_request = bool(
        arguments.get("referenceScope")
        and re.search(rf"\b{_RESULT_REFERENCE}\b", normalized)
    )
    return explicit_discovery or ranked_or_filtered_set or scoped_result_request
