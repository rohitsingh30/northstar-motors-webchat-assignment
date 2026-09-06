"""Strict contracts at the AI/application trust boundaries."""

from .facts import (
    AlternativeChange,
    AlternativeOffer,
    AtomicFact,
    AvailableCard,
    AvailableLink,
    AvailableSuggestion,
    ResultEntity,
    ToolResultEnvelope,
)
from .plan import (
    AgentPlan,
    Clarification,
    InteractionProposal,
    PlanIntent,
    PlannedToolCall,
    WorkflowStart,
)
from .presentation import CollectionDisplayItem, CollectionPresentation
from .response import (
    FactSegment,
    GroundedMessageDraft,
    GroundedResponseDraft,
    LinkSegment,
    ListBlock,
    ListItem,
    ParagraphBlock,
    TextSegment,
)

__all__ = [
    "AgentPlan",
    "AlternativeChange",
    "AlternativeOffer",
    "AtomicFact",
    "AvailableCard",
    "AvailableLink",
    "AvailableSuggestion",
    "Clarification",
    "CollectionDisplayItem",
    "CollectionPresentation",
    "FactSegment",
    "GroundedMessageDraft",
    "GroundedResponseDraft",
    "InteractionProposal",
    "LinkSegment",
    "ListBlock",
    "ListItem",
    "ParagraphBlock",
    "PlanIntent",
    "PlannedToolCall",
    "ResultEntity",
    "TextSegment",
    "ToolResultEnvelope",
    "WorkflowStart",
]
