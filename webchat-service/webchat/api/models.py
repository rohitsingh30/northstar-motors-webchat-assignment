from __future__ import annotations

import re
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from webchat.domain.turn_actions import TurnAction


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PageControl(StrictModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_.:-]*$")
    label: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=200)


class PageEntity(StrictModel):
    type: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9-]*$")
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    label: str | None = Field(default=None, max_length=200)
    attributes: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def bounded_attributes(cls, value):
        if len(value) > 24:
            raise ValueError("page entities support at most 24 attributes")
        for key, item in value.items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]{0,79}", key):
                raise ValueError("page entity attribute names must be safe identifiers")
            if isinstance(item, str) and len(item) > 240:
                raise ValueError("page entity attribute values are too long")
        return value


class PageContext(StrictModel):
    path: str = Field(min_length=1, max_length=500)
    section: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9-]*$")
    vehicleId: str | None = None
    title: str = Field(min_length=1, max_length=120)
    heading: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    pageText: str | None = Field(default=None, max_length=6_000)
    dialogText: str | None = Field(default=None, max_length=3_000)
    controls: list[PageControl] = Field(default_factory=list, max_length=30)
    entities: list[PageEntity] = Field(default_factory=list, max_length=50)

    @field_validator("path")
    @classmethod
    def local_path(cls, value: str) -> str:
        if not value.startswith("/") or value.startswith("//"):
            raise ValueError("path must be a local absolute path")
        return value

    @field_validator("vehicleId")
    @classmethod
    def vehicle_id(cls, value: str | None) -> str | None:
        if value is not None and (
            len(value) != 7 or not value.startswith("veh-") or not value[4:].isdigit()
        ):
            raise ValueError("vehicleId must match veh-000")
        return value


class CreateConversationRequest(StrictModel):
    pageContext: PageContext


class SendTurnRequest(StrictModel):
    clientMessageId: UUID
    text: str = Field(min_length=1, max_length=4000)
    pageContext: PageContext
    action: TurnAction | None = None

    @field_validator("text")
    @classmethod
    def non_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("text must not be blank")
        return cleaned


class ConfirmDraftRequest(StrictModel):
    clientActionId: UUID
    expectedKind: Literal[
        "sales_enquiry",
        "test_drive",
        "vehicle_interest",
        "callback",
        "workshop_booking",
        "workshop_amend",
        "workshop_cancel",
        "dealership_message",
        "part_exchange",
    ] | None = None


class TestDriveOptionsRequest(StrictModel):
    vehicleId: str = Field(pattern=r"^veh-[0-9]{3}$")


class OfferEnquiryOptionsRequest(StrictModel):
    offerId: str = Field(pattern=r"^offer-[0-9]{2}$")


class WorkshopOptionsRequest(StrictModel):
    serviceTypeId: str = Field(min_length=1, max_length=80)
    dealershipId: str | None = Field(default=None, max_length=80)


class ContactRequest(StrictModel):
    firstName: str = Field(max_length=100)
    lastName: str = Field(max_length=100)
    email: str = Field(max_length=254)
    phone: str = Field(max_length=30)

    @field_validator("firstName", "lastName")
    @classmethod
    def valid_name(cls, value: str, info) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2 or any(character.isdigit() for character in cleaned):
            label = "first name" if info.field_name == "firstName" else "last name"
            raise ValueError(f"Enter a valid {label}.")
        return cleaned

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        cleaned = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", cleaned):
            raise ValueError("Enter a complete email address.")
        return cleaned

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, value: str) -> str:
        cleaned = re.sub(r"[\s().-]", "", value.strip())
        if cleaned.startswith("+44"):
            cleaned = f"0{cleaned[3:]}"
        if not re.fullmatch(r"0(?:1|7)\d{8,9}", cleaned):
            raise ValueError("Enter a UK 01 landline or 07 mobile number.")
        return cleaned


class TestDriveDraftRequest(ContactRequest):
    slotId: str = Field(pattern=r"^td-slot-[0-9]{4}$")
    vehicleId: str = Field(pattern=r"^veh-[0-9]{3}$")


class WorkshopDraftRequest(ContactRequest):
    slotId: str = Field(pattern=r"^ws-slot-[0-9]{4}$")
    serviceTypeId: str = Field(min_length=1, max_length=80)
    dealershipId: str = Field(min_length=1, max_length=80)
    registration: str = Field(min_length=2, max_length=20)
    mileage: int = Field(ge=0, le=2_000_000)
    notes: str | None = Field(default=None, max_length=1_000)


class PartExchangeDraftRequest(ContactRequest):
    dealershipId: str = Field(min_length=1, max_length=80)
    registration: str = Field(min_length=2, max_length=12)
    mileage: int = Field(ge=0, le=2_000_000)
    condition: Literal["excellent", "good", "fair"]


class PartExchangeEstimateRequest(StrictModel):
    registration: str = Field(min_length=2, max_length=12)
    mileage: int = Field(ge=0, le=1_000_000)
    condition: Literal["excellent", "good", "fair"]


class CallbackDraftRequest(ContactRequest):
    dealershipId: str = Field(min_length=1, max_length=80)
    department: Literal["sales", "service", "parts"]
    reason: str = Field(min_length=5, max_length=1_000)
    vehicleId: str | None = Field(default=None, pattern=r"^veh-[0-9]{3}$")
    preferredTime: str | None = Field(default=None, max_length=120)


class SalesEnquiryDraftRequest(ContactRequest):
    dealershipId: str = Field(min_length=1, max_length=80)
    enquiryType: Literal["general", "availability", "finance", "part-exchange"]
    message: str = Field(min_length=5, max_length=2_000)
    vehicleId: str | None = Field(default=None, pattern=r"^veh-[0-9]{3}$")


