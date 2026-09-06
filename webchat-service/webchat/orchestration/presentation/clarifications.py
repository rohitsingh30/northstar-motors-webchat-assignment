"""Turn reviewed finite clarification options into a closed widget view."""

from __future__ import annotations

import re
from typing import Any

from webchat.domain.interactions import PendingInteraction
from webchat.orchestration.tools.result import ToolResult


def clarification_suggestion_result(
    text: str,
    suggestions: tuple[str, ...],
    interaction: PendingInteraction | None,
) -> ToolResult | None:
    """Render model-proposed replies without allowing them to execute an action."""
    if not suggestions or interaction is None or interaction.kind != "input":
        return None
    normalized = tuple(dict.fromkeys(option.strip() for option in suggestions if option.strip()))
    if not 2 <= len(normalized) <= 4:
        return None
    if len(normalized) == 3:
        normalized = normalized[:2]
    view_suggestions = [{"label": option, "text": option} for option in normalized]
    return ToolResult(
        text,
        "suggestion_list",
        {
            "version": 1,
            "completeChoiceSet": True,
            "suggestions": view_suggestions,
        },
        {"suggestionCount": len(view_suggestions)},
        interaction,
    )


def reference_clarification_presentation(
    interaction: PendingInteraction | None,
    context: Any,
) -> tuple[str, dict[str, Any] | None] | None:
    """Project a trusted reference ambiguity into one application-owned choice surface.

    Semantic resolution owns which references are candidates. The application owns their ordered
    labels and presentation, so a direct clarification can never degrade into a model-authored
    inline enumeration.
    """

    if interaction is None or interaction.kind != "reference_choice":
        return None
    candidates = _reference_display_candidates(context)
    items = []
    presentations = []
    namespaces: set[str] = set()
    for position, reference in enumerate(interaction.candidate_references, start=1):
        candidate = candidates.get(reference)
        if candidate is None:
            return None
        namespace, identifier, label, description, raw = candidate
        namespaces.add(namespace)
        items.append({"id": identifier, **raw, "position": position})
        if namespace == "vehicle":
            # Vehicle cards use page-local option labels. A clarification may merge several
            # trusted pages, so its own candidate order is authoritative and any earlier ordinal
            # must be removed before assigning the consolidated one.
            label = re.sub(
                r"^Option\s+\d+\s*(?:—|–|-)\s*",
                "",
                label,
                flags=re.IGNORECASE,
            ).strip()
            label = f"Option {position} — {label}"
        presentations.append(
            {
                "label": label,
                **({"description": description} if description else {}),
            }
        )
    if len(items) < 2 or len(namespaces) != 1:
        return None
    prompt = _collection_owned_prompt(interaction.prompt)
    if len(items) > 4:
        # Keep the complete typed candidate scope for the next turn without dumping a large menu.
        # The hosted planner owns the useful narrowing question; a finite visual chooser is only
        # appropriate for the existing two-to-four item interaction contract.
        return prompt, None
    return prompt, {
        "version": 1,
        "selectionOnly": True,
        "choiceEntityType": namespaces.pop(),
        "items": items,
        "collectionPresentation": {
            "schemaVersion": 1,
            "layout": "bullet_list",
            "purpose": "clarification",
            "items": presentations,
        },
    }


def _reference_display_candidates(
    context: Any,
) -> dict[str, tuple[str, str, str, str, dict[str, Any]]]:
    candidates: dict[str, tuple[str, str, str, str, dict[str, Any]]] = {}

    def add(
        namespace: str,
        identifier: Any,
        label: Any,
        description: Any = None,
        raw: dict[str, Any] | None = None,
    ) -> None:
        identifier = str(identifier or "").strip()
        label = str(label or "").strip()
        if not identifier or not label:
            return
        candidates.setdefault(
            f"{namespace}:{identifier}",
            (
                namespace,
                identifier,
                label,
                str(description or "").strip(),
                dict(raw or {}),
            ),
        )

    vehicles = [
        *getattr(context, "displayed_vehicles", ()),
        *getattr(context, "page_vehicles", ()),
    ]
    for item in vehicles:
        if not isinstance(item, dict):
            continue
        identity = " ".join(
            str(item.get(field) or "").strip() for field in ("year", "make", "model")
        ).strip()
        label = str(item.get("label") or identity).strip()
        description = " · ".join(
            value
            for value in (
                str(item.get("variant") or "").strip(),
                str(item.get("colour") or "").strip(),
                (
                    f"{int(item['mileage']):,} mi"
                    if isinstance(item.get("mileage"), int)
                    else ""
                ),
                str(item.get("dealershipTown") or "").strip(),
            )
            if value
        )
        add(
            "vehicle",
            item.get("vehicleId"),
            label,
            description,
            {key: value for key, value in item.items() if key != "vehicleId"},
        )

    for item in getattr(context, "displayed_offers", ()):
        if isinstance(item, dict):
            add(
                "offer",
                item.get("offerId"),
                item.get("title")
                or " ".join(str(item.get(key) or "") for key in ("make", "model")).strip(),
                item.get("productType"),
                {key: value for key, value in item.items() if key != "offerId"},
            )

    for item in getattr(context, "displayed_dealerships", ()):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        town = str(item.get("town") or "").strip()
        add(
            "dealership",
            item.get("dealershipId"),
            name or town,
            town if name and town and town.casefold() not in name.casefold() else None,
            {key: value for key, value in item.items() if key != "dealershipId"},
        )

    # Persisted choices are the identity fallback for namespaces without a richer typed entity
    # projection. Insert them last so a page-local label cannot erase retained vehicle, offer, or
    # dealership attributes when a new clarification consolidates multiple result surfaces.
    for item in getattr(context, "displayed_choices", ()):
        if not isinstance(item, dict):
            continue
        reference = str(item.get("entityReference") or "")
        if ":" not in reference:
            continue
        namespace, identifier = reference.split(":", 1)
        add(namespace, identifier, item.get("label"), item.get("description"))
    return candidates


def _collection_owned_prompt(prompt: str) -> str:
    """Remove a colon-delimited inline option list now owned by the collection."""

    lead, separator, _details = " ".join(prompt.split()).partition(":")
    if not separator:
        return lead
    return f"{lead.rstrip(' ?.')}?"
