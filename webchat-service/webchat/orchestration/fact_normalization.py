"""Normalize legacy domain tool outputs into the common trusted fact envelope.

The normalizer is intentionally deterministic. Tool handlers may return domain-shaped data while
the migration is in progress, but neither their prose nor raw payload becomes AI authority.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from webchat.orchestration.contracts.facts import (
    AlternativeOffer,
    AtomicFact,
    AvailableCard,
    AvailableCollection,
    AvailableLink,
    AvailableSuggestion,
    ResponseObligation,
    ResultEntity,
    ToolResultEnvelope,
)
from webchat.orchestration.contracts.presentation import CollectionPresentation

_PRIVATE_FIELDS = {
    "firstname",
    "lastname",
    "fullname",
    "registration",
    "notes",
    "message",
    "reason",
    "subject",
    "preferredtime",
    "bookingproof",
    "verification",
}
_ACTION_FIELDS = {
    "href",
    "url",
    "action",
    "actions",
    "buttons",
    "onclick",
    "handler",
    "quickreplies",
    "suggestions",
}
_ENTITY_FIELDS = {
    "vehicleId": "vehicle",
    "offerId": "offer",
    "dealershipId": "dealership",
    "serviceTypeId": "service",
    "slotId": "appointment",
    "bookingId": "booking",
}
_OPERATIONAL_IDENTIFIER_FIELDS = {
    "id",
    *(field.replace("_", "").casefold() for field in _ENTITY_FIELDS),
    *(f"{field.replace('_', '').casefold()}s" for field in _ENTITY_FIELDS),
}
_ENTITY_CONTAINERS = {
    "vehicle": "vehicle",
    "vehicles": "vehicle",
    "offer": "offer",
    "offers": "offer",
    "dealership": "dealership",
    "dealerships": "dealership",
    "service": "service",
    "services": "service",
    "appointment": "appointment",
    "appointments": "appointment",
    "slot": "appointment",
    "slots": "appointment",
    "booking": "booking",
    "bookings": "booking",
}
_VIEW_TO_CARD = {
    "vehicle_list": "vehicle_preview",
    "vehicle_details": "vehicle_preview",
    "vehicle_availability": "vehicle_preview",
    "vehicle_comparison": "vehicle_comparison",
    "offer_list": "offer",
    "dealership_list": "dealership",
    "workshop_location_list": "dealership",
    "opening_hours": "opening_hours",
    "service_list": "service",
    "part_exchange_estimate": "valuation",
    "confirmation": "confirmation",
    "receipt": "receipt",
    "workshop_booking_details": "booking",
}


class FactNormalizer:
    def normalize_approved_content(
        self,
        *,
        result_id: str,
        intent_id: str,
        entries: tuple[dict[str, str], ...],
    ) -> ToolResultEnvelope:
        now = datetime.now(UTC).replace(microsecond=0)
        facts = [
            AtomicFact(
                factId=f"fact-{_slug(result_id.removeprefix('result-'))}-entry-{index}",
                field=f"entries.{index}.text",
                value=entry["text"],
                displayValue=entry["text"],
                source="approved_content",
                sensitivity="public",
                observedAt=now,
            )
            for index, entry in enumerate(entries, start=1)
        ]
        return ToolResultEnvelope(
            resultId=result_id,
            callId=f"call-{_slug(result_id.removeprefix('result-'))}",
            intentId=intent_id,
            tool="approved_content",
            status="success",
            facts=facts,
            observedAt=now,
        )

    def normalize(
        self,
        *,
        result_id: str,
        call_id: str,
        intent_id: str,
        tool: str,
        result,
    ) -> ToolResultEnvelope:
        now = datetime.now(UTC).replace(microsecond=0)
        private_facts = str(result.view_type or "") in {
            "draft",
            "confirmation",
            "receipt",
            "private_booking_lookup",
            "part_exchange_estimate_form",
        } or tool.startswith(("prepare_", "request_"))
        presentation_source = result.view_payload
        facts_source = result.facts if isinstance(result.facts, dict) and not private_facts else {}
        if (
            private_facts
            and isinstance(presentation_source, dict)
            and isinstance(presentation_source.get("inputGuidance"), dict)
        ):
            facts_source = {"inputGuidance": presentation_source["inputGuidance"]}
        if tool == "estimate_part_exchange":
            private_inputs = {"registration", "mileage", "condition"}
            facts_source = _without_keys(facts_source, private_inputs)
            presentation_source = _without_keys(result.view_payload, private_inputs)
        resolution = facts_source.get("resolution")
        public_facts_source = (
            _without_keys(facts_source, {"resolution"})
            if isinstance(resolution, dict)
            and resolution.get("kind") in {"test_drive_slots", "workshop_slots"}
            else facts_source
        )
        selection_only = bool(
            isinstance(result.view_payload, dict)
            and result.view_payload.get("selectionOnly") is True
        )
        _validate_finite_choice_contract(result.view_payload, selection_only=selection_only)
        entity_type = _result_entity_type(tool, result.view_type, result.view_payload)
        entities = self._entities(facts_source, presentation_source, entity_type=entity_type)
        facts = self._facts(result_id, public_facts_source, now, entity_type=entity_type)
        collector_only = selection_only or str(result.view_type or "") in {
            "draft",
            "private_booking_lookup",
            "part_exchange_estimate_form",
            "slot_list",
            "test_drive_slot_picker",
        }
        cards = (
            []
            if collector_only
            or tool in {"list_dealership_departments", "find_dealership_departments"}
            else self._cards(result_id, result.view_type, presentation_source)
        )
        suggestions = [] if selection_only else self._suggestions(result_id, presentation_source)
        status = "success"
        resolution_status = (
            str(resolution.get("status") or "") if isinstance(resolution, dict) else ""
        )
        if resolution_status == "ambiguous" or facts_source.get("outcome") == "ambiguous":
            status = "ambiguous"
        elif resolution_status == "empty" or facts_source.get("outcome") == "empty":
            status = "empty"
        elif (
            facts_source.get("unavailable") is True
            or facts_source.get("supported") is False
            or facts_source.get("outcome") == "unavailable"
            or resolution_status == "unavailable"
        ):
            status = "unavailable"
        elif not facts and not cards and not private_facts:
            status = "empty"
        raw_alternative = result.alternative_offer
        if _requires_alternative_offer(
            resolution=resolution,
            facts_source=facts_source,
            presentation_source=presentation_source,
        ) and not isinstance(raw_alternative, dict):
            raise ValueError(
                "a result that offers a substitute must declare trusted alternative provenance"
            )
        normalized_alternative = (
            AlternativeOffer.model_validate(raw_alternative)
            if isinstance(raw_alternative, dict)
            else None
        )
        volatile = tool.startswith(("search_", "list_", "get_vehicle", "compare_"))
        return ToolResultEnvelope(
            resultId=result_id,
            callId=call_id,
            intentId=intent_id,
            tool=tool,
            status=status,
            entities=entities,
            facts=facts,
            availableCards=cards,
            availableSuggestions=suggestions,
            availableLinks=self._links(result_id, facts_source),
            availableCollections=self._collections(
                result_id,
                result.view_type,
                result.view_payload,
            ),
            alternativeOffer=normalized_alternative,
            responseObligation=_response_obligation(
                tool=tool,
                view_type=result.view_type,
                facts_source=facts_source,
                facts=facts,
                cards=cards,
                entities=entities,
                suggestions=suggestions,
            ),
            observedAt=now,
            expiresAt=now + timedelta(minutes=5) if volatile else None,
        )

    @staticmethod
    def _collections(
        result_id: str,
        view_type: str | None,
        payload: dict[str, Any] | None,
    ) -> list[AvailableCollection]:
        if not view_type or not isinstance(payload, dict):
            return []
        presentation = payload.get("collectionPresentation")
        if not isinstance(presentation, dict):
            return []
        collection_view_type = (
            "choice_list" if payload.get("collectionViewType") == "choice_list" else view_type
        )
        return [
            AvailableCollection(
                reference=f"collection:{result_id}",
                viewType=collection_view_type,
                presentation=CollectionPresentation.model_validate(presentation),
            )
        ]

    def _facts(
        self,
        result_id: str,
        source: dict[str, Any],
        observed_at: datetime,
        *,
        entity_type: str | None = None,
    ) -> list[AtomicFact]:
        facts: list[AtomicFact] = []
        used: set[str] = set()
        for path, value, entity_reference in _flatten_public(source, entity_type=entity_type):
            # Preserve the source path so both the hosted composer and the isolated test fake
            # can distinguish repeated item fields without receiving the raw tool payload.
            field = ".".join(path)[:80].rstrip(".")
            slug = _slug("-".join(path))[:100]
            base = f"fact-{_slug(result_id.removeprefix('result-'))}-{slug}"
            fact_id = base
            suffix = 2
            while fact_id in used:
                fact_id = f"{base}-{suffix}"
                suffix += 1
            used.add(fact_id)
            operational_identifier = any(
                part.replace("_", "").casefold() in _OPERATIONAL_IDENTIFIER_FIELDS for part in path
            )
            facts.append(
                AtomicFact(
                    factId=fact_id,
                    entityReference=entity_reference,
                    field=field,
                    value=value,
                    displayValue=_display(value, field),
                    source=(
                        "application_derived"
                        if path and path[0] in {"comparison", "inputGuidance"}
                        else "dealership_platform"
                    ),
                    # IDs remain available to deterministic policy and action binding, but are
                    # never model-visible or customer-displayable business facts.
                    sensitivity="internal" if operational_identifier else "public",
                    observedAt=observed_at,
                )
            )
        return facts[:500]

    def _entities(self, *sources: Any, entity_type: str | None = None) -> list[ResultEntity]:
        found: dict[str, ResultEntity] = {}

        def visit(value: Any, declared_type: str | None = None) -> None:
            if isinstance(value, dict):
                if declared_type and isinstance(value.get("id"), str) and value["id"]:
                    reference = f"{declared_type}:{value['id']}"
                    found[reference] = ResultEntity(reference=reference, type=declared_type)
                inferred = _first_entity_reference(value)
                if inferred:
                    namespace = inferred.split(":", 1)[0]
                    found[inferred] = ResultEntity(reference=inferred, type=namespace)
                for key, item in value.items():
                    if str(key).replace("_", "").casefold() in _ACTION_FIELDS:
                        continue
                    field_entity_type = _ENTITY_FIELDS.get(key)
                    if field_entity_type and isinstance(item, str) and item:
                        reference = f"{field_entity_type}:{item}"
                        found[reference] = ResultEntity(reference=reference, type=field_entity_type)
                    container_type = _ENTITY_CONTAINERS.get(str(key).casefold())
                    if key == "items" and isinstance(item, list):
                        container_type = entity_type
                    visit(item, container_type)
            elif isinstance(value, list):
                for item in value:
                    visit(item, declared_type)

        for source in sources:
            visit(source, entity_type if isinstance(source, dict) and source.get("id") else None)
        return list(found.values())[:100]

    def _cards(
        self, result_id: str, view_type: str | None, payload: dict[str, Any] | None
    ) -> list[AvailableCard]:
        card_type = _VIEW_TO_CARD.get(str(view_type or ""))
        if not card_type or not isinstance(payload, dict):
            return []
        visual = _visual_only(payload)
        visual = _number_result_items(visual)
        if not visual:
            return []
        entity_reference = _first_entity_reference(payload)
        return [
            AvailableCard(
                reference=f"card:{result_id}:{card_type}",
                type=card_type,
                entityReference=entity_reference,
                data=visual,
            )
        ]

    def _suggestions(
        self, result_id: str, payload: dict[str, Any] | None
    ) -> list[AvailableSuggestion]:
        if not isinstance(payload, dict) or not isinstance(payload.get("suggestions"), list):
            return []
        results = []
        for index, item in enumerate(payload["suggestions"][:20], start=1):
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or item.get("text") or "").strip()
            message = str(item.get("message") or item.get("text") or label).strip()
            if not label or not message:
                continue
            action = item.get("action") if isinstance(item.get("action"), dict) else None
            results.append(
                AvailableSuggestion(
                    reference=f"suggestion:{result_id}:{index}",
                    label=label[:120],
                    message=message[:500],
                    action=action,
                )
            )
        return results

    def _links(self, result_id: str, source: dict[str, Any]) -> list[AvailableLink]:
        """Expose only protocol-bound public contact destinations from trusted read tools."""
        links: list[AvailableLink] = []
        for path, value, _entity_reference in _flatten_public(source):
            if not isinstance(value, str):
                continue
            field = path[-1].casefold()
            if field == "phone":
                destination = re.sub(r"[^0-9+]", "", value)
                if not re.fullmatch(r"\+?[0-9]{7,15}", destination):
                    continue
                kind, href, label = "telephone", f"tel:{destination}", f"Call {value}"
            elif field == "email":
                if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
                    continue
                kind, href, label = "email", f"mailto:{value}", f"Email {value}"
            else:
                continue
            links.append(
                AvailableLink(
                    reference=f"link:{result_id}:{len(links) + 1}",
                    label=label,
                    href=href,
                    source="dealership_platform",
                    destinationKind=kind,
                )
            )
        return links[:20]


def _requires_alternative_offer(
    *,
    resolution: Any,
    facts_source: dict[str, Any],
    presentation_source: Any,
) -> bool:
    """Recognize application-owned recovery shapes without interpreting customer language."""

    continuation = str(resolution.get("continuation") or "") if isinstance(resolution, dict) else ""
    if continuation in {
        "offer_schedule_alternatives",
        "offer_location_alternatives",
        "offer_location_and_schedule_alternatives",
        "offer_equivalent_vehicle_slots",
        "choose_alternative_location",
    }:
        return True

    def has_marker(value: Any) -> bool:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).replace("_", "").casefold()
                if child is True and (
                    normalized.startswith("requested")
                    and normalized.endswith(("nomatch", "noavailability"))
                    or normalized == "alternativevehicle"
                ):
                    return True
                if normalized == "alternativeoffer" and isinstance(child, dict):
                    return True
                if has_marker(child):
                    return True
        elif isinstance(value, list):
            return any(has_marker(item) for item in value)
        return False

    return has_marker(facts_source) or has_marker(presentation_source)


def _flatten_public(
    value: Any,
    path: tuple[str, ...] = (),
    entity_reference: str | None = None,
    entity_type: str | None = None,
):
    if isinstance(value, dict):
        local_entity = entity_reference or _first_entity_reference(value, entity_type)
        for key, item in value.items():
            normalized = str(key).replace("_", "").casefold()
            if normalized in _PRIVATE_FIELDS or normalized in _ACTION_FIELDS:
                continue
            yield from _flatten_public(item, (*path, str(key)), local_entity, entity_type)
        return
    if isinstance(value, list):
        for index, item in enumerate(value[:100], start=1):
            yield from _flatten_public(item, (*path, str(index)), entity_reference, entity_type)
        return
    if value is not None and isinstance(value, str | int | float | bool) and path:
        yield path, value, entity_reference


def _visual_only(value: Any) -> Any:
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            normalized = str(key).replace("_", "").casefold()
            if normalized in _ACTION_FIELDS or normalized in _PRIVATE_FIELDS:
                continue
            cleaned = _visual_only(item)
            if cleaned not in (None, {}, []):
                clean[str(key)] = cleaned
        return clean
    if isinstance(value, list):
        return [cleaned for item in value if (cleaned := _visual_only(item)) not in (None, {}, [])]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return None


def _without_keys(value: Any, excluded: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_keys(item, excluded)
            for key, item in value.items()
            if str(key).casefold() not in excluded
        }
    if isinstance(value, list):
        return [_without_keys(item, excluded) for item in value]
    return value


def _number_result_items(payload: dict[str, Any]) -> dict[str, Any]:
    """Number only genuine result choices, never a uniquely resolved answer."""

    result = dict(payload)
    for key in ("vehicles", "items", "offers", "dealerships", "services", "slots"):
        items = result.get(key)
        if isinstance(items, list):
            numbered = len(items) > 1
            normalized_items = []
            for index, item in enumerate(items, start=1):
                if not isinstance(item, dict):
                    normalized_items.append(item)
                    continue
                normalized = {name: value for name, value in item.items() if name != "optionNumber"}
                if numbered:
                    normalized["optionNumber"] = index
                normalized_items.append(normalized)
            result[key] = normalized_items
            break
    return result


def _first_entity_reference(value: Any, entity_type: str | None = None) -> str | None:
    if not isinstance(value, dict):
        return None
    for field, field_entity_type in _ENTITY_FIELDS.items():
        if isinstance(value.get(field), str) and value[field]:
            return f"{field_entity_type}:{value[field]}"
    if entity_type and isinstance(value.get("id"), str) and value["id"]:
        return f"{entity_type}:{value['id']}"
    return None


def _result_entity_type(
    tool: str,
    view_type: str | None,
    payload: dict[str, Any] | None = None,
) -> str | None:
    choice_entity_type = (
        str(payload.get("choiceEntityType") or "") if isinstance(payload, dict) else ""
    )
    if choice_entity_type in {"appointment", "dealership", "offer", "service", "vehicle"}:
        return choice_entity_type
    if tool in {
        "search_vehicles",
        "reset_vehicle_search",
        "refine_vehicle_search",
        "select_page_vehicles",
        "get_vehicle",
        "get_vehicle_availability",
        "resolve_vehicle_availability",
        "compare_vehicles",
        "compare_vehicle_models",
    } or view_type in {"vehicle_list", "vehicle_details", "vehicle_comparison"}:
        return "vehicle"
    if tool in {"list_offers", "get_offer"} or view_type == "offer_list":
        return "offer"
    if tool.startswith(("list_dealership", "get_dealership")) or view_type in {
        "dealership_list",
        "workshop_location_list",
    }:
        return "dealership"
    if tool in {"list_service_types", "get_service_information"}:
        return "service"
    if tool in {
        "list_test_drive_slots",
        "list_workshop_slots",
        "refine_workshop_slots",
    } or view_type in {"slot_list", "test_drive_slot_picker"}:
        return "appointment"
    return None


def _validate_finite_choice_contract(
    payload: dict[str, Any] | None,
    *,
    selection_only: bool,
) -> None:
    """Require executable choice collections to identify their semantic target."""

    if not selection_only or not isinstance(payload, dict):
        return
    presentation = payload.get("collectionPresentation")
    purpose = str(presentation.get("purpose") or "") if isinstance(presentation, dict) else ""
    # A semantic reference clarification already carries its target in PendingInteraction.
    if purpose == "clarification":
        return
    namespace = str(payload.get("choiceEntityType") or "")
    field = str(payload.get("choiceField") or "")
    if not namespace or not field:
        raise ValueError("a finite workflow choice must declare its entity type and target field")
    expected_namespace = _ENTITY_FIELDS.get(field)
    if namespace == "workflow_option":
        if expected_namespace is not None:
            raise ValueError("an entity reference field cannot use scalar workflow choices")
        return
    if expected_namespace != namespace:
        raise ValueError("finite workflow choice namespace does not match its target field")


def _response_obligation(
    *,
    tool: str,
    view_type: str | None,
    facts_source: dict[str, Any],
    facts: list[AtomicFact],
    cards: list[AvailableCard],
    entities: list[ResultEntity],
    suggestions: list[AvailableSuggestion],
) -> ResponseObligation | None:
    resolution = facts_source.get("resolution")
    if (
        isinstance(resolution, dict)
        and resolution.get("kind") in {"test_drive_slots", "workshop_slots"}
        and resolution.get("continuation")
    ):
        vehicle_id = str(resolution.get("vehicleId") or "")
        subjects = [f"vehicle:{vehicle_id}"] if vehicle_id else ["appointment:workshop"]
        candidate_references = [
            entity.reference for entity in entities if entity.type == "appointment"
        ]
        choice_mode = (
            "confirm_single"
            if len(candidate_references) == 1
            else "choose_multiple"
            if len(candidate_references) >= 2
            else None
        )
        required_context_facts: list[str] = []
        if resolution.get("continuation") == "request_schedule_preferences" and vehicle_id:
            location_fact = next(
                (fact.factId for fact in facts if fact.field == "vehicle.dealershipName"),
                None,
            ) or next(
                (fact.factId for fact in facts if fact.field == "vehicle.dealershipTown"),
                None,
            )
            if location_fact:
                required_context_facts.append(location_fact)
        return ResponseObligation(
            kind="workflow_transition",
            workflowCode=resolution["continuation"],
            subjectReferences=subjects,
            requiredFactIds=required_context_facts,
            choiceMode=choice_mode,
            candidateReferences=candidate_references,
        )
    if (
        isinstance(resolution, dict)
        and resolution.get("kind") == "offer_scope"
        and resolution.get("status") == "ambiguous"
    ):
        make_facts = [fact.factId for fact in facts if fact.field.startswith("availableMakes.")]
        return ResponseObligation(
            kind="guided_discovery",
            subjectReferences=["offer:catalogue"],
            requiredFactIds=make_facts[:4],
        )
    if (
        isinstance(resolution, dict)
        and resolution.get("kind") == "dealership_location"
        and resolution.get("status") == "unavailable"
        and any(card.type == "dealership" for card in cards)
    ):
        required = [
            fact.factId
            for fact in facts
            if fact.field in {"resolution.status", "resolution.requestedTown"}
        ]
        return ResponseObligation(
            kind="location_resolution",
            subjectReferences=[
                fact.entityReference
                for fact in facts
                if fact.entityReference and fact.entityReference.startswith("dealership:")
            ][:4]
            or ["dealership:locations"],
            requiredCardType="dealership",
            requiredFactIds=required,
        )
    if (
        tool == "get_service_information"
        and isinstance(resolution, dict)
        and resolution.get("status") == "matched"
    ):
        price_fact = next(
            (
                fact.factId
                for fact in facts
                if fact.field in {"service.priceFromPence", "service.pricingStatus"}
            ),
            None,
        )
        subjects = [entity.reference for entity in entities if entity.type == "service"]
        return ResponseObligation(
            kind="service_detail",
            subjectReferences=subjects[:1] or ["service:matched"],
            requiredFactIds=[price_fact] if price_fact else [],
        )
    if (
        tool
        in {
            "search_vehicles",
            "reset_vehicle_search",
            "refine_vehicle_search",
            "select_page_vehicles",
        }
        and facts_source.get("outcome") == "empty"
    ):
        summary_fact = next(
            (fact.factId for fact in facts if fact.field == "filterSummary"),
            None,
        )
        return ResponseObligation(
            kind="informational_next_steps",
            subjectReferences=["vehicle:catalogue"],
            requiredFactIds=[summary_fact] if summary_fact else [],
        )
    catalogue_card = next(
        (
            card
            for card in cards
            if card.type in {"vehicle_preview", "offer", "dealership"}
            and isinstance(card.data.get("items"), list)
            and (
                len(card.data["items"]) > 1
                or tool
                in {
                    "search_vehicles",
                    "reset_vehicle_search",
                    "refine_vehicle_search",
                    "select_page_vehicles",
                }
            )
        ),
        None,
    )
    if catalogue_card is not None:
        subjects = list(
            dict.fromkeys(
                fact.entityReference for fact in facts if fact.entityReference is not None
            )
        )
        fallback_subject = {
            "vehicle_preview": "vehicle:catalogue",
            "offer": "offer:catalogue",
            "dealership": "dealership:catalogue",
        }[catalogue_card.type]
        return ResponseObligation(
            kind="catalogue_results",
            subjectReferences=subjects[:4] or [fallback_subject],
            requiredCardType=catalogue_card.type,
        )
    if tool in {"compare_vehicles", "compare_vehicle_models"}:
        subjects = list(
            dict.fromkeys(
                fact.entityReference
                for fact in facts
                if fact.entityReference and fact.entityReference.startswith("vehicle:")
            )
        )
        if isinstance(resolution, dict) and resolution.get("status") == "ambiguous":
            if subjects and any(card.type == "vehicle_preview" for card in cards):
                return ResponseObligation(
                    kind="vehicle_resolution",
                    subjectReferences=subjects[:4],
                    requiredCardType="vehicle_preview",
                    forbiddenSuggestionActions=["compare_displayed_vehicles"],
                )
            return None
        if view_type != "vehicle_comparison" or len(subjects) < 2:
            return None
        required_fact_ids = [
            fact.factId for fact in facts if fact.field == "comparison.selectionBasis"
        ]
        complete_suggestion_contract = (
            {
                "suggestionMode": "complete",
                "suggestionReferences": [suggestion.reference for suggestion in suggestions],
            }
            if len(suggestions) in {2, 4}
            else {}
        )
        return ResponseObligation(
            kind="vehicle_comparison",
            subjectReferences=subjects[:3],
            requiredCardType="vehicle_comparison",
            minimumComparedDimensions=2,
            allowedComparisonFields=[
                "pricePence",
                "monthlyPricePence",
                "mileage",
                "fuelType",
                "transmission",
                "bodyStyle",
                "year",
            ],
            requiredFactIds=required_fact_ids,
            forbiddenSuggestionActions=["compare_displayed_vehicles"],
            **complete_suggestion_contract,
        )
    if view_type != "suggestion_list" and (facts or cards or entities) and len(suggestions) >= 2:
        subjects = list(dict.fromkeys(entity.reference for entity in entities))
        return ResponseObligation(
            kind="informational_next_steps",
            subjectReferences=subjects[:4] or ["information:result"],
        )
    return None


def _display(value: Any, field: str | None = None) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    normalized = str(field or "").casefold()
    if normalized.endswith("pricingstatus"):
        return {
            "published": "Published price",
            "priced_on_request": "Priced on request",
        }.get(str(value), str(value))
    if normalized.endswith("pence") and isinstance(value, int) and not isinstance(value, bool):
        return f"£{value / 100:,.2f}"
    if normalized.endswith("mileage") and isinstance(value, int | float):
        return f"{value:,.0f} mi"
    if normalized.endswith("durationminutes") and isinstance(value, int | float):
        return f"{value:,.0f} minutes"
    if normalized.endswith("startsat") and isinstance(value, str):
        try:
            instant = datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=UTC)
            local = instant.astimezone(ZoneInfo("Europe/London"))
            return local.strftime("%a %d %b at %H:%M")
    return str(value)


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]+", "-", value).strip("-") or "value"
