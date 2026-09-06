"""Typed output of the conversational-understanding phase.

The model resolves meaning; application code validates only references, state relationships, and
the closed dialogue vocabulary.  No customer phrase is interpreted in this module.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .plan import IntentKind, StrictModel

DialogueAct = Literal[
    "start_goal",
    "continue_goal",
    "answer_open_question",
    "modify_goal",
    "switch_goal",
    "interrupt_with_information_request",
    "resume_goal",
    "cancel_goal",
    "accept",
    "decline",
    "social",
]

GoalRelation = Literal[
    "new",
    "active",
    "paused",
    "open_question",
    "unrelated",
    "none",
]

AmbiguityKind = Literal["none", "intent", "reference", "required_input"]
ResultPresentation = Literal["default", "explicit_request"]
IntentStructure = Literal[
    "single_outcome",
    "compound_outcomes",
    "uncertain_intent",
]

REFERENCE_FIELD_NAMESPACES = {
    "vehicleId": "vehicle",
    "offerId": "offer",
    "dealershipId": "dealership",
    "serviceTypeId": "service",
    "slotId": "appointment",
}


class ResolvedInput(StrictModel):
    """One customer-authored value with auditable transcript provenance."""

    field: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9]{0,79}$")
    value: str | int | float | bool | list[str] | list[int]
    sourceContextId: str = Field(pattern=r"^message:[A-Za-z0-9_-]{1,100}$")
    sourceText: str = Field(min_length=1, max_length=500)


class RequestedInputChange(StrictModel):
    """One customer-requested field edit, without claiming a replacement value."""

    field: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9]{0,79}$")
    sourceContextId: str = Field(pattern=r"^message:[A-Za-z0-9_-]{1,100}$")
    sourceText: str = Field(min_length=1, max_length=500)


class TurnUnderstanding(StrictModel):
    """The AI's explicit interpretation of one customer turn.

    `supportingContextIds` makes the interpretation auditable.  Those IDs identify application
    context records, not arbitrary snippets invented by the model.
    """

    schemaVersion: Literal[1] = 1
    dialogueAct: DialogueAct
    goalRelation: GoalRelation
    intentStructure: IntentStructure
    intentKinds: list[IntentKind] = Field(default_factory=list, max_length=4)
    resultPresentation: ResultPresentation = "default"
    answeredQuestionId: str | None = Field(default=None, pattern=r"^question-[A-Za-z0-9_-]{1,64}$")
    resolvedReferences: list[str] = Field(default_factory=list, max_length=12)
    referenceCandidates: list[str] = Field(default_factory=list, max_length=12)
    resolvedInputs: list[ResolvedInput] = Field(default_factory=list, max_length=24)
    requestedInputChanges: list[RequestedInputChange] = Field(default_factory=list, max_length=8)
    ambiguity: AmbiguityKind = "none"
    confidence: Literal["high", "medium", "low"]
    supportingContextIds: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="before")
    @classmethod
    def migrate_additive_intent_structure(cls, value):
        """Read resolutions written before intent structure became explicit.

        Hosted output must provide this required field. This structural migration exists only for
        already-persisted state and in-process integrations using the earlier additive contract;
        it does not inspect or classify customer language.
        """

        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        if "referenceCandidates" not in migrated:
            if migrated.get("ambiguity") == "reference":
                migrated["referenceCandidates"] = list(migrated.get("resolvedReferences") or [])
                migrated["resolvedReferences"] = []
            else:
                migrated["referenceCandidates"] = []
        # `referenceBasis` briefly existed as a turn-wide provider field. It could not represent a
        # compound turn containing both a contextual and a described entity, and rejecting a
        # provider's mislabeled basis discarded otherwise valid finite/deictic resolutions. Drop it
        # on read; provenance is already represented per input and by the persisted open question.
        migrated.pop("referenceBasis", None)
        previous_structure = migrated.get("intentStructure")
        previous_names = {
            "single": "single_outcome",
            "compound": "compound_outcomes",
            "alternatives": "uncertain_intent",
        }
        if previous_structure in previous_names:
            migrated["intentStructure"] = previous_names[previous_structure]
            return migrated
        if "intentStructure" in migrated:
            return migrated
        intents = migrated.get("intentKinds") or []
        if migrated.get("ambiguity") == "intent" and len(intents) >= 2:
            migrated["intentStructure"] = "uncertain_intent"
        elif len(intents) >= 2:
            migrated["intentStructure"] = "compound_outcomes"
        else:
            migrated["intentStructure"] = "single_outcome"
        return migrated

    @model_validator(mode="after")
    def coherent_dialogue_relationship(self) -> TurnUnderstanding:
        answers = self.dialogueAct == "answer_open_question"
        if answers != bool(self.answeredQuestionId):
            raise ValueError("answer_open_question requires exactly one answeredQuestionId")
        if self.ambiguity != "none" and self.confidence == "high":
            raise ValueError("an ambiguous interpretation cannot have high confidence")
        if len(self.intentKinds) != len(set(self.intentKinds)):
            raise ValueError("intentKinds must be unique")
        if self.intentStructure == "single_outcome" and len(self.intentKinds) > 1:
            raise ValueError("single intent structure permits at most one intent")
        if self.intentStructure == "compound_outcomes" and len(self.intentKinds) < 2:
            raise ValueError("compound intent structure requires multiple requested outcomes")
        if self.intentStructure == "uncertain_intent" and (
            self.ambiguity != "intent" or len(self.intentKinds) < 2
        ):
            raise ValueError("intent alternatives require multiple ambiguous interpretations")
        if self.ambiguity == "intent" and self.intentStructure != "uncertain_intent":
            raise ValueError("intent ambiguity must be represented as alternatives")
        if self.ambiguity != "intent" and self.intentStructure == "uncertain_intent":
            raise ValueError("intent alternatives require intent ambiguity")
        if len(self.resolvedReferences) != len(set(self.resolvedReferences)):
            raise ValueError("resolvedReferences must be unique")
        if len(self.referenceCandidates) != len(set(self.referenceCandidates)):
            raise ValueError("referenceCandidates must be unique")
        if set(self.resolvedReferences).intersection(self.referenceCandidates):
            raise ValueError("resolved references and ambiguous candidates must be disjoint")
        if self.ambiguity == "reference" and len(self.referenceCandidates) < 2:
            raise ValueError("reference ambiguity requires multiple candidates")
        if self.ambiguity != "reference" and self.referenceCandidates:
            raise ValueError("reference candidates require reference ambiguity")
        fields = [item.field for item in self.resolvedInputs]
        if len(fields) != len(set(fields)):
            raise ValueError("resolvedInputs fields must be unique")
        changed_fields = [item.field for item in self.requestedInputChanges]
        if len(changed_fields) != len(set(changed_fields)):
            raise ValueError("requested input changes must be unique")
        if len(self.supportingContextIds) != len(set(self.supportingContextIds)):
            raise ValueError("supportingContextIds must be unique")
        return self
