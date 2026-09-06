"""Extract trusted entity references from closed server and host-page payloads."""

from __future__ import annotations

import json
import re

from webchat.orchestration.appointments import appointment_choice

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

_COLLECTION_NAMESPACES = {
    "service_list": "service",
    "trusted_slot_context": "appointment",
}

_DYNAMIC_CHOICE_NAMESPACES = frozenset(
    {"appointment", "dealership", "service", "vehicle", "workflow_option"}
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


def current_vehicle_reference_context(
    messages,
    *,
    retain_prior_pages: bool = False,
) -> list[dict[str, object]]:
    """Return current visible vehicles, optionally retaining pages for reference resolution.

    A completed operation receipt is a newer, singular discourse subject than an older catalogue.
    It therefore becomes the current vehicle reference scope until another vehicle result replaces
    it. Current-page actions such as comparison use only the latest visible result. Semantic
    reference resolution may retain bounded prior pages from the same search so a customer can
    refer to an earlier result without making those rows current again.
    """

    collected_pages: dict[int, list[dict[str, object]]] = {}
    active_signature: str | None = None
    paginated = False
    seen_pages: set[int] = set()
    message_list = list(messages)
    for reverse_index, message in enumerate(reversed(message_list)):
        if message.role == "assistant" and message.view_type == "receipt":
            try:
                receipt = json.loads(message.view_payload_json or "{}")
            except (TypeError, ValueError):
                continue
            vehicle_id = receipt.get("vehicleId") if isinstance(receipt, dict) else None
            if isinstance(vehicle_id, str) and re.fullmatch(r"veh-[0-9]{3}", vehicle_id):
                receipt_index = len(message_list) - reverse_index - 1
                identity = _preceding_vehicle_identity(message_list[:receipt_index], vehicle_id)
                return [
                    {
                        "position": 1,
                        "vehicleId": vehicle_id,
                        "label": receipt.get("vehicleLabel"),
                        **identity,
                    }
                ]
        if message.role != "assistant" or message.view_type not in {
            "vehicle_list",
            "vehicle_comparison",
            "grounded_presentation",
        }:
            continue
        try:
            payload = json.loads(message.view_payload_json or "{}")
        except (TypeError, ValueError):
            continue
        if message.view_type == "grounded_presentation":
            payload = _grounded_card_data(
                payload,
                "vehicle_preview",
                "vehicle_comparison",
            )
        if not isinstance(payload, dict):
            continue
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            continue
        search = payload.get("search")
        signature = None
        page = 1
        if isinstance(search, dict) and isinstance(search.get("filters"), dict):
            signature = json.dumps(search["filters"], sort_keys=True, separators=(",", ":"))
            page = int(search.get("page") or payload.get("page") or 1)
        if active_signature is None:
            active_signature = signature
            paginated = signature is not None
        elif not paginated or signature != active_signature:
            break
        if page in seen_pages:
            continue
        seen_pages.add(page)
        candidates: list[dict[str, object]] = []
        for position, item in enumerate(items, start=1):
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            candidates.append(
                {
                    "position": position,
                    **({"resultPage": page} if paginated else {}),
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
        collected_pages[page] = candidates
        collected_count = sum(len(items) for items in collected_pages.values())
        if not retain_prior_pages or not paginated or len(seen_pages) >= 4 or collected_count >= 12:
            break
    # Transcript discovery runs newest-first so that a different search terminates the scan, but
    # one paginated result set remains customer-ordered by page and then by row. Page-local
    # ordinals are provenance only; a later consolidated choice surface assigns fresh ordinals.
    return [item for page in sorted(collected_pages) for item in collected_pages[page]][:12]


def _preceding_vehicle_identity(messages, vehicle_id: str) -> dict[str, object]:
    """Recover public identity fields that produced a later trusted receipt."""

    for message in reversed(messages):
        if message.role != "assistant" or not message.view_type:
            continue
        try:
            payload = json.loads(message.view_payload_json or "{}")
        except (TypeError, ValueError):
            continue
        candidates: list[object] = []
        if isinstance(payload, dict):
            candidates.append(payload.get("vehicle"))
            candidates.extend(payload.get("items") or [])
            if message.view_type == "grounded_presentation":
                cards = payload.get("cards") or []
                for card in cards:
                    data = card.get("data") if isinstance(card, dict) else None
                    if isinstance(data, dict):
                        candidates.extend(data.get("items") or [])
                        candidates.append(data.get("vehicle"))
        matched = next(
            (
                candidate
                for candidate in candidates
                if isinstance(candidate, dict)
                and str(candidate.get("id") or candidate.get("vehicleId") or "") == vehicle_id
            ),
            None,
        )
        if matched is None:
            continue
        return {
            field: matched[field]
            for field in PAGE_VEHICLE_REFERENCE_FIELDS
            if field not in {"position", "vehicleId", "label"}
            and matched.get(field) not in (None, "")
        }
    return {}


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
        if message.role != "assistant" or message.view_type not in {
            "vehicle_list",
            "grounded_presentation",
        }:
            continue
        try:
            payload = json.loads(message.view_payload_json or "{}")
        except (TypeError, ValueError):
            return None
        if message.view_type == "grounded_presentation":
            payload = _grounded_card_data(payload, "vehicle_preview")
            if payload is None:
                continue
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
        if message.role != "assistant" or message.view_type not in {
            "offer_list",
            "grounded_presentation",
        }:
            continue
        try:
            payload = json.loads(message.view_payload_json or "{}")
        except (TypeError, ValueError):
            return []
        if message.view_type == "grounded_presentation":
            payload = _grounded_card_data(payload, "offer")
            if payload is None:
                continue
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
    supported_views = {
        "choice_list",
        "dealership_list",
        "opening_hours",
        "workshop_location_list",
        "grounded_presentation",
    }
    for message in reversed(messages):
        if message.role != "assistant" or message.view_type not in supported_views:
            continue
        try:
            payload = json.loads(message.view_payload_json or "{}")
        except (TypeError, ValueError):
            return []
        if (
            message.view_type == "choice_list"
            and isinstance(payload, dict)
            and payload.get("choiceEntityType") != "dealership"
        ):
            # ``choice_list`` is a transport-neutral finite-choice view.  Its item IDs belong
            # to the namespace declared by the producer; scalar workflow values such as Parts
            # must never become trusted dealership IDs merely because they share this view.
            continue
        if message.view_type == "grounded_presentation":
            payload = _grounded_card_data(payload, "dealership", "opening_hours")
            if payload is None:
                continue
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


def current_choice_reference_context(
    messages,
    *,
    choice_field: str | None = None,
) -> list[dict[str, object]]:
    """Return the latest ordered, server-authored conversational choice set.

    When a workflow recovery names a missing field, only a collection that explicitly owns that
    field is eligible. This prevents a later choice from another interrupted workflow being
    replayed beside the reconstructed question.
    """

    vehicle_views = {"vehicle_list", "vehicle_comparison", "grounded_presentation"}
    for message in reversed(messages):
        if message.role != "assistant" or not message.view_type:
            continue
        namespace = _COLLECTION_NAMESPACES.get(message.view_type)
        if (
            namespace is None
            and message.view_type != "choice_list"
            and message.view_type not in vehicle_views
        ):
            continue
        try:
            payload = json.loads(message.view_payload_json or "{}")
        except (TypeError, ValueError):
            return []
        if not isinstance(payload, dict):
            return []
        if message.view_type in vehicle_views:
            if choice_field not in {None, "vehicleId"}:
                continue
            if message.view_type == "grounded_presentation":
                payload = _grounded_card_data(
                    payload,
                    "vehicle_preview",
                    "vehicle_comparison",
                )
                if payload is None:
                    continue
            raw_items = payload.get("items")
            if not isinstance(raw_items, list) or not raw_items:
                continue
            choices = _vehicle_surface_choices(raw_items)
            if choices:
                return choices
            continue
        if choice_field is not None and payload.get("choiceField") != choice_field:
            continue
        if message.view_type == "choice_list":
            dynamic_namespace = payload.get("choiceEntityType")
            if dynamic_namespace not in _DYNAMIC_CHOICE_NAMESPACES:
                return []
            namespace = str(dynamic_namespace)
        if message.view_type == "trusted_slot_context":
            raw_items = payload.get("items")
            if not isinstance(raw_items, list):
                continue
            choices = [
                {
                    "position": position,
                    "entityReference": f"appointment:{item['id']}",
                    "label": str(
                        projected.get("displayLabel")
                        or projected.get("label")
                        or projected.get("slotLabel")
                        or projected.get("startsAt")
                    ),
                    "startsAt": projected.get("startsAt"),
                    "timeZone": projected.get("timeZone"),
                    "localStartsAt": projected.get("localStartsAt"),
                    "localDate": projected.get("localDate"),
                    "localTime": projected.get("localTime"),
                    "displayLabel": projected.get("displayLabel"),
                    "dealershipId": item.get("dealershipId"),
                    "dealershipName": item.get("dealershipName"),
                    "dealershipTown": item.get("dealershipTown"),
                    "serviceTypeId": item.get("serviceTypeId"),
                    "serviceTypeName": item.get("serviceTypeName") or item.get("serviceName"),
                    "vehicleId": item.get("vehicleId"),
                    "year": item.get("vehicleYear") or item.get("year"),
                    "make": item.get("make"),
                    "model": item.get("model"),
                    "variant": item.get("variant"),
                }
                for position, item in enumerate(raw_items[:12], start=1)
                if isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and item.get("startsAt")
                for projected in [appointment_choice(item)]
            ]
            if choices:
                return choices
            # An empty recovery view is an outcome, not a replacement choice set. Keep scanning
            # for the most recent non-empty application-owned choices.
            continue
        presentation = payload.get("collectionPresentation")
        if not isinstance(presentation, dict) or presentation.get("purpose") not in {
            "choice",
            "clarification",
        }:
            continue
        raw_items = payload.get("items")
        display_items = presentation.get("items")
        if not isinstance(raw_items, list) or not isinstance(display_items, list):
            return []
        choices: list[dict[str, object]] = []
        for ordinal, (raw, display) in enumerate(
            zip(raw_items, display_items, strict=False),
            start=1,
        ):
            if not isinstance(raw, dict) or not isinstance(display, dict):
                continue
            identifier = raw.get("id")
            label = display.get("label")
            if not isinstance(identifier, str) or not isinstance(label, str):
                continue
            raw_position = raw.get("position")
            position = (
                raw_position if isinstance(raw_position, int) and raw_position > 0 else ordinal
            )
            projected = appointment_choice(raw) if namespace == "appointment" else {}
            choices.append(
                {
                    "position": position,
                    "entityReference": f"{namespace}:{identifier}",
                    "label": label,
                    "description": display.get("description"),
                    **(
                        {
                            key: projected.get(key)
                            for key in (
                                "startsAt",
                                "timeZone",
                                "localStartsAt",
                                "localDate",
                                "localTime",
                                "displayLabel",
                                "dealershipId",
                                "dealershipName",
                                "dealershipTown",
                                "serviceTypeId",
                                "serviceTypeName",
                                "vehicleId",
                                "year",
                                "make",
                                "model",
                                "variant",
                            )
                            if projected.get(key) is not None
                        }
                        if namespace == "appointment"
                        else {}
                    ),
                    **(
                        {
                            "inputField": payload["choiceField"],
                            "value": identifier,
                        }
                        if namespace == "workflow_option" and payload.get("choiceField")
                        else {}
                    ),
                }
            )
        return choices
    return []


def _vehicle_surface_choices(items: list[object]) -> list[dict[str, object]]:
    """Project the latest visible vehicle options as the current ordinal scope."""

    choices: list[dict[str, object]] = []
    for ordinal, item in enumerate(items[:12], start=1):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        raw_position = item.get("optionNumber") or item.get("position")
        position = raw_position if isinstance(raw_position, int) and raw_position > 0 else ordinal
        identity = " ".join(
            str(item.get(field) or "").strip() for field in ("make", "model")
        ).strip()
        choices.append(
            {
                "position": position,
                "entityReference": f"vehicle:{item['id']}",
                "label": str(item.get("label") or f"Option {position} — {identity}").strip(),
                **{
                    field: item[field]
                    for field in PAGE_VEHICLE_REFERENCE_FIELDS
                    if field not in {"position", "vehicleId", "label"}
                    and item.get(field) not in (None, "")
                },
            }
        )
    return choices


def _grounded_card_data(payload: object, *card_types: str) -> dict | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("cards"), list):
        return None
    for card in payload["cards"]:
        if not isinstance(card, dict) or card.get("type") not in card_types:
            continue
        data = card.get("data")
        if isinstance(data, dict):
            return data
    return None
