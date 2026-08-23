from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any


def price_limit(text: str) -> int | None:
    match = re.search(r"(?:£|under |below |less than |up to )\s*([0-9][0-9,]*)", text)
    return int(match.group(1).replace(",", "")) * 100 if match else None


def vehicle_query(text: str) -> str | None:
    """Extract stock terms without embedding makes or models from the catalogue."""
    patterns = (
        r"\bonly\s+(?:show\s+me\s+)?([a-z0-9][a-z0-9 -]{0,60})",
        r"\btest[- ]drive(?:\s+(?:of|for))?\s+(?:an?\s+)?([a-z0-9][a-z0-9 -]{0,60})",
        r"\b(?:find|show me|looking for)\s+(?:an?\s+|some\s+)?([a-z0-9][a-z0-9 -]{0,60})",
        r"\b(?:is|are|do you have|have you got)\s+(?:the\s+)?(?:an?\s+)?([a-z0-9][a-z0-9 -]{0,60})\s+(?:available|reserved|sold)\b",
        r"\b([a-z0-9-]+)\s+(?:cars|vehicles)\b",
    )
    candidate = next(
        (
            match.group(1)
            for pattern in patterns
            if (match := re.search(pattern, text, re.IGNORECASE))
        ),
        None,
    )
    if not candidate:
        return None
    candidate = re.split(
        r"\b(?:below|under|less than|up to|with|near|at|on|from|in|available|reserved|sold)\b",
        candidate,
        maxsplit=1,
    )[0]
    candidate = re.sub(r"\b(?:cars?|vehicles?)\b", "", candidate).strip(" .,!?")
    words = candidate.split()
    if not words or re.fullmatch(r"20[0-9]{2}(?:\s+or\s+newer)?", candidate, re.IGNORECASE):
        return None
    meaningful_words = {word.lower() for word in words} - {"a", "an", "the"}
    if meaningful_words.issubset(
        {"available", "cheapest", "expensive", "latest", "newest", "reserved", "sold"}
    ):
        return None
    if (
        len(words) == 1
        and words[0].lower() != "this"
        and words[0].lower().endswith("s")
        and not words[0].lower().endswith("ss")
    ):
        return words[0][:-1]
    return candidate


def location_query(text: str) -> str | None:
    patterns = (
        r"\b(?:in|at|near)\s+([a-z][a-z -]{1,60})",
        r"\bwhere\s+(?:is|are)\s+(?:the\s+)?([a-z][a-z -]{1,60}?)\s+dealership\b",
        r"\b(?:phone|email|contact|details?)\s+(?:number\s+)?(?:and\s+email\s+)?for\s+([a-z][a-z -]{1,60}?)\s+(?:sales|service|parts|dealership)\b",
        r"^\s*([a-z][a-z -]{1,60}?)\s+(?:sales|service|parts)\s+(?:desk\s+)?(?:opening|hours)\b",
        r"^\s*(?!(?:are|can|do|does|find|is|list|our|show|the|what|where|which|your)\b)([a-z][a-z -]{1,60}?)\s+dealership\b",
    )
    match = next(
        (
            candidate
            for pattern in patterns
            if (candidate := re.search(pattern, text, re.IGNORECASE))
        ),
        None,
    )
    if match is None:
        return None
    location = re.split(
        r"\b(?:below|under|less than|up to|with|near|at|on|from)\b",
        match.group(1),
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" .,?!")
    location = re.split(
        r"\s+(?:for|about)\s+(?:it|that|this|them)\b",
        location,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" .,?!")
    return location or None


def vehicle_filters(text: str) -> dict[str, Any]:
    filters: dict[str, Any] = {"sort": "priceAsc"}
    if (limit := price_limit(text)) is not None:
        filters["maxPricePence"] = limit
    if location := location_query(text):
        filters["dealershipTown"] = location
    if query := vehicle_query(text):
        filters["q"] = query.lower()
    if mileage := re.search(r"(?:under|below|less than|up to)\s*([0-9,]+)\s*miles", text):
        filters["maxMileage"] = int(mileage.group(1).replace(",", ""))
    if year := re.search(r"(?:from|since|newer than)\s*(20[0-9]{2})", text):
        filters["minYear"] = int(year.group(1))
    if newer := re.search(r"\b(20[0-9]{2})\s*or\s*newer\b", text, re.IGNORECASE):
        filters["minYear"] = int(newer.group(1))
    normalized = text.lower()
    if re.search(r"\bavailable\b", normalized):
        filters["availability"] = "available"
    elif re.search(r"\breserved\b", normalized):
        filters["availability"] = "reserved"
    elif re.search(r"\bsold\b", normalized):
        filters["availability"] = "sold"
    if "newest" in normalized or "latest" in normalized:
        filters["sort"] = "newest"
    elif "lowest mileage" in normalized or "low mileage" in normalized:
        filters["sort"] = "mileageAsc"
    elif "highest price" in normalized or "most expensive" in normalized:
        filters["sort"] = "priceDesc"
    _apply_vehicle_dimensions(filters, text)
    _remove_query_terms_expressed_by_dimensions(filters)
    return filters


