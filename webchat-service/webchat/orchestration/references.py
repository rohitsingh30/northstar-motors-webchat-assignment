"""Extract trusted entity references from closed server and host-page payloads."""

from __future__ import annotations

import json
import re

PAGE_VEHICLE_REFERENCE_FIELDS = (
    "position",
    "vehicleId",
    "label",
    "year",
    "make",
    "model",
    "variant",
    "bodyStyle",
    "colour",
    "pricePence",
    "mileage",
    "fuelType",
    "transmission",
    "availability",
    "dealershipTown",
)


def page_vehicle_identities(
    vehicles: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Project bounded candidate attributes used only to resolve a page vehicle ID."""
    return [
        {
            key: vehicle[key]
            for key in PAGE_VEHICLE_REFERENCE_FIELDS
            if vehicle.get(key) not in (None, "")
        }
        for vehicle in vehicles
    ]


def current_vehicle_reference_context(messages) -> list[dict[str, object]]:
    """Return exact, ordered vehicle cards from the latest assistant result."""
    for message in reversed(messages):
        if message.role != "assistant" or message.view_type != "vehicle_list":
            continue
        try:
            payload = json.loads(message.view_payload_json or "{}")
        except (TypeError, ValueError):
            return []
        items = payload.get("items", []) if isinstance(payload, dict) else []
        candidates: list[dict[str, object]] = []
        for position, item in enumerate(items, start=1):
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            candidates.append(
                {
                    "position": position,
                    "vehicleId": item["id"],
                    **{
                        field: item.get(field)
                        for field in (
                            "year",
                            "make",
                            "model",
                            "variant",
                            "bodyStyle",
                            "pricePence",
                            "mileage",
                            "fuelType",
                            "transmission",
                            "colour",
                            "availability",
                            "dealershipTown",
                        )
                    },
                }
            )
        return candidates
    return []


def page_vehicle_reference_context(context: dict) -> list[dict[str, object]]:
    """Extract bounded vehicle candidates supplied by the host-page contract."""
    entities = context.get("entities", []) if isinstance(context, dict) else []
    candidates: list[dict[str, object]] = []
    active_vehicle_id = context.get("vehicleId") if isinstance(context, dict) else None
    if isinstance(active_vehicle_id, str) and re.fullmatch(r"veh-[0-9]{3}", active_vehicle_id):
        candidates.append(
            {
                "position": 1,
                "vehicleId": active_vehicle_id,
                "label": context.get("title"),
            }
        )
    for position, entity in enumerate(entities, start=1):
        if not isinstance(entity, dict) or entity.get("type") != "vehicle":
            continue
        vehicle_id = entity.get("id")
        if not isinstance(vehicle_id, str) or not re.fullmatch(r"veh-[0-9]{3}", vehicle_id):
            continue
        if any(item["vehicleId"] == vehicle_id for item in candidates):
            continue
        attributes = entity.get("attributes")
        attributes = attributes if isinstance(attributes, dict) else {}
        candidates.append(
            {
                "position": position,
                "vehicleId": vehicle_id,
                "label": entity.get("label"),
                **{
                    field: attributes.get(field)
                    for field in (
                        "year",
                        "make",
                        "model",
                        "variant",
                        "pricePence",
                        "mileage",
                        "fuelType",
                        "transmission",
                        "bodyStyle",
                        "colour",
                        "availability",
                        "dealershipTown",
                    )
                },
            }
        )
    return candidates


def current_vehicle_search_state(messages) -> dict[str, object] | None:
    """Return server-authored pagination state from the latest vehicle view."""
    for message in reversed(messages):
        if message.role != "assistant" or message.view_type != "vehicle_list":
            continue
        try:
            payload = json.loads(message.view_payload_json or "{}")
        except (TypeError, ValueError):
            return None
        search = payload.get("search") if isinstance(payload, dict) else None
        if not isinstance(search, dict) or not isinstance(search.get("filters"), dict):
            return None
        page = search.get("page")
        if not isinstance(page, int) or page < 1:
            return None
        return {"filters": dict(search["filters"]), "page": page}
    return None


def current_offer_reference_context(messages) -> list[dict[str, object]]:
    """Return ordered offers from the latest server-authored offer view."""
    for message in reversed(messages):
        if message.role != "assistant" or message.view_type != "offer_list":
            continue
        try:
            payload = json.loads(message.view_payload_json or "{}")
        except (TypeError, ValueError):
            return []
        items = payload.get("items", []) if isinstance(payload, dict) else []
        candidates: list[dict[str, object]] = []
        for position, item in enumerate(items, start=1):
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            candidates.append(
                {
                    "position": position,
                    "offerId": item["id"],
                    **{
                        field: item.get(field)
                        for field in (
                            "title",
                            "make",
                            "model",
                            "productType",
                            "monthlyPricePence",
                            "expiresOn",
                        )
                    },
                }
            )
        return candidates
    return []


def current_dealership_reference_context(messages) -> list[dict[str, object]]:
    """Return ordered dealerships from the latest server-authored dealership view."""
    supported_views = {"dealership_list", "opening_hours", "workshop_location_list"}
    for message in reversed(messages):
        if message.role != "assistant" or message.view_type not in supported_views:
            continue
        try:
            payload = json.loads(message.view_payload_json or "{}")
        except (TypeError, ValueError):
            return []
        items = payload.get("items", []) if isinstance(payload, dict) else []
        candidates: list[dict[str, object]] = []
        for position, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            dealership_id = item.get("id")
            town = item.get("town")
            if not isinstance(dealership_id, str) and not isinstance(town, str):
                continue
            candidates.append(
                {
                    "position": position,
                    "dealershipId": dealership_id,
                    "name": item.get("name"),
                    "town": town,
                    "postcode": item.get("postcode"),
                }
            )
        return candidates
    return []
