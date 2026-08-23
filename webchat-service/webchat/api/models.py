from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class TurnAction(StrictModel):
    type: Literal[
        "select_test_drive_vehicle",
        "next_vehicle_page",
        "search_vehicle_inventory",
        "compare_displayed_vehicles",
        "select_workshop_service",
        "start_dealership_workshop",
        "try_workshop_location",
        "show_workshop_services",
        "start_vehicle_interest",
        "start_sales_enquiry",
        "start_offer_enquiry",
        "view_offer",
        "apply_vehicle_preference",
        "choose_vehicle_filter",
        "clear_vehicle_filter",
        "reset_vehicle_search",
        "start_callback",
        "start_dealership_message",
        "show_dealerships",
        "show_opening_hours",
        "show_dealership_contact_options",
    ]
    vehicleId: str | None = Field(default=None, pattern=r"^veh-[0-9]{3}$")
    serviceTypeId: str | None = Field(default=None, min_length=1, max_length=80)
    dealershipId: str | None = Field(default=None, min_length=1, max_length=80)
    offerId: str | None = Field(default=None, min_length=1, max_length=80)
    vehicleFilter: (
        Literal[
            "make",
            "model",
            "fuelType",
            "transmission",
            "bodyStyle",
            "maxPricePence",
            "maxMileage",
        ]
        | None
    ) = None
    vehicleFilterValue: str | int | None = None

    @model_validator(mode="after")
    def matching_identifier(self):
        identifiers = {
            "vehicleId": self.vehicleId,
            "serviceTypeId": self.serviceTypeId,
            "dealershipId": self.dealershipId,
        }
        required = {
            "select_test_drive_vehicle": {"vehicleId"},
            "start_vehicle_interest": {"vehicleId"},
            "start_sales_enquiry": {"vehicleId"},
            "start_offer_enquiry": {"offerId"},
            "view_offer": {"offerId"},
            "select_workshop_service": {"serviceTypeId"},
            "start_dealership_workshop": {"dealershipId"},
            "try_workshop_location": {"serviceTypeId", "dealershipId"},
            "next_vehicle_page": set(),
            "search_vehicle_inventory": set(),
            "compare_displayed_vehicles": set(),
            "show_workshop_services": set(),
            "apply_vehicle_preference": {"vehicleFilter", "vehicleFilterValue"},
            "choose_vehicle_filter": {"vehicleFilter"},
            "clear_vehicle_filter": {"vehicleFilter"},
            "reset_vehicle_search": set(),
            "start_callback": set(),
            "start_dealership_message": set(),
            "show_dealerships": set(),
            "show_opening_hours": set(),
            "show_dealership_contact_options": set(),
        }[self.type]
        identifiers.update(
            offerId=self.offerId,
            vehicleFilter=self.vehicleFilter,
            vehicleFilterValue=self.vehicleFilterValue,
        )
        provided = {key for key, value in identifiers.items() if value is not None}
        if provided != required:
            labels = ", ".join(sorted(required)) or "no identifiers"
            raise ValueError(f"{self.type} requires {labels}")
        if self.type == "apply_vehicle_preference":
            numeric = self.vehicleFilter in {"maxPricePence", "maxMileage"}
            if numeric and (
                isinstance(self.vehicleFilterValue, bool)
                or not isinstance(self.vehicleFilterValue, int)
            ):
                raise ValueError(f"{self.vehicleFilter} requires an integer value")
            if not numeric and not str(self.vehicleFilterValue or "").strip():
                raise ValueError(f"{self.vehicleFilter} requires a text value")
        return self


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
