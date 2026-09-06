"""Strict output contract for AI composition and resolved public presentation."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextSegment(StrictModel):
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=2_000)


class EmphasisSegment(StrictModel):
    """Short AI-authored choice or key phrase rendered with semantic emphasis."""

    type: Literal["emphasis"]
    text: str = Field(min_length=1, max_length=160)


class FactSegment(StrictModel):
    type: Literal["fact"]
    factId: str = Field(pattern=r"^fact-[A-Za-z0-9_.:-]{1,160}$")


class LinkSegment(StrictModel):
    type: Literal["link"]
    linkReference: str = Field(pattern=r"^link:[A-Za-z0-9_.:-]{1,180}$")


class BulletSegment(StrictModel):
    """Start one semantic list item in an assistant message."""

    type: Literal["bullet"]


class QuickReplyDraft(StrictModel):
    """A low-risk, AI-proposed possible customer reply with no executable authority."""

    label: str = Field(min_length=1, max_length=60)
    message: str = Field(min_length=1, max_length=240)


InlineSegment = Annotated[
    TextSegment | EmphasisSegment | FactSegment | LinkSegment,
    Field(discriminator="type"),
]


class ParagraphBlock(StrictModel):
    """One paragraph whose trusted facts and links remain inline with its prose."""

    type: Literal["paragraph"]
    segments: list[InlineSegment] = Field(min_length=1, max_length=40)


class ListItem(StrictModel):
    segments: list[InlineSegment] = Field(min_length=1, max_length=40)


class ListBlock(StrictModel):
    """A semantic unordered list explicitly chosen by the response composer."""

    type: Literal["list"]
    items: list[ListItem] = Field(min_length=1, max_length=12)


ResponseBlock = Annotated[ParagraphBlock | ListBlock, Field(discriminator="type")]


class GroundedMessageDraft(StrictModel):
    purpose: Literal[
        "answer",
        "comparison",
        "clarification",
        "transition",
        "follow_up",
        "workflow_prompt",
        "warning",
        "next_step",
    ]
    blocks: list[ResponseBlock] = Field(min_length=1, max_length=12)
    collectionReference: str | None = Field(
        default=None,
        pattern=r"^collection:[A-Za-z0-9_.:-]{1,180}$",
    )

    @model_validator(mode="before")
    @classmethod
    def upgrade_legacy_segments(cls, value: Any) -> Any:
        """Accept internal/test callers using the old stream while exposing only blocks to AI."""

        if not isinstance(value, dict) or "blocks" in value or "segments" not in value:
            return value
        upgraded = dict(value)
        upgraded["blocks"] = _legacy_segments_to_blocks(upgraded.pop("segments"))
        return upgraded


class GroundedResponseDraft(StrictModel):
    schemaVersion: Literal[1] = 1
    messages: list[GroundedMessageDraft] = Field(min_length=1, max_length=4)
    cardReferences: list[str] = Field(default_factory=list, max_length=100)
    suggestionReferences: list[str] = Field(default_factory=list, max_length=20)
    quickReplies: list[QuickReplyDraft] = Field(default_factory=list, max_length=4)
    linkReferences: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid_order_and_references(self) -> GroundedResponseDraft:
        # Repetition is a presentation defect, not a safety failure. Normalize it rather than
        # throwing away an otherwise valid dealership result and asking the customer to rephrase.
        self.cardReferences = list(dict.fromkeys(self.cardReferences))
        self.suggestionReferences = list(dict.fromkeys(self.suggestionReferences))
        self.linkReferences = list(dict.fromkeys(self.linkReferences))
        seen_labels: set[str] = set()
        seen_messages: set[str] = set()
        unique_quick_replies: list[QuickReplyDraft] = []
        for item in self.quickReplies:
            label = item.label.casefold()
            message = item.message.casefold()
            if label in seen_labels or message in seen_messages:
                continue
            seen_labels.add(label)
            seen_messages.add(message)
            unique_quick_replies.append(item)
        self.quickReplies = unique_quick_replies
        segment_links = {
            segment.linkReference
            for message in self.messages
            for block in message.blocks
            for segments in (
                [block.segments]
                if isinstance(block, ParagraphBlock)
                else [item.segments for item in block.items]
            )
            for segment in segments
            if isinstance(segment, LinkSegment)
        }
        self.linkReferences = list(dict.fromkeys([*self.linkReferences, *segment_links]))
        return self


def _legacy_segments_to_blocks(segments: Any) -> list[dict[str, Any]]:
    """Compile the former bullet-marker stream into explicit paragraph/list blocks."""

    if not isinstance(segments, list):
        return []
    blocks: list[dict[str, Any]] = []
    paragraph: list[Any] = []
    list_items: list[dict[str, Any]] = []
    current_item: list[Any] | None = None

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append({"type": "paragraph", "segments": list(paragraph)})
            paragraph.clear()

    def flush_list() -> None:
        nonlocal current_item
        if current_item:
            list_items.append({"segments": current_item})
        current_item = None
        if list_items:
            blocks.append({"type": "list", "items": list(list_items)})
            list_items.clear()

    for segment in segments:
        segment_type = (
            segment.get("type") if isinstance(segment, dict) else getattr(segment, "type", None)
        )
        if segment_type == "bullet":
            flush_paragraph()
            if current_item:
                list_items.append({"segments": current_item})
            current_item = []
            continue
        if current_item is not None:
            current_item.append(segment)
        else:
            paragraph.append(segment)
    flush_paragraph()
    flush_list()
    return blocks