class VehicleInterestDraftRequest(ContactRequest):
    vehicleId: str = Field(pattern=r"^veh-[0-9]{3}$")
    notes: str | None = Field(default=None, max_length=1_000)


class DealershipMessageDraftRequest(ContactRequest):
    dealershipId: str = Field(min_length=1, max_length=80)
    department: Literal["sales", "service", "parts", "general"]
    subject: str = Field(min_length=3, max_length=120)
    message: str = Field(min_length=5, max_length=2_000)
    preferredContactMethod: Literal["email", "phone"]


class WorkshopAmendDraftRequest(StrictModel):
    slotId: str | None = Field(default=None, pattern=r"^ws-slot-[0-9]{4}$")
    mileage: int | None = Field(default=None, ge=0, le=2_000_000)
    notes: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def has_change(self):
        if self.slotId is None and self.mileage is None and not str(self.notes or "").strip():
            raise ValueError("Enter a new mileage, notes, or appointment time.")
        return self


class BookingLookupRequest(StrictModel):
    reference: str = Field(min_length=3, max_length=80)
    lastName: str = Field(min_length=1, max_length=100)
    registration: str = Field(min_length=2, max_length=20)
    phone: str = Field(min_length=7, max_length=30)
    mode: Literal["lookup", "amend", "cancel"] = "lookup"


class WorkshopExistingActionRequest(StrictModel):
    mode: Literal["amend", "cancel"]
    bookingReference: str | None = Field(default=None, min_length=3, max_length=80)


AssistantMode = Literal["hosted", "limited_demo"]


class PublicTextSegment(StrictModel):
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=8_000)


class PublicEmphasisSegment(StrictModel):
    type: Literal["emphasis"]
    text: str = Field(min_length=1, max_length=160)


class PublicFactSegment(StrictModel):
    type: Literal["fact"]
    factId: str = Field(pattern=r"^fact-[A-Za-z0-9_.:-]{1,160}$")
    text: str = Field(min_length=1, max_length=8_000)


class PublicLinkSegment(StrictModel):
    type: Literal["link"]
    label: str = Field(min_length=1, max_length=160)
    href: str = Field(min_length=1, max_length=1_000)
    destinationKind: Literal["telephone", "email", "directions", "website"]


class PublicBulletSegment(StrictModel):
    type: Literal["bullet"]


PublicInlineSegment = (
    PublicTextSegment | PublicEmphasisSegment | PublicFactSegment | PublicLinkSegment
)


class PublicParagraphBlock(StrictModel):
    type: Literal["paragraph"]
    segments: list[PublicInlineSegment] = Field(min_length=1, max_length=40)


class PublicListItem(StrictModel):
    segments: list[PublicInlineSegment] = Field(min_length=1, max_length=40)


class PublicListBlock(StrictModel):
    type: Literal["list"]
    items: list[PublicListItem] = Field(min_length=1, max_length=12)


class PublicMessage(StrictModel):
    id: str
    turnId: str | None = None
    role: Literal["user", "assistant"]
    text: str = Field(max_length=8_000)
    createdAt: str
    purpose: str | None = None
    viewType: str | None = None
    view: dict[str, Any] | None = None
    segments: (
        list[
            PublicTextSegment
            | PublicEmphasisSegment
            | PublicFactSegment
            | PublicLinkSegment
            | PublicBulletSegment
        ]
        | None
    ) = Field(default=None, max_length=40)
    blocks: list[PublicParagraphBlock | PublicListBlock] | None = Field(
        default=None,
        max_length=12,
    )


class CreateConversationResponse(StrictModel):
    conversationId: UUID
    createdAt: str
    assistantMode: AssistantMode
    messages: list[PublicMessage]


class RestoreConversationResponse(StrictModel):
    conversationId: UUID
    assistantMode: AssistantMode
    messages: list[PublicMessage]


class WorkflowActivationResponse(StrictModel):
    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{1,79}$")
    status: Literal["collecting"]
    activation: dict[str, Any]


class PendingInteractionResponse(StrictModel):
    interactionId: str
    kind: str
    trustedEntity: dict[str, Any] | None = None
    originatingMessageId: str
    createdAtStateVersion: int = Field(ge=0)
    status: str
    activeDraftId: str | None = None
    workflowKind: str | None = None
    supersededByInteractionId: str | None = None
    supersededAtTurnId: str | None = None


class OpenVehicleDetailAction(StrictModel):
    actionId: str
    type: Literal["open_vehicle_detail"]
    interactionId: str
    vehicleId: str = Field(pattern=r"^veh-[0-9]{3}$")
    sameSiteUrl: str = Field(pattern=r"^/\?vehicle=veh-[0-9]{3}$")

    @model_validator(mode="after")
    def target_matches_vehicle(self):
        if self.sameSiteUrl != f"/?vehicle={self.vehicleId}":
            raise ValueError("client action URL must match its trusted vehicle")
        return self


class TurnErrorResponse(StrictModel):
    code: str
    message: str
    retryable: bool


class SendTurnResponse(StrictModel):
    schemaVersion: Literal[1]
    turnId: UUID
    status: Literal["completed", "failed", "processing"]
    stateVersion: int = Field(ge=0)
    assistantMode: AssistantMode
    messages: list[PublicMessage]
    cards: list[dict[str, Any]]
    quickReplies: list[dict[str, Any]]
    workflow: WorkflowActivationResponse | None
    pendingInteraction: PendingInteractionResponse | None
    clientActions: list[OpenVehicleDetailAction]
    error: TurnErrorResponse | None
