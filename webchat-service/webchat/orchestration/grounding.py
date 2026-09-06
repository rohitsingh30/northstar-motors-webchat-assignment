"""Hard trust grounding plus non-failing presentation normalization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from webchat.domain.field_guidance import field_prompt
from webchat.orchestration.contracts.facts import (
    AtomicFact,
    AvailableCard,
    AvailableCollection,
    AvailableLink,
    AvailableSuggestion,
    ToolResultEnvelope,
)
from webchat.orchestration.contracts.response import (
    EmphasisSegment,
    FactSegment,
    GroundedMessageDraft,
    GroundedResponseDraft,
    LinkSegment,
    ListBlock,
    ParagraphBlock,
    TextSegment,
)
from webchat.orchestration.customer_language import validate_customer_language

_CRITICAL_TEXT = re.compile(
    r"(?:£|\b\d[\d,]*(?:\.\d+)?\s*(?:miles?|mi)\b|"
    r"\b(?:19|20)\d{2}\b|"
    r"\b20\d{2}-\d{2}-\d{2}\b|"
    r"\b\d{1,2}[/-]\d{1,2}[/-](?:\d{2}|\d{4})\b|"
    r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b|"
    r"\b\d+(?:\.\d+)?\s*(?:minutes?|hours?|days?|months?|years?)\b|"
    r"\b\d+(?:\.\d+)?(?:\s*%|\s+apr\b)|"
    r"\b(?:veh|offer|booking|enquiry)-[A-Za-z0-9-]+\b|"
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b|"
    r"\b(?:\+44|0)(?:1|7)\d[\d ()-]{7,}\b)",
    re.IGNORECASE,
)
_PRIVATE_MARKER = re.compile(
    r"\b(?:customer_private|bookingproof|verificationtoken)\b", re.IGNORECASE
)
_TEST_DRIVE_AVAILABILITY_CLAIM = re.compile(
    r"\b(?:"
    r"test[\s-]*drive(?:s|\s+(?:slots?|appointments?|times?))?\s+(?:are|is)\s+available|"
    r"(?:vehicles?|models?|cars?|bmws?)\s+(?:are|is)\s+available\s+for\s+(?:a\s+)?"
    r"test[\s-]*drive|"
    r"available\s+test[\s-]*drive(?:s|\s+(?:slots?|appointments?|times?))?|"
    r"you\s+can\s+test[\s-]*drive"
    r")\b",
    re.IGNORECASE,
)
_NEGATIVE_APPOINTMENT_AVAILABILITY_CLAIM = re.compile(
    r"(?:\b(?:no|not any|not currently any)\b|\bcould(?:n't| not)\s+find\s+any\b)"
    r"[^.?!]{0,100}"
    r"\b(?:test[\s-]*drive|workshop)?\s*(?:slots?|appointments?|times?)\b",
    re.IGNORECASE,
)
_PROTECTED_INPUT_REQUEST = re.compile(
    r"\b(?:provide|send|share|enter|need|collect|ask for|what(?:'s| is))\b[\s\S]{0,100}"
    r"\b(?:registration|mileage|first and last name|first name|last name|full name|"
    r"email|phone(?: number)?)\b",
    re.IGNORECASE,
)
_UNBACKED_PROGRESS_CLAIM = re.compile(
    r"\b(?:already selected|(?:i(?:'ve| have)|we(?:'ve| have))\s+(?:got|selected|prepared|booked)|"
    r"slot (?:is |has been )?selected)\b",
    re.IGNORECASE,
)
_FINANCE_TOTAL_REQUEST = re.compile(r"\btotal (?:amount )?payable\b", re.IGNORECASE)
_UNSUPPORTED_FINANCE_TOTAL = re.compile(
    r"\btotal (?:amount )?payable\b[\s\S]{0,180}"
    r"\b(?:would be|is approximately|comes to|equals|deposit plus)\b",
    re.IGNORECASE,
)
_INTERNAL_WORKFLOW_NARRATION = re.compile(
    r"\b(?:workflow state|availability recovery|recovery branch|missingpublicfields|"
    r"live resolver|fallback wording|tool result|renderer state|empty results?|"
    r"broad search|preference[_ -]filtered)\b",
    re.IGNORECASE,
)
_VAGUE_NEXT_STEP_QUESTION = re.compile(
    r"\bwhat would you like to (?:do|explore)(?: next)?\??$",
    re.IGNORECASE,
)
_UNSUPPORTED_LOCATION_RANKING = re.compile(
    r"\b(?:nearest|closest)\b[^.?!]{0,100}\b(?:appointment|dealership|location|option)\b|"
    r"\b(?:appointment|dealership|location|option)\b[^.?!]{0,100}\b(?:nearest|closest)\b",
    re.IGNORECASE,
)
_PROTECTED_SUGGESTION_ACTIONS = {
    "confirm_active_draft",
    "cancel_active_draft",
    "confirm_active_interaction",
    "cancel_active_interaction",
}
_WORKFLOW_PROGRESS_TOOLS = {
    "get_vehicle_availability",
    "list_test_drive_slots",
    "list_workshop_slots",
    "refine_workshop_slots",
}
_APPLICATION_OWNED_COLLECTION_PURPOSES = {"choice", "clarification"}
_QUESTION_PURPOSES = {"clarification", "follow_up", "workflow_prompt", "next_step"}
_INCOMPLETE_SENTENCE_ENDING = re.compile(
    r"(?:[:,;]|[–—-]|\b(?:and|or|but|because|with|at|to|from|for|of|in|on|by|as|than|"
    r"that|which|who|whose|where|when|while|including|is|are|was|were|be|been|being|"
    r"has|have|had|can|could|would|should|will|may|might|must|include|includes|offer|"
    r"offers|provide|provides|contain|contains|show|shows|the|a|an|your|our|"
    r"this|these|those))\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ResolvedMessage:
    purpose: str
    text: str
    blocks: tuple[dict, ...]
    collection_reference: str | None = None
    collection_rendered_in_blocks: bool = False

    @property
    def segments(self) -> tuple[dict, ...]:
        """Legacy projection for older API consumers and persisted conversations."""

        flattened: list[dict] = []
        for block in self.blocks:
            if block["type"] == "paragraph":
                flattened.extend(block["segments"])
                continue
            for item in block["items"]:
                flattened.append({"type": "bullet"})
                flattened.extend(item["segments"])
        return tuple(flattened)


@dataclass(frozen=True)
class ResolvedResponse:
    messages: tuple[ResolvedMessage, ...]
    cards: tuple[AvailableCard, ...]
    suggestions: tuple[AvailableSuggestion, ...]


class GroundingValidator:
    def __init__(
        self,
        *,
        allowed_link_hosts: set[str] | None = None,
        allow_local_http: bool = True,
    ) -> None:
        self.allowed_link_hosts = {item.casefold() for item in allowed_link_hosts or set()}
        self.allow_local_http = allow_local_http

    def resolve(
        self,
        draft: GroundedResponseDraft,
        results: list[ToolResultEnvelope],
        *,
        workflow_state: dict | None = None,
        question_owned: bool | None = None,
    ) -> ResolvedResponse:
        draft = normalize_optional_suggestions(draft, results, workflow_state or {})
        _validate_response_obligations(draft, results, workflow_state or {})
        facts = _unique_index((fact for result in results for fact in result.facts), "factId")
        cards = _unique_index(
            (card for result in results for card in result.availableCards), "reference"
        )
        suggestions = _unique_index(
            (item for result in results for item in result.availableSuggestions), "reference"
        )
        links = _unique_index(
            (link for result in results for link in result.availableLinks), "reference"
        )
        collections = _unique_index(
            (collection for result in results for collection in result.availableCollections),
            "reference",
        )
        resolved_messages = tuple(
            self._message(message, facts, links, collections) for message in draft.messages
        )
        if question_owned is False and any(
            message.purpose in {"clarification", "follow_up", "workflow_prompt", "next_step"}
            or message.text.rstrip().endswith("?")
            for message in resolved_messages
        ):
            raise ValueError("customer question requires a typed owner")
        _validate_tool_claim_boundaries(
            resolved_messages, results, workflow_state=workflow_state or {}
        )
        collection_item_fact_ids = {
            collection.reference: {
                fact.factId for fact in result.facts if fact.field.startswith("items.")
            }
            for result in results
            for collection in result.availableCollections
        }
        resolved_messages = _attach_trusted_collections(
            resolved_messages,
            collections,
            collection_item_fact_ids=collection_item_fact_ids,
        )
        selected_cards = tuple(_require(cards, reference) for reference in draft.cardReferences)
        resolved_messages = normalize_incomplete_sentence_blocks(
            resolved_messages,
            preserve_visual_introductions=bool(selected_cards),
        )
        selected_suggestions = [
            _require(suggestions, reference) for reference in draft.suggestionReferences
        ]
        selected_suggestion_references = {
            suggestion.reference for suggestion in selected_suggestions
        }
        selected_suggestions.extend(
            suggestion
            for suggestion in suggestions.values()
            if str((suggestion.action or {}).get("type") or "") in _PROTECTED_SUGGESTION_ACTIONS
            and suggestion.reference not in selected_suggestion_references
        )
        ai_suggestions = []
        allow_ai_quick_replies = not any(
            collection.presentation.purpose == "choice" for collection in collections.values()
        )
        for index, item in enumerate(
            draft.quickReplies if allow_ai_quick_replies else [],
            start=1,
        ):
            self._validate_text(item.label)
            self._validate_text(item.message)
            ai_suggestions.append(
                AvailableSuggestion(
                    reference=f"suggestion:ai:{index}",
                    label=item.label,
                    message=item.message,
                )
            )
        _validate_answer_controls(
            resolved_messages,
            (*selected_suggestions, *ai_suggestions),
            tuple(collections.values()),
        )
        for reference in draft.linkReferences:
            self._validate_link(_require(links, reference))
        return ResolvedResponse(
            resolved_messages,
            selected_cards,
            (*selected_suggestions, *ai_suggestions),
        )

    def _message(
        self,
        message,
        facts: dict,
        links: dict,
        collections: dict[str, AvailableCollection],
    ) -> ResolvedMessage:
        blocks: list[dict] = []
        for block in message.blocks:
            if isinstance(block, ParagraphBlock):
                blocks.append(
                    {
                        "type": "paragraph",
                        "segments": self._inline_segments(block.segments, facts, links),
                    }
                )
                continue
            if isinstance(block, ListBlock):
                blocks.append(
                    {
                        "type": "list",
                        "items": [
                            {
                                "segments": self._inline_segments(
                                    item.segments,
                                    facts,
                                    links,
                                )
                            }
                            for item in block.items
                        ],
                    }
                )
                continue
            raise TypeError("unknown response block")  # pragma: no cover
        text = _resolved_block_text(blocks)
        if not text:
            raise ValueError("resolved response message is empty")
        if message.collectionReference is not None:
            _require(collections, message.collectionReference)
        return ResolvedMessage(
            message.purpose,
            text,
            tuple(blocks),
            message.collectionReference,
            False,
        )

    def _inline_segments(self, source, facts: dict, links: dict) -> list[dict]:
        segments: list[dict] = []
        for segment in source:
            if isinstance(segment, TextSegment):
                self._validate_text(segment.text)
                segments.append({"type": "text", "text": segment.text})
            elif isinstance(segment, EmphasisSegment):
                self._validate_text(segment.text)
                segments.append({"type": "emphasis", "text": segment.text})
            elif isinstance(segment, FactSegment):
                fact: AtomicFact = _require(facts, segment.factId)
                if fact.sensitivity not in {"public", "customer_safe"}:
                    raise ValueError("response references a non-displayable fact")
                segments.append(
                    {
                        "type": "fact",
                        "factId": fact.factId,
                        "text": fact.displayValue,
                    }
                )
            elif isinstance(segment, LinkSegment):
                link: AvailableLink = _require(links, segment.linkReference)
                self._validate_link(link)
                segments.append(
                    {
                        "type": "link",
                        "label": link.label,
                        "href": link.href,
                        "destinationKind": link.destinationKind,
                    }
                )
            else:  # pragma: no cover - discriminated schema makes this unreachable
                raise TypeError("unknown response segment")
        return _normalize_inline_segments(segments)

    @staticmethod
    def _validate_text(text: str) -> None:
        validate_customer_language(text)
        if _PRIVATE_MARKER.search(text):
            raise ValueError("response text contains a private/internal marker")
        if _CRITICAL_TEXT.search(text):
            raise ValueError("critical business values must use trusted fact segments")
        if "<" in text or ">" in text:
            raise ValueError("AI response text must be plain text")

    def _validate_link(self, link: AvailableLink) -> None:
        parsed = urlsplit(link.href)
        scheme = parsed.scheme.casefold()
        if scheme == "tel":
            if link.destinationKind != "telephone":
                raise ValueError("link protocol does not match destination kind")
            return
        if scheme == "mailto":
            if link.destinationKind != "email":
                raise ValueError("link protocol does not match destination kind")
            return
        if not scheme and link.href.startswith("/") and not link.href.startswith("//"):
            if link.destinationKind not in {"directions", "website"}:
                raise ValueError("relative links must be website destinations")
            return
        if link.destinationKind not in {"directions", "website"}:
            raise ValueError("web links must be website destinations")
        if scheme not in {"https", "http"} or not parsed.netloc:
            raise ValueError("link destination protocol is not allowed")
        host = (parsed.hostname or "").casefold()
        if scheme == "http" and not (
            self.allow_local_http and host in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("plain HTTP links are allowed only for local development")
        if host not in self.allowed_link_hosts and host not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("link host is not allow-listed")
        if parsed.username or parsed.password:
            raise ValueError("link destinations cannot contain credentials")


def _attach_trusted_collections(
    messages: tuple[ResolvedMessage, ...],
    collections: dict[str, AvailableCollection],
    *,
    collection_item_fact_ids: dict[str, set[str]] | None = None,
) -> tuple[ResolvedMessage, ...]:
    """Give each collection one presentation owner without rejecting a safe result.

    Choice and clarification collections are application-owned because their trusted items and
    actions form one answer surface. The response composer may introduce or ask about that surface,
    but any duplicate item blocks it emits are removed across the whole turn before the collection
    is attached to the final question. Informational collections retain the earlier either/or
    behaviour: a complete AI-rendered list may own their presentation.
    """

    normalized = _prune_application_owned_collection_details(
        messages,
        collections,
        collection_item_fact_ids or {},
    )
    normalized = _place_application_owned_collections(normalized, collections)

    attached: set[str] = set()
    attached_messages: list[ResolvedMessage] = []
    for message in normalized:
        reference = message.collection_reference
        rendered_in_blocks = False
        if (
            reference is not None
            and reference in collections
            and _collection_is_fully_rendered(message.blocks, collections[reference])
        ):
            # Keep the reference context but tell the renderer that the AI already presented it.
            attached.add(reference)
            rendered_in_blocks = True
        elif reference in attached:
            reference = None
        elif reference is not None:
            attached.add(reference)
        attached_messages.append(
            ResolvedMessage(
                message.purpose,
                message.text,
                message.blocks,
                reference,
                rendered_in_blocks,
            )
        )
    normalized = attached_messages

    for reference, collection in collections.items():
        if reference in attached:
            continue
        matching_index = next(
            (
                index
                for index, message in enumerate(normalized)
                if message.collection_reference is None
                and _collection_is_fully_rendered(message.blocks, collection)
            ),
            None,
        )
        if matching_index is None:
            continue
        message = normalized[matching_index]
        normalized[matching_index] = ResolvedMessage(
            message.purpose,
            message.text,
            message.blocks,
            reference,
            True,
        )
        attached.add(reference)

    missing = [reference for reference in collections if reference not in attached]
    preferred_slots = [
        index
        for index, message in enumerate(normalized)
        if message.collection_reference is None
        and message.purpose in {"answer", "comparison", "transition"}
    ]
    fallback_slots = [
        index
        for index, message in enumerate(normalized)
        if message.collection_reference is None and index not in preferred_slots
    ]
    for reference, index in zip(missing, [*preferred_slots, *fallback_slots], strict=False):
        message = normalized[index]
        normalized[index] = ResolvedMessage(
            message.purpose,
            message.text,
            message.blocks,
            reference,
            False,
        )
        attached.add(reference)

    # A compound turn can return more independent collections than the composer emitted bubbles.
    # Preserve those authoritative results with a neutral presentation wrapper instead of failing
    # the customer's otherwise successful request.
    for reference in missing:
        if reference in attached:
            continue
        normalized.append(
            ResolvedMessage(
                "answer",
                "Here are the relevant results.",
                (
                    {
                        "type": "paragraph",
                        "segments": [{"type": "text", "text": "Here are the relevant results."}],
                    },
                ),
                reference,
                False,
            )
        )
    return tuple(normalized)


def _prune_application_owned_collection_details(
    messages: tuple[ResolvedMessage, ...],
    collections: dict[str, AvailableCollection],
    collection_item_fact_ids: dict[str, set[str]],
) -> list[ResolvedMessage]:
    """Remove composer-owned copies of application-owned collection item details."""

    application_collections = [
        collection
        for collection in collections.values()
        if collection.presentation.purpose in _APPLICATION_OWNED_COLLECTION_PURPOSES
    ]
    normalized: list[ResolvedMessage] = []
    for message in messages:
        blocks = list(message.blocks)
        for collection in application_collections:
            item_fact_ids = collection_item_fact_ids.get(collection.reference, set())
            blocks = [
                block
                for block in blocks
                if not _block_duplicates_application_collection(
                    block,
                    collection,
                    item_fact_ids,
                )
            ]
        if not blocks:
            continue
        normalized.append(
            ResolvedMessage(
                message.purpose,
                _resolved_block_text(blocks),
                tuple(blocks),
                message.collection_reference,
                False,
            )
        )
    return normalized


def _place_application_owned_collections(
    messages: list[ResolvedMessage],
    collections: dict[str, AvailableCollection],
) -> list[ResolvedMessage]:
    """Attach each application-owned collection exactly once to its final question."""

    normalized = list(messages)
    for reference, collection in collections.items():
        if collection.presentation.purpose not in _APPLICATION_OWNED_COLLECTION_PURPOSES:
            continue

        # Explicit model placement is only a hint. The application owns the final placement and
        # moves a choice surface to the question it answers.
        normalized = [
            ResolvedMessage(
                message.purpose,
                message.text,
                message.blocks,
                (
                    None
                    if message.collection_reference == reference
                    else message.collection_reference
                ),
                False,
            )
            for message in normalized
        ]
        owner_index = next(
            (
                index
                for index in range(len(normalized) - 1, -1, -1)
                if normalized[index].collection_reference is None
                and normalized[index].purpose in _QUESTION_PURPOSES
                and normalized[index].text.rstrip().endswith("?")
            ),
            None,
        )
        if owner_index is None:
            prompt = (
                "Which option did you mean?"
                if collection.presentation.purpose == "clarification"
                else "Which option would you like?"
            )
            normalized.append(
                ResolvedMessage(
                    "clarification"
                    if collection.presentation.purpose == "clarification"
                    else "workflow_prompt",
                    prompt,
                    (
                        {
                            "type": "paragraph",
                            "segments": [{"type": "text", "text": prompt}],
                        },
                    ),
                    reference,
                    False,
                )
            )
            continue
        owner = normalized[owner_index]
        normalized[owner_index] = ResolvedMessage(
            owner.purpose,
            owner.text,
            owner.blocks,
            reference,
            False,
        )
    return normalized


def _block_duplicates_application_collection(
    block: dict,
    collection: AvailableCollection,
    item_fact_ids: set[str],
) -> bool:
    referenced_fact_ids = {
        str(segment.get("factId"))
        for segments in (
            [block.get("segments", [])]
            if block.get("type") == "paragraph"
            else [item.get("segments", []) for item in block.get("items", [])]
        )
        for segment in segments
        if segment.get("type") == "fact" and segment.get("factId")
    }
    if referenced_fact_ids.intersection(item_fact_ids):
        return True
    block_text = _normalized_collection_text(
        " ".join(
            _resolved_inline_text(segments)
            for segments in (
                [block.get("segments", [])]
                if block.get("type") == "paragraph"
                else [item.get("segments", []) for item in block.get("items", [])]
            )
        )
    )
    matching_items = [
        item
        for item in collection.presentation.items
        if re.search(
            rf"(?<!\w){re.escape(_normalized_collection_text(item.label))}(?!\w)",
            block_text,
        )
    ]
    if block.get("type") == "list":
        return bool(matching_items) or _collection_list_matches(
            block,
            collection,
            require_complete=False,
        )
    if len(matching_items) > 1:
        return True
    if len(matching_items) != 1:
        return False
    item = matching_items[0]
    duplicate_values = {_normalized_collection_text(item.label)}
    if item.description:
        duplicate_values.update(
            {
                _normalized_collection_text(item.description),
                _normalized_collection_text(f"{item.label} — {item.description}"),
                _normalized_collection_text(f"{item.label} - {item.description}"),
                _normalized_collection_text(f"{item.label}: {item.description}"),
            }
        )
    return block_text in duplicate_values


def _collection_is_fully_rendered(
    blocks: tuple[dict, ...],
    collection: AvailableCollection,
) -> bool:
    """Recognize only a complete, trusted-equivalent AI list as collection presentation."""

    return any(
        block.get("type") == "list"
        and _collection_list_matches(block, collection, require_complete=True)
        for block in blocks
    )


def _collection_list_matches(
    block: dict,
    collection: AvailableCollection,
    *,
    require_complete: bool,
) -> bool:
    expected = [
        {
            _normalized_collection_text(item.label),
            *(
                {
                    _normalized_collection_text(f"{item.label} — {item.description}"),
                    _normalized_collection_text(f"{item.label} - {item.description}"),
                    _normalized_collection_text(f"{item.label}: {item.description}"),
                }
                if item.description
                else set()
            ),
        }
        for item in collection.presentation.items
    ]
    rendered = [
        _normalized_collection_text(_resolved_inline_text(item.get("segments", [])))
        for item in block.get("items", [])
    ]
    if not rendered or len(rendered) > len(expected):
        return False
    if require_complete and len(rendered) != len(expected):
        return False
    unmatched = list(expected)
    for value in rendered:
        match = next(
            (index for index, accepted in enumerate(unmatched) if value in accepted),
            None,
        )
        if match is None:
            return False
        unmatched.pop(match)
    return not unmatched if require_complete else True


def _normalized_collection_text(value: str) -> str:
    return " ".join(str(value).casefold().split()).strip(" .")


def normalize_optional_suggestions(
    draft: GroundedResponseDraft,
    results: list[ToolResultEnvelope] | tuple[ToolResultEnvelope, ...],
    workflow_state: dict,
) -> GroundedResponseDraft:
    """Normalize optional chip conflicts without judging natural-language relevance."""

    appended_continuation = False
    question_purposes = {"clarification", "follow_up", "workflow_prompt", "next_step"}
    quick_replies = list(draft.quickReplies[:4])
    if len(quick_replies) == 3:
        quick_replies = quick_replies[:2]
    draft = draft.model_copy(update={"quickReplies": quick_replies})
    missing_fields = list(
        dict(workflow_state.get("constraints") or {}).get("missingPublicFields") or []
    )
    if missing_fields and draft.messages[-1].purpose not in question_purposes:
        messages = list(draft.messages)
        prompt = GroundedMessageDraft(
            purpose="workflow_prompt",
            blocks=[
                ParagraphBlock(
                    type="paragraph",
                    segments=[TextSegment(type="text", text=field_prompt(missing_fields[0]))],
                )
            ],
        )
        if len(messages) < 4:
            messages.append(prompt)
        else:
            messages[-1] = prompt
        draft = draft.model_copy(update={"messages": messages})
    available_suggestions = [
        suggestion for result in results for suggestion in result.availableSuggestions
    ]
    action_by_reference = {
        suggestion.reference: str((suggestion.action or {}).get("type") or "")
        for suggestion in available_suggestions
    }
    references = list(draft.suggestionReferences)
    action_suggestion_by_message = {
        " ".join(suggestion.message.casefold().split()): suggestion
        for suggestion in available_suggestions
        if suggestion.action
    }
    remaining_quick_replies = []
    for quick_reply in quick_replies:
        matched = action_suggestion_by_message.get(
            " ".join(quick_reply.message.casefold().split())
        )
        if matched is None:
            remaining_quick_replies.append(quick_reply)
            continue
        if matched.reference not in references:
            references.append(matched.reference)
    quick_replies = remaining_quick_replies
    draft = draft.model_copy(
        update={"suggestionReferences": references, "quickReplies": quick_replies}
    )
    complete_suggestion_references = list(
        dict.fromkeys(
            reference
            for result in results
            if result.responseObligation is not None
            and result.responseObligation.suggestionMode == "complete"
            for reference in result.responseObligation.suggestionReferences
        )
    )
    if complete_suggestion_references:
        # A complete set is application-owned: composition may introduce it conversationally,
        # but it may not omit one candidate or replace it with model-authored quick replies.
        return draft.model_copy(
            update={
                "suggestionReferences": complete_suggestion_references,
                "quickReplies": [],
            }
        )
    if (
        results
        and not missing_fields
        and not any(result.tool.startswith(("prepare_", "request_")) for result in results)
        and not any(
            collection.presentation.purpose in _APPLICATION_OWNED_COLLECTION_PURPOSES
            for result in results
            for collection in result.availableCollections
        )
        and not _draft_ends_with_question(draft)
    ):
        draft = _append_conversation_continuation(
            draft,
            has_suggestions=bool(available_suggestions or quick_replies),
        )
        appended_continuation = True
    typed_references = [reference for reference in references if action_by_reference.get(reference)]
    protected_references = [
        reference
        for reference in typed_references
        if action_by_reference.get(reference) in _PROTECTED_SUGGESTION_ACTIONS
    ]
    if workflow_state.get("activeWorkflow") and draft.messages[-1].purpose in question_purposes:
        continuation_items = workflow_continuation_suggestions(results, workflow_state)
        continuation_references = {item.reference for item in continuation_items}
        selected = [reference for reference in references if reference in continuation_references]
        selected = _balanced_suggestion_references(
            [*protected_references, *selected],
            continuation_items,
        )
        return _synchronize_appended_continuation(
            draft.model_copy(
                update={
                    "suggestionReferences": selected,
                    "quickReplies": [],
                }
            ),
            appended=appended_continuation,
        )
    catalogue_suggestions = [
        suggestion
        for result in results
        if result.responseObligation is not None
        and result.responseObligation.kind == "catalogue_results"
        and any(card.type == "vehicle_preview" for card in result.availableCards)
        for suggestion in result.availableSuggestions
    ]
    if catalogue_suggestions:
        available_references = {item.reference for item in catalogue_suggestions}
        ai_selected = [reference for reference in references if reference in available_references]
        return _synchronize_appended_continuation(
            draft.model_copy(
                update={
                    "suggestionReferences": _required_next_step_references(
                        ai_selected,
                        catalogue_suggestions,
                    ),
                    "quickReplies": [],
                }
            ),
            appended=appended_continuation,
        )
    comparison_suggestions = [
        suggestion
        for result in results
        if result.responseObligation is not None
        and result.responseObligation.kind == "vehicle_comparison"
        for suggestion in result.availableSuggestions
    ]
    if comparison_suggestions:
        available_references = {item.reference for item in comparison_suggestions}
        ai_selected = [reference for reference in references if reference in available_references]
        return _synchronize_appended_continuation(
            draft.model_copy(
                update={
                    "suggestionReferences": _required_next_step_references(
                        ai_selected,
                        comparison_suggestions,
                    ),
                    "quickReplies": [],
                }
            ),
            appended=appended_continuation,
        )
    informational_suggestions = [
        suggestion
        for result in results
        if result.responseObligation is not None
        and result.responseObligation.kind == "informational_next_steps"
        for suggestion in result.availableSuggestions
    ]
    if informational_suggestions:
        available_references = {item.reference for item in informational_suggestions}
        ai_selected = [reference for reference in references if reference in available_references]
        return _synchronize_appended_continuation(
            draft.model_copy(
                update={
                    "suggestionReferences": _required_next_step_references(
                        ai_selected,
                        informational_suggestions,
                    ),
                    "quickReplies": [],
                }
            ),
            appended=appended_continuation,
        )
    if draft.messages[-1].purpose not in question_purposes:
        return _synchronize_appended_continuation(
            draft.model_copy(
                update={
                    # Typed controls remain useful after an answer (for example inventory paging
                    # or a finite filter choice). Only free-form AI answer guesses require a
                    # question.
                    "suggestionReferences": _balanced_suggestion_references(
                        typed_references,
                        [item for result in results for item in result.availableSuggestions],
                    ),
                    "quickReplies": [],
                }
            ),
            appended=appended_continuation,
        )
    if references and draft.quickReplies:
        if typed_references:
            # Exact server-authored operation controls outrank model-authored text-only copies.
            return _synchronize_appended_continuation(
                draft.model_copy(
                    update={
                        "suggestionReferences": _balanced_suggestion_references(
                            typed_references,
                            available_suggestions,
                        ),
                        "quickReplies": [],
                    }
                ),
                appended=appended_continuation,
            )
        # Prefer the AI's contextual answer choices to a tool's generic optional menu.
        return _synchronize_appended_continuation(
            draft.model_copy(
                update={"suggestionReferences": [], "quickReplies": draft.quickReplies}
            ),
            appended=appended_continuation,
        )
    return _synchronize_appended_continuation(
        draft.model_copy(
            update={
                "suggestionReferences": _balanced_suggestion_references(
                    references,
                    [item for result in results for item in result.availableSuggestions],
                )
            }
        ),
        appended=appended_continuation,
    )


def _draft_ends_with_question(draft: GroundedResponseDraft) -> bool:
    if not draft.messages:
        return False
    final = draft.messages[-1]
    for block in reversed(final.blocks):
        segment_groups = (
            [block.segments]
            if isinstance(block, ParagraphBlock)
            else [item.segments for item in block.items]
        )
        for segments in reversed(segment_groups):
            for segment in reversed(segments):
                value = (
                    segment.text
                    if isinstance(segment, TextSegment | EmphasisSegment)
                    else ""
                )
                if value:
                    return value.rstrip().endswith("?")
    return False


def _append_conversation_continuation(
    draft: GroundedResponseDraft,
    *,
    has_suggestions: bool,
) -> GroundedResponseDraft:
    """Guarantee that a successful result never leaves the customer at a dead end."""

    prompt = (
        "Which of these options would you like help with next?"
        if has_suggestions
        else "What else can I help you with?"
    )
    follow_up = GroundedMessageDraft(
        purpose="follow_up",
        segments=[TextSegment(type="text", text=prompt)],
    )
    messages = list(draft.messages)
    if len(messages) < 4:
        messages.append(follow_up)
    else:
        final = messages[-1]
        messages[-1] = final.model_copy(
            update={
                "purpose": "follow_up",
                "blocks": [
                    *final.blocks,
                    ParagraphBlock(
                        type="paragraph",
                        segments=[TextSegment(type="text", text=prompt)],
                    ),
                ],
            }
        )
    return draft.model_copy(update={"messages": messages})


def _synchronize_appended_continuation(
    draft: GroundedResponseDraft,
    *,
    appended: bool,
) -> GroundedResponseDraft:
    """Make an application-added question describe the controls that actually survived.

    Candidate discovery and final control selection are intentionally separate phases. A prompt
    may say "these options" only after the latter phase has retained at least one visible option.
    """

    if not appended or not draft.messages:
        return draft
    messages = list(draft.messages)
    final = messages[-1]
    blocks = list(final.blocks)
    if not blocks or not isinstance(blocks[-1], ParagraphBlock):
        return draft
    segments = list(blocks[-1].segments)
    if (
        len(segments) != 1
        or not isinstance(segments[0], TextSegment)
        or segments[0].text
        not in {
            "Which of these options would you like help with next?",
            "What else can I help you with?",
        }
    ):
        return draft
    prompt = (
        "Which of these options would you like help with next?"
        if draft.suggestionReferences or draft.quickReplies
        else "What else can I help you with?"
    )
    blocks[-1] = ParagraphBlock(
        type="paragraph",
        segments=[TextSegment(type="text", text=prompt)],
    )
    messages[-1] = final.model_copy(update={"blocks": blocks})
    return draft.model_copy(update={"messages": messages})


def _required_next_step_references(
    selected: list[str],
    candidates: list[AvailableSuggestion],
) -> list[str]:
    """Materialize a balanced result-scoped next step when composition omits one."""

    available = list(dict.fromkeys(item.reference for item in candidates))[:4]
    references = [reference for reference in dict.fromkeys(selected) if reference in available][:4]
    target = (
        4
        if len(available) >= 4 and len(references) in {0, 3, 4}
        else min(2, len(available))
    )
    for reference in available:
        if len(references) >= target:
            break
        if reference not in references:
            references.append(reference)
    if len(references) == 3:
        references = references[:2]
    return references


def _balanced_suggestion_references(
    selected: list[str],
    candidates: list[AvailableSuggestion],
) -> list[str]:
    """Keep optional chip rows balanced without inventing customer actions.

    Three selected chips are completed from the same trusted candidate set when possible; if no
    fourth candidate exists, the lower-priority third chip is omitted. Other cardinalities are
    preserved because a single mandatory/protected action must not be hidden.
    """

    references = list(dict.fromkeys(selected))[:4]
    if len(references) != 3:
        return references
    for candidate in candidates:
        if candidate.reference not in references:
            return [*references, candidate.reference]
    return references[:2]


def workflow_continuation_suggestions(
    results: list[ToolResultEnvelope] | tuple[ToolResultEnvelope, ...],
    workflow_state: dict,
) -> list[AvailableSuggestion]:
    """Select controls by result provenance and the fields they can supply."""

    if not workflow_state.get("activeWorkflow"):
        return []
    missing_fields = set(
        dict(workflow_state.get("constraints") or {}).get("missingPublicFields") or []
    )

    def relevant(items: list[AvailableSuggestion]) -> list[AvailableSuggestion]:
        if not missing_fields:
            return list(items)
        return [item for item in items if missing_fields.intersection((item.action or {}).keys())]

    return next(
        (
            selected
            for result in reversed(results)
            if (selected := relevant(result.availableSuggestions))
        ),
        [],
    )


def _validate_response_obligations(
    draft: GroundedResponseDraft,
    results: list[ToolResultEnvelope] | tuple[ToolResultEnvelope, ...],
    workflow_state: dict,
) -> None:
    """Enforce structural conversation quality that the renderer cannot safely invent."""

    obligations = [
        result.responseObligation for result in results if result.responseObligation is not None
    ]
    workflow_results = [
        result
        for result in results
        if result.responseObligation is not None
        and result.responseObligation.kind == "workflow_transition"
    ]
    missing_public_fields = list(
        dict(workflow_state.get("constraints") or {}).get("missingPublicFields") or []
    )
    if (
        workflow_results
        and missing_public_fields
        and draft.messages[-1].purpose != "workflow_prompt"
    ):
        raise ValueError("workflow transition must end with its required customer question")
    if (
        any(obligation.kind == "catalogue_results" for obligation in obligations)
        and not missing_public_fields
        and draft.messages[-1].purpose not in {"follow_up", "next_step"}
    ):
        raise ValueError("catalogue results must end with a separate follow-up question")
    informational_next_steps = any(
        obligation.kind == "informational_next_steps" for obligation in obligations
    )
    if informational_next_steps and not missing_public_fields:
        final_message = draft.messages[-1]
        if final_message.purpose not in {"follow_up", "next_step"}:
            raise ValueError("informational result must end with a separate follow-up question")
        available_references = {
            suggestion.reference for result in results for suggestion in result.availableSuggestions
        }
        selected_references = [
            reference
            for reference in draft.suggestionReferences
            if reference in available_references
        ]
        if len(available_references) >= 2 and len(selected_references) not in {2, 4}:
            raise ValueError("informational follow-up must select two or four trusted suggestions")
    if any(
        result.status in {"empty", "unavailable"} for result in workflow_results
    ) and not any(message.purpose == "warning" for message in draft.messages):
        raise ValueError("empty workflow outcome must be explained before recovery")
    required_fact_ids = {
        fact_id for obligation in obligations for fact_id in obligation.requiredFactIds
    }
    referenced_fact_ids = {
        segment.factId
        for message in draft.messages
        for block in message.blocks
        for segments in (
            [block.segments]
            if isinstance(block, ParagraphBlock)
            else [item.segments for item in block.items]
        )
        for segment in segments
        if isinstance(segment, FactSegment)
    }
    if not required_fact_ids.issubset(referenced_fact_ids):
        raise ValueError("response omitted facts required by its result contract")

    action_by_reference = {
        suggestion.reference: str((suggestion.action or {}).get("type") or "")
        for result in results
        for suggestion in result.availableSuggestions
    }
    forbidden_actions = {
        action for obligation in obligations for action in obligation.forbiddenSuggestionActions
    }
    if any(
        action_by_reference.get(reference) in forbidden_actions
        for reference in draft.suggestionReferences
    ):
        raise ValueError("response selected an action forbidden by its result contract")
    complete_suggestion_sets = [
        obligation.suggestionReferences
        for obligation in obligations
        if obligation.suggestionMode == "complete"
    ]
    for required_references in complete_suggestion_sets:
        if [
            reference
            for reference in draft.suggestionReferences
            if reference in required_references
        ] != required_references:
            raise ValueError("response omitted or reordered a complete suggestion set")

    if not any(obligation.kind == "vehicle_comparison" for obligation in obligations):
        return
    comparison_indices = [
        index for index, message in enumerate(draft.messages) if message.purpose == "comparison"
    ]
    if not comparison_indices:
        raise ValueError("vehicle comparison must use a comparison message")
    comparison_index = comparison_indices[-1]
    comparison = draft.messages[comparison_index]
    if len(comparison.blocks) != 1 or not isinstance(comparison.blocks[0], ParagraphBlock):
        raise ValueError("vehicle comparison must use one concise paragraph")
    comparison_fact_ids = {
        segment.factId
        for segment in comparison.blocks[0].segments
        if isinstance(segment, FactSegment)
    }
    comparison_obligation = next(
        obligation for obligation in obligations if obligation.kind == "vehicle_comparison"
    )
    compared_dimensions = 0
    for field in comparison_obligation.allowedComparisonFields:
        referenced_subjects = {
            fact.entityReference
            for result in results
            for fact in result.facts
            if fact.factId in comparison_fact_ids
            and fact.entityReference in comparison_obligation.subjectReferences
            and fact.field.casefold().endswith(f".{field.casefold()}")
        }
        if len(referenced_subjects) >= 2:
            compared_dimensions += 1
    if compared_dimensions < comparison_obligation.minimumComparedDimensions:
        raise ValueError("vehicle comparison paragraph omitted material differences")
    if not any(
        message.purpose in {"follow_up", "next_step"}
        for message in draft.messages[comparison_index + 1 :]
    ):
        raise ValueError("vehicle comparison must end with a separate follow-up message")


def _validate_tool_claim_boundaries(
    messages: tuple[ResolvedMessage, ...],
    results: list[ToolResultEnvelope] | tuple[ToolResultEnvelope, ...],
    *,
    workflow_state: dict,
) -> None:
    """Keep one domain's availability from being presented as another domain's fact."""

    response_text = " ".join(message.text for message in messages)
    obligations = [
        result.responseObligation for result in results if result.responseObligation is not None
    ]
    if (
        obligations
        and obligations[-1].kind in {"catalogue_results", "vehicle_comparison"}
        and _VAGUE_NEXT_STEP_QUESTION.search(messages[-1].text.strip())
    ):
        raise ValueError("catalogue and comparison follow-ups must ask a concrete question")
    if _INTERNAL_WORKFLOW_NARRATION.search(response_text):
        raise ValueError("customer response exposes internal workflow terminology")
    opaque_entity_identifiers = {
        entity.reference.split(":", 1)[1]
        for result in results
        for entity in result.entities
    }
    if any(
        re.search(rf"(?<![A-Za-z0-9_-]){re.escape(identifier)}(?![A-Za-z0-9_-])", response_text)
        for identifier in opaque_entity_identifiers
        if identifier
    ):
        raise ValueError("customer response exposes an operational entity identifier")
    has_prepared_capability = any(result.tool.startswith("prepare_") for result in results)
    if not has_prepared_capability and _PROTECTED_INPUT_REQUEST.search(response_text):
        raise ValueError("private protected inputs require a server-issued secure collector")
    has_workflow_progress = any(
        result.tool in _WORKFLOW_PROGRESS_TOOLS or result.tool.startswith("prepare_")
        for result in results
    )
    if not has_workflow_progress and _UNBACKED_PROGRESS_CLAIM.search(response_text):
        raise ValueError("operation progress requires a trusted tool result")
    entities = dict(workflow_state.get("entities") or {})
    if re.search(
        r"\b(?:you|i|we)(?:'ve| have)?\s+(?:selected|chosen|set)\b[^.?!]{0,100}"
        r"\b(?:option|car|vehicle|model)\b",
        response_text,
        re.IGNORECASE,
    ) and not entities.get("vehicleId"):
        raise ValueError("vehicle selection claim requires persisted selection evidence")
    if (
        workflow_state.get("activeWorkflow") == "test_drive"
        and not entities.get("vehicleId")
        and re.search(
            r"\b(?:what|which)\b[^?]{0,120}\b(?:day|date|time|morning|afternoon)\b[^?]*\?",
            response_text,
            re.IGNORECASE,
        )
    ):
        raise ValueError("test-drive schedule prompt requires a persisted vehicle selection")
    has_available_test_drive_slot = any(
        result.tool == "list_test_drive_slots" and result.status == "success" for result in results
    )
    if not has_available_test_drive_slot and _TEST_DRIVE_AVAILABILITY_CLAIM.search(response_text):
        raise ValueError("test-drive availability requires a trusted slot result")
    has_empty_appointment_result = any(
        result.tool
        in {"list_test_drive_slots", "list_workshop_slots", "refine_workshop_slots"}
        and result.status in {"empty", "unavailable"}
        for result in results
    )
    if (
        _NEGATIVE_APPOINTMENT_AVAILABILITY_CLAIM.search(response_text)
        and not has_empty_appointment_result
    ):
        raise ValueError("appointment unavailability requires a trusted empty result")
    transition_codes = {
        result.responseObligation.workflowCode
        for result in results
        if result.responseObligation is not None
        and result.responseObligation.kind == "workflow_transition"
    }
    if transition_codes.intersection(
        {"offer_location_alternatives", "offer_location_and_schedule_alternatives"}
    ) and _UNSUPPORTED_LOCATION_RANKING.search(response_text):
        raise ValueError("alternative dealership cannot be ranked without distance evidence")
    missing_public_fields = list(
        dict(workflow_state.get("constraints") or {}).get("missingPublicFields") or []
    )
    if transition_codes and missing_public_fields:
        final_message = messages[-1]
        if final_message.purpose != "workflow_prompt" or not final_message.text.rstrip().endswith(
            "?"
        ):
            raise ValueError("workflow transition must ask its persisted open question explicitly")
    if (
        any(
            result.responseObligation is not None
            and result.responseObligation.kind == "catalogue_results"
            for result in results
        )
        and not missing_public_fields
    ):
        final_message = messages[-1]
        if final_message.purpose not in {"follow_up", "next_step"} or not final_message.text.rstrip().endswith(
            "?"
        ):
            raise ValueError("catalogue result follow-up must be an explicit question")
    if transition_codes.intersection(
        {"offer_vehicle_alternatives", "choose_alternative_location", "offer_workshop_contact"}
    ) and re.search(
        r"\b(?:what|which|another|different)\b[^?]{0,100}\b(?:day|date|time)\b[^?]*\?",
        response_text,
        re.IGNORECASE,
    ):
        raise ValueError("this availability outcome cannot request schedule preferences")
    if transition_codes.intersection(
        {
            "offer_schedule_alternatives",
            "offer_location_alternatives",
            "offer_location_and_schedule_alternatives",
            "offer_equivalent_vehicle_slots",
        }
    ) and re.search(
        r"\b(?:what|which|another|different)\b[^?]{0,100}\b(?:day|date|location)\b[^?]*\?",
        response_text,
        re.IGNORECASE,
    ):
        raise ValueError("available same-target schedule alternatives require slot selection")
    unpublished_holiday_department = any(
        fact.field.endswith("holidayDepartment.status") and fact.value == "not_published"
        for result in results
        for fact in result.facts
    )
    if unpublished_holiday_department and re.search(
        r"\b(?:is|are|was|were)\s+closed\b", response_text, re.IGNORECASE
    ):
        raise ValueError("missing holiday hours cannot support a closure claim")
    finance_fields = {fact.field.rsplit(".", 1)[-1] for result in results for fact in result.facts}
    complete_finance_terms = {
        "monthlyPricePence",
        "upfrontPaymentPence",
        "termMonths",
        "feesPence",
        "finalPaymentPence",
    }.issubset(finance_fields)
    if (
        _FINANCE_TOTAL_REQUEST.search(response_text)
        and not complete_finance_terms
        and _UNSUPPORTED_FINANCE_TOTAL.search(response_text)
    ):
        raise ValueError("finance total requires complete trusted terms")


def _validate_answer_controls(
    messages: tuple[ResolvedMessage, ...],
    suggestions: tuple[AvailableSuggestion, ...],
    collections: tuple[AvailableCollection, ...],
) -> None:
    """A visible answer control is valid only when an explicit question owns it."""

    expects_answer = bool(suggestions) or any(
        collection.presentation.purpose in {"choice", "clarification"}
        for collection in collections
    )
    if not expects_answer:
        return
    final_message = messages[-1]
    if final_message.purpose not in {
        "clarification",
        "follow_up",
        "workflow_prompt",
        "next_step",
    } or not final_message.text.rstrip().endswith("?"):
        raise ValueError("visible answer controls require an explicit final question")


def _normalize_inline_segments(segments: list[dict]) -> list[dict]:
    """Apply one typography rule at the text/fact/link boundary used by every response."""

    normalized: list[dict] = []
    for segment in segments:
        field = "label" if segment["type"] == "link" else "text"
        value = re.sub(r"\s+", " ", str(segment[field])).strip()
        if not value:
            continue
        needs_space = bool(normalized) and not re.match(r"^[,.;:!?%)\]}]", value)
        if needs_space:
            if normalized[-1]["type"] == "text":
                normalized[-1] = {
                    **normalized[-1],
                    "text": f"{normalized[-1]['text']} ",
                }
            elif segment["type"] == "text":
                value = f" {value}"
            else:
                normalized.append({"type": "text", "text": " "})
        clean = dict(segment)
        clean[field] = value
        normalized.append(clean)
    return normalized


def _resolved_inline_text(segments: list[dict]) -> str:
    rendered: list[str] = []
    for segment in segments:
        if segment["type"] == "link":
            rendered.append(str(segment["label"]))
        else:
            rendered.append(str(segment["text"]))
    return "".join(rendered)


def _resolved_block_text(blocks: list[dict]) -> str:
    rendered: list[str] = []
    for block in blocks:
        if block["type"] == "paragraph":
            rendered.append(_resolved_inline_text(block["segments"]).strip())
            continue
        rendered.extend(
            f"- {_resolved_inline_text(item['segments']).strip()}" for item in block["items"]
        )
    return "\n".join(item for item in rendered if item).strip()


def normalize_incomplete_sentence_blocks(
    messages: tuple[ResolvedMessage, ...],
    *,
    preserve_visual_introductions: bool = False,
) -> tuple[ResolvedMessage, ...]:
    """Prune incomplete prose without turning a safe composed result into a failed turn."""

    normalized: list[ResolvedMessage] = []
    first_empty: ResolvedMessage | None = None
    for message in messages:
        blocks: list[dict] = []
        for index, block in enumerate(message.blocks):
            followed_by_inline_list = (
                index + 1 < len(message.blocks)
                and message.blocks[index + 1].get("type") == "list"
            )
            segments = block.get("segments", [])
            is_incomplete = (
                block.get("type") == "paragraph"
                and not followed_by_inline_list
                and segments
                and has_incomplete_sentence_ending(_resolved_inline_text(segments))
            )
            if is_incomplete and (
                message.collection_reference is not None or preserve_visual_introductions
            ):
                is_incomplete = False
            if not is_incomplete:
                blocks.append(block)
        if not blocks:
            first_empty = first_empty or message
            continue
        normalized.append(
            ResolvedMessage(
                purpose=message.purpose,
                text=_resolved_block_text(blocks),
                blocks=tuple(blocks),
                collection_reference=message.collection_reference,
                collection_rendered_in_blocks=message.collection_rendered_in_blocks,
            )
        )
    if normalized or first_empty is None:
        return tuple(normalized)
    blocks = (
        {
            "type": "paragraph",
            "segments": [
                {
                    "type": "text",
                    "text": "I couldn’t present that response clearly.",
                }
            ],
        },
    )
    return (
        ResolvedMessage(
            purpose=first_empty.purpose,
            text=_resolved_block_text(list(blocks)),
            blocks=blocks,
            collection_reference=first_empty.collection_reference,
            collection_rendered_in_blocks=first_empty.collection_rendered_in_blocks,
        ),
    )


def has_incomplete_sentence_ending(value: str) -> bool:
    """Recognize bounded syntactic evidence that a public paragraph stops mid-sentence."""

    return bool(_INCOMPLETE_SENTENCE_ENDING.search(str(value).rstrip()))


def normalize_explicit_card_owned_messages(
    messages: tuple[ResolvedMessage, ...],
    cards: list[AvailableCard] | tuple[AvailableCard, ...],
    results: list[ToolResultEnvelope] | tuple[ToolResultEnvelope, ...],
) -> tuple[ResolvedMessage, ...]:
    """Remove item facts already owned by a card without rejecting the composed answer.

    This runs only after the orchestrator has decided that the visual will actually be attached for
    an explicit result request. Comparison prose is intentionally exempt because its selected
    differences and the comparison table have complementary presentation roles.
    """

    selected = {card.reference for card in cards if card.type != "vehicle_comparison"}
    if not selected:
        return messages
    owned_fact_ids: set[str] = set()
    for result in results:
        owned_fields: set[str] = set()
        for card in result.availableCards:
            if card.reference in selected:
                owned_fields.update(_visual_leaf_fields(card.data))
        owned_fact_ids.update(
            fact.factId for fact in result.facts if fact.field in owned_fields
        )
    if not owned_fact_ids:
        return messages

    normalized: list[ResolvedMessage] = []
    first_empty: ResolvedMessage | None = None
    for message in messages:
        if message.purpose in _QUESTION_PURPOSES:
            normalized.append(message)
            continue
        blocks: list[dict] = []
        for block in message.blocks:
            if block["type"] == "paragraph":
                if not _segments_reference_any_fact(block["segments"], owned_fact_ids):
                    blocks.append(block)
                continue
            items = [
                item
                for item in block["items"]
                if not _segments_reference_any_fact(item["segments"], owned_fact_ids)
            ]
            if items:
                blocks.append({**block, "items": items})
        if not blocks:
            first_empty = first_empty or message
            continue
        normalized.append(
            ResolvedMessage(
                purpose=message.purpose,
                text=_resolved_block_text(blocks),
                blocks=tuple(blocks),
                collection_reference=message.collection_reference,
                collection_rendered_in_blocks=message.collection_rendered_in_blocks,
            )
        )
    if normalized or first_empty is None:
        return tuple(normalized)
    blocks = (
        {
            "type": "paragraph",
            "segments": [{"type": "text", "text": "Here are the requested details."}],
        },
    )
    return (
        ResolvedMessage(
            purpose=first_empty.purpose,
            text=_resolved_block_text(blocks),
            blocks=blocks,
            collection_reference=first_empty.collection_reference,
            collection_rendered_in_blocks=first_empty.collection_rendered_in_blocks,
        ),
    )


def _visual_leaf_fields(value, path: tuple[str, ...] = ()) -> set[str]:
    if isinstance(value, dict):
        fields: set[str] = set()
        for key, item in value.items():
            if key in {"collectionPresentationRendered", "optionNumber"}:
                continue
            fields.update(_visual_leaf_fields(item, (*path, str(key))))
        return fields
    if isinstance(value, list):
        fields: set[str] = set()
        for index, item in enumerate(value, start=1):
            fields.update(_visual_leaf_fields(item, (*path, str(index))))
        return fields
    return {".".join(path)} if path else set()


def _segments_reference_any_fact(segments: list[dict], fact_ids: set[str]) -> bool:
    return any(
        segment.get("type") == "fact" and segment.get("factId") in fact_ids
        for segment in segments
    )


def _unique_index(values, field: str) -> dict:
    result = {}
    for item in values:
        key = getattr(item, field)
        if key in result:
            raise ValueError(f"duplicate trusted reference: {key}")
        result[key] = item
    return result


def _require(values: dict, reference: str):
    try:
        return values[reference]
    except KeyError as error:
        raise ValueError(f"unknown or stale trusted reference: {reference}") from error
