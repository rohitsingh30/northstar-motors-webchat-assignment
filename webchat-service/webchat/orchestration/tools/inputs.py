"""Strict input models for allow-listed business tools."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyInput(ToolInput):
    pass


VehicleExclusionValues = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=80)]],
    Field(max_length=20),
]


class VehicleSearch(ToolInput):
    page: int = Field(default=1, ge=1, le=100)
    q: str | None = Field(default=None, max_length=100)
    make: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    fuelType: str | None = Field(default=None, max_length=40)
    transmission: str | None = Field(default=None, max_length=40)
    bodyStyle: str | None = Field(default=None, max_length=40)
    excludedMakes: VehicleExclusionValues | None = None
    excludedModels: VehicleExclusionValues | None = None
    excludedFuelTypes: VehicleExclusionValues | None = None
    excludedTransmissions: VehicleExclusionValues | None = None
    excludedBodyStyles: VehicleExclusionValues | None = None
    availability: Literal["available", "reserved", "sold"] | None = None
    dealershipId: str | None = Field(default=None, max_length=80)
    dealershipTown: str | None = Field(default=None, max_length=80)
    minPricePence: int | None = Field(default=None, ge=0)
    maxPricePence: int | None = Field(default=None, ge=0)
    maxMileage: int | None = Field(default=None, ge=0)
    minYear: int | None = Field(default=None, ge=1900, le=2100)
    sort: Literal["newest", "priceAsc", "priceDesc", "mileageAsc"] = "newest"


class PageVehicleSelection(ToolInput):
    vehicleIds: list[str] = Field(min_length=1, max_length=50)
    q: str | None = Field(default=None, max_length=100)
    make: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    fuelType: str | None = Field(default=None, max_length=40)
    transmission: str | None = Field(default=None, max_length=40)
    bodyStyle: str | None = Field(default=None, max_length=40)
    excludedMakes: VehicleExclusionValues | None = None
    excludedModels: VehicleExclusionValues | None = None
    excludedFuelTypes: VehicleExclusionValues | None = None
    excludedTransmissions: VehicleExclusionValues | None = None
    excludedBodyStyles: VehicleExclusionValues | None = None
    availability: Literal["available", "reserved", "sold"] | None = None
    dealershipId: str | None = Field(default=None, max_length=80)
    dealershipTown: str | None = Field(default=None, max_length=80)
    minPricePence: int | None = Field(default=None, ge=0)
    maxPricePence: int | None = Field(default=None, ge=0)
    maxMileage: int | None = Field(default=None, ge=0)
    minYear: int | None = Field(default=None, ge=1900, le=2100)
    sort: Literal["newest", "priceAsc", "priceDesc", "mileageAsc"] = "newest"
    limit: int = Field(default=3, ge=1, le=12)

    @field_validator("vehicleIds")
    @classmethod
    def valid_unique_vehicle_ids(cls, values):
        if any(not re.fullmatch(r"veh-[0-9]{3}", value) for value in values):
            raise ValueError("invalid vehicle ID")
        return list(dict.fromkeys(values))


class StableId(ToolInput):
    id: str = Field(max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class VehicleComparison(ToolInput):
    vehicleIds: list[str] = Field(min_length=2, max_length=3)


class VehicleModelComparison(ToolInput):
    """Natural-language comparison requests resolved against live stock."""

    queries: list[str] = Field(min_length=2, max_length=3)


class Filters(ToolInput):
    dealershipId: str | None = Field(default=None, max_length=80)
    vehicleId: str | None = Field(default=None, pattern=r"^veh-[0-9]{3}$")
    serviceTypeId: str | None = Field(default=None, max_length=80)
    serviceTypeName: str | None = Field(default=None, max_length=80)
    dealershipTown: str | None = Field(default=None, max_length=80)
    dateFrom: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    dateTo: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    make: str | None = Field(default=None, max_length=60)
    productType: Literal["PCP", "PCH"] | None = None
    workflowMode: Literal["booking", "amendment"] | None = None


class PartExchangeEstimate(ToolInput):
    registration: str = Field(min_length=2, max_length=12)
    mileage: int = Field(ge=0, le=1_000_000)
    condition: Literal["excellent", "good", "fair"]


class PartExchangeEstimateForm(ToolInput):
    registration: str | None = Field(default=None, min_length=2, max_length=12)
    mileage: int | None = Field(default=None, ge=0, le=1_000_000)
    condition: Literal["excellent", "good", "fair"] | None = None


class OpeningHoursQuery(ToolInput):
    day: (
        Literal[
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        | None
    ) = None
    dealershipId: str | None = Field(default=None, max_length=80)
    town: str | None = Field(default=None, max_length=80)
    department: Literal["sales", "service", "parts"] | None = None


class DealershipQuery(ToolInput):
    town: str | None = Field(default=None, max_length=80)


class DealershipScopeQuery(ToolInput):
    dealershipId: str | None = Field(default=None, max_length=80)
    town: str | None = Field(default=None, max_length=80)


class BusinessInformationQuery(ToolInput):
    topic: Literal["finance", "privacy", "part_exchange", "general"]
    question: str = Field(min_length=1, max_length=4_000)


class ServiceQuery(ToolInput):
    q: str | None = Field(default=None, min_length=1, max_length=200)
    serviceTypeId: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def has_service_reference(self):
        if self.q is None and self.serviceTypeId is None:
            raise ValueError("a service query or service type ID is required")
        return self


class BookingLookupForm(ToolInput):
    mode: Literal["lookup", "amend", "cancel"] = "lookup"


class VehiclePreferenceRequest(ToolInput):
    reuseCurrentSearch: bool = False
    dimension: Literal[
        "startingPoint",
        "budgets",
        "mileages",
        "makes",
        "models",
        "fuelTypes",
        "transmissions",
        "bodyStyles",
    ] = "startingPoint"


class WorkflowDraftInput(ToolInput):
    """Fields that may prefill an application-owned draft; missing fields stay in the form."""

    slotId: str | None = Field(default=None, max_length=80)
    vehicleId: str | None = Field(default=None, pattern=r"^veh-[0-9]{3}$")
    serviceTypeId: str | None = Field(default=None, max_length=80)
    dealershipId: str | None = Field(default=None, max_length=80)
    dealershipTown: str | None = Field(default=None, max_length=80)
    verifiedGrantId: str | None = Field(default=None, max_length=120)
    enquiryType: Literal["general", "availability", "finance", "part-exchange"] | None = Field(
        default=None,
        description="Prefill only when the customer's enquiry type is explicit or context-resolved.",
    )
    department: Literal["sales", "service", "parts", "general"] | None = None
    preferredContactMethod: Literal["email", "phone"] | None = Field(
        default=None,
        description="Prefill only when the customer expressed this reply preference.",
    )
    condition: Literal["excellent", "good", "fair"] | None = None
    registration: str | None = Field(default=None, max_length=20)
    mileage: int | None = Field(default=None, ge=0, le=2_000_000)
    firstName: str | None = Field(default=None, max_length=100)
    lastName: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=30)
    subject: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Customer-authored subject content; omit when the customer only requested a workflow."
        ),
    )
    message: str | None = Field(
        default=None,
        max_length=2_000,
        description=(
            "Relevant customer-authored message content; never copy the workflow request itself."
        ),
    )
    notes: str | None = Field(
        default=None,
        max_length=1_000,
        description="Relevant customer-authored notes; omit when no notes were supplied.",
    )
    reason: str | None = Field(
        default=None,
        max_length=1_000,
        description="The customer's stated reason, not their generic request to start the workflow.",
    )
    preferredTime: str | None = Field(
        default=None,
        max_length=120,
        description="A callback time preference explicitly supplied by the customer.",
    )


class WorkshopAmendmentInput(ToolInput):
    """Customer-editable fields for an existing workshop booking."""

    mileage: int | None = Field(default=None, ge=0, le=2_000_000)
    notes: str | None = Field(
        default=None,
        max_length=1_000,
        description="Relevant customer-authored notes; omit when no notes were supplied.",
    )


class WorkshopAmendmentSlotContext(ToolInput):
    """Live slot metadata resolved by the application, never by the model."""

    slotId: str = Field(pattern=r"^ws-slot-[0-9]{4}$")
    selectedStartsAt: str = Field(min_length=1, max_length=80)
    selectedDealershipId: str = Field(min_length=1, max_length=80)
    selectedDealershipName: str = Field(min_length=1, max_length=160)
    selectedServiceTypeId: str = Field(min_length=1, max_length=80)
    selectedServiceName: str = Field(min_length=1, max_length=160)


class DealershipMessageDraftInput(ToolInput):
    """Only fields that can legitimately prefill the dealership-message form."""

    dealershipId: str | None = Field(default=None, max_length=80)
    dealershipTown: str | None = Field(default=None, max_length=80)
    department: Literal["sales", "service", "parts", "general"] | None = None
    subject: str | None = Field(
        default=None,
        max_length=120,
        description=(
            "A subject supported by relevant customer-authored conversation context. Omit it for "
            "a generic request to open, start, send, or leave a dealership message."
        ),
    )
    message: str | None = Field(
        default=None,
        max_length=2_000,
        description=(
            "The actual content the customer wants delivered, copied or faithfully summarized "
            "from conversation context. Workflow intent such as asking to send or leave a message "
            "is not message content; omit this field when no deliverable content was supplied."
        ),
    )
    preferredContactMethod: Literal["email", "phone"] | None = Field(
        default=None,
        description="Prefill only when the customer expressed this reply preference.",
    )
    firstName: str | None = Field(default=None, max_length=100)
    lastName: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=30)