def _apply_vehicle_dimensions(filters: dict[str, Any], text: str) -> None:
    dimensions = {
        "fuelType": ("petrol", "diesel", "electric", "hybrid"),
        "transmission": ("automatic", "manual"),
        "bodyStyle": ("suv", "hatchback", "saloon", "estate"),
    }
    for field, values in dimensions.items():
        match = next(
            (value for value in values if re.search(rf"\b{value}(?:s|es)?\b", text, re.IGNORECASE)),
            None,
        )
        if match:
            filters[field] = match.upper() if match == "suv" else match.title()


def _remove_query_terms_expressed_by_dimensions(filters: dict[str, Any]) -> None:
    query = str(filters.get("q") or "").casefold()
    if not query:
        return
    for field in ("fuelType", "transmission", "bodyStyle"):
        value = str(filters.get(field) or "").casefold()
        if value:
            query = re.sub(rf"\b{re.escape(value)}(?:s|es)?\b", " ", query)
    generic = {
        "a",
        "an",
        "car",
        "cars",
        "find",
        "me",
        "only",
        "show",
        "some",
        "vehicle",
        "vehicles",
    }
    meaningful = [word for word in re.findall(r"[a-z0-9]+", query) if word not in generic]
    if meaningful:
        filters["q"] = " ".join(meaningful)
    else:
        filters.pop("q", None)


def workshop_filters(text: str) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if location := location_query(text):
        filters["dealershipTown"] = location
    selected_service = re.search(
        r"\bservice:\s*(.+?)(?=\s+\b(?:in|at|near)\b|$)", text, re.IGNORECASE
    )
    service = selected_service.group(1).strip(" .?!") if selected_service else None
    if service:
        filters["serviceTypeName"] = service
    if "next week" in text.lower():
        today = datetime.now(UTC).date()
        monday = today + timedelta(days=7 - today.weekday())
        filters.update(
            dateFrom=monday.isoformat(),
            dateTo=(monday + timedelta(days=6)).isoformat(),
        )
    return filters


def contact_fields(text: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if email := re.search(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", text, re.IGNORECASE):
        fields["email"] = email.group(0)
    if phone := re.search(r"\b(?:\+44|0)[0-9 ]{9,14}\b", text):
        fields["phone"] = re.sub(r"\s+", "", phone.group(0))
    if name := re.search(
        r"(?:my name is|i am|i'm)\s+([a-z'-]+)\s+([a-z'-]+)",
        text,
        re.IGNORECASE,
    ):
        fields.update(firstName=name.group(1).title(), lastName=name.group(2).title())
    return fields


def stable_dealership_id(text: str) -> str | None:
    match = re.search(r"\bnorthstar-[a-z0-9]+(?:-[a-z0-9]+)*\b", text)
    return match.group(0) if match else None


def department(words: frozenset[str]) -> str | None:
    return next(
        (value for value in ("sales", "service", "parts", "general") if value in words), None
    )


def opening_day(text: str) -> str | None:
    days = (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    )
    return next(
        (day.title() for day in days if re.search(rf"\b{day}\b", text, re.IGNORECASE)),
        None,
    )


def slot_id(text: str) -> str | None:
    match = re.search(r"\b(?:td|ws)-slot-[0-9]{4}\b", text)
    return match.group(0) if match else None


def comparison_queries(text: str) -> list[str]:
    match = re.search(r"compare\s+(?:the\s+)?(.+?)\s+and\s+(?:the\s+)?(.+?)(?:[?.!]|$)", text)
    return [part.strip() for part in match.groups() if part.strip()] if match else []
