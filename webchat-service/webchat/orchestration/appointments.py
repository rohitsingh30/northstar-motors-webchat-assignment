"""Canonical customer-facing appointment choice projection.

Appointment instants remain UTC for operations.  Every conversational and rendered choice uses
the same Europe/London projection so an answer can never be resolved against a different clock
from the one the customer saw.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from webchat.orchestration.contracts.presentation import CollectionPresentation

APPOINTMENT_TIME_ZONE = "Europe/London"
APPOINTMENT_CHOICE_LIMIT = 4
_LONDON = ZoneInfo(APPOINTMENT_TIME_ZONE)
_MONTHS = (
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sept",
    "Oct",
    "Nov",
    "Dec",
)


def appointment_fallback_queries(
    filters: dict[str, Any],
    *,
    earliest_date: str,
) -> dict[str, dict[str, Any]]:
    """Return the shared ordered scopes used after an appointment preference misses.

    Providers first keep the operational target and location while relaxing only schedule, then
    keep the target and requested schedule while relaxing location, and finally relax both
    schedule and location. Callers decide which later scopes are allowed for their workflow, but
    they do not rebuild these filters independently.
    """

    same_target_next = {
        key: value for key, value in filters.items() if key not in {"dateFrom", "dateTo"}
    }
    same_target_next["dateFrom"] = earliest_date
    other_location_requested_schedule = {
        key: value for key, value in filters.items() if key != "dealershipId"
    }
    other_location_next = {
        key: value
        for key, value in filters.items()
        if key not in {"dealershipId", "dateFrom", "dateTo"}
    }
    other_location_next["dateFrom"] = earliest_date
    return {
        "same_target_next": same_target_next,
        "other_location_requested_schedule": other_location_requested_schedule,
        "other_location_next": other_location_next,
    }


def appointment_display(starts_at: str) -> dict[str, str]:
    """Return the one trusted UK-local representation of an appointment instant."""

    instant = datetime.fromisoformat(str(starts_at))
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    local = instant.astimezone(_LONDON)
    local_date = local.date().isoformat()
    local_time = local.strftime("%H:%M")
    return {
        "timeZone": APPOINTMENT_TIME_ZONE,
        "localStartsAt": local.isoformat(timespec="minutes"),
        "localDate": local_date,
        "localTime": local_time,
        "displayLabel": f"{local.strftime('%a')}, {local.day} {_MONTHS[local.month]} {local.year}, {local_time}",
    }


def appointment_choice(item: dict[str, Any]) -> dict[str, Any]:
    """Attach display metadata without changing the authoritative operational instant."""

    starts_at = item.get("startsAt")
    if not isinstance(starts_at, str) or not starts_at:
        return dict(item)
    try:
        display = appointment_display(starts_at)
    except ValueError:
        return dict(item)
    return {**item, **display}


def appointment_choices(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the complete customer-facing finite set for one appointment decision.

    Availability APIs can return a long planning horizon, but a conversational appointment
    result is a decision surface rather than a paginated catalogue.  Bound the trusted records
    before facts, presentation, dialogue state, and replies are derived so every consumer sees the
    same first four gateway-ranked choices.
    """

    return [appointment_choice(item) for item in items[:APPOINTMENT_CHOICE_LIMIT]]


def appointment_collection_presentation(
    items: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Project every appointment choice through the generic collection owner."""

    display_items = []
    for item in items[:12]:
        label = str(item.get("displayLabel") or "").strip()
        if not label:
            continue
        vehicle = " ".join(
            str(item.get(field) or "").strip()
            for field in ("vehicleYear", "make", "model", "variant")
            if str(item.get(field) or "").strip()
        )
        context = [
            str(item.get("dealershipTown") or item.get("dealershipName") or "").strip(),
            str(item.get("serviceTypeName") or item.get("serviceName") or "").strip(),
            vehicle,
        ]
        display_items.append(
            {
                "label": label,
                "description": " · ".join(value for value in context if value) or None,
            }
        )
    if not display_items:
        return None
    return CollectionPresentation.model_validate(
        {
            "schemaVersion": 1,
            # Times are reference information the customer answers in ordinary language; render
            # the complete set as readable bullets instead of large action-like chips.
            "layout": "bullet_list",
            "purpose": "choice",
            "items": display_items,
        }
    ).model_dump(mode="json", exclude_none=True)


def appointment_choice_replies(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Return compact controls whose message is the exact trusted appointment label."""

    choices = [item for item in items[:12] if str(item.get("displayLabel") or "").strip()]
    if len(choices) == 1:
        return [
            {
                "label": "Choose this time",
                "text": str(choices[0]["displayLabel"]),
            }
        ]
    replies: list[dict[str, str]] = []
    labels: dict[str, int] = {}
    for item in choices:
        try:
            local = datetime.fromisoformat(str(item.get("localStartsAt") or ""))
            label = f"{local.strftime('%a')} {local.day} {_MONTHS[local.month]} · {local:%H:%M}"
        except ValueError:
            label = str(item["displayLabel"])
        labels[label] = labels.get(label, 0) + 1
        replies.append({"label": label, "text": str(item["displayLabel"])})
    for reply, item in zip(replies, choices, strict=True):
        if labels[reply["label"]] < 2:
            continue
        location = str(item.get("dealershipTown") or item.get("dealershipName") or "").strip()
        if location:
            reply["label"] = f"{reply['label']} · {location}"
    return replies


def appointment_collection_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a current appointment payload into a self-describing finite choice."""

    result = dict(payload)
    items = appointment_choices(list(result.get("items") or []))
    result["items"] = items
    if presentation := appointment_collection_presentation(items):
        # Generic choice consumers must not infer an entity namespace from a view name. The
        # namespace and target field travel with the same payload as the labels and identifiers.
        result["choiceEntityType"] = "appointment"
        result["choiceField"] = "slotId"
        result["collectionPresentation"] = presentation
        result["collectionViewType"] = "choice_list"
        result["choiceReplies"] = appointment_choice_replies(items)
    return result


def customer_choice_context(items: list[dict[str, object]]) -> list[dict[str, object]]:
    """Hide operational instants from model-facing choice context.

    Policy retains the full trusted records.  The semantic resolver receives only the exact local
    label and components rendered to the customer, plus non-temporal provenance.
    """

    customer_items: list[dict[str, object]] = []
    for item in items:
        if not str(item.get("entityReference") or "").startswith("appointment:"):
            customer_items.append(dict(item))
            continue
        customer_items.append(
            {key: value for key, value in item.items() if key not in {"startsAt", "localStartsAt"}}
        )
    return customer_items
