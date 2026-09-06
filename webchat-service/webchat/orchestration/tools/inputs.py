"""Strict input models for allow-listed business tools."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from webchat.domain.interactions import ActionHandoff


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyInput(ToolInput):
    pass


VehicleExclusionValues = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=80)]],
    Field(max_length=20),
]

VehicleInclusionValues = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=80)]],
    Field(min_length=1, max_length=20),
]


class VehicleSearch(ToolInput):
    page: int = Field(default=1, ge=1, le=100)
    q: str | None = Field(
        default=None,
        max_length=100,
        description=(
            "Free-text vehicle identity constraint only for words naming or describing make, "
            "model, variant, or colour, including misspellings. Do not use as a catch-all for "
            "price, mileage, year, fuel, transmission, body style, availability, location, or "
            "result ordering; those have dedicated fields."
        ),
    )
    make: str | None = Field(default=None, max_length=60)
    makes: VehicleInclusionValues | None = None
    model: str | None = Field(default=None, max_length=60)
    models: VehicleInclusionValues | None = None
    colour: str | None = Field(default=None, max_length=60)
    colours: VehicleInclusionValues | None = None
    fuelType: str | None = Field(default=None, max_length=40)
    fuelTypes: VehicleInclusionValues | None = None
    transmission: str | None = Field(default=None, max_length=40)
    transmissions: VehicleInclusionValues | None = None
    bodyStyle: str | None = Field(default=None, max_length=40)
    bodyStyles: VehicleInclusionValues | None = None
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
    minMileage: int | None = Field(default=None, ge=0)
    maxMileage: int | None = Field(default=None, ge=0)
    minYear: int | None = Field(default=None, ge=1900, le=2100)
    maxYear: int | None = Field(default=None, ge=1900, le=2100)
    sort: Literal[
        "newest", "priceAsc", "priceDesc", "mileageAsc", "mileageDesc"
    ] = Field(
        default="newest",
        description=(
            "Requested result order: newest orders by model year, priceAsc by lowest price, "
            "priceDesc by highest price, mileageAsc by lowest mileage, and mileageDesc by "
            "highest mileage."
        ),
    )

    @model_validator(mode="after")
    def ordered_ranges(self):
        for lower, upper, label in (
            (self.minPricePence, self.maxPricePence, "price"),
            (self.minMileage, self.maxMileage, "mileage"),
            (self.minYear, self.maxYear, "year"),
        ):
            if lower is not None and upper is not None and lower > upper:
                raise ValueError(f"minimum {label} cannot exceed maximum {label}")
        return self


class VehicleIdentityQuery(ToolInput):
    """Inventory descriptors used to resolve one vehicle across every stock state."""

    q: str | None = Field(
        default=None,
        max_length=100,
        description="Free-text vehicle identity across make, model, variant, year, and colour.",
    )
    make: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    colour: str | None = Field(default=None, max_length=60)
    dealershipId: str | None = Field(default=None, max_length=80)
    dealershipTown: str | None = Field(default=None, max_length=80)
    minYear: int | None = Field(default=None, ge=1900, le=2100)
    maxYear: int | None = Field(default=None, ge=1900, le=2100)

    @model_validator(mode="after")
    def has_identity_descriptor(self):
        if not any(
            getattr(self, field) is not None
            for field in (
                "q",
                "make",
                "model",
                "colour",
                "dealershipId",
                "dealershipTown",
                "minYear",
                "maxYear",
            )
        ):
            raise ValueError("at least one vehicle identity descriptor is required")
        if self.minYear is not None and self.maxYear is not None and self.minYear > self.maxYear:
            raise ValueError("minimum year cannot exceed maximum year")
        return self


class PageVehicleSelection(ToolInput):
    vehicleIds: list[str] = Field(min_length=1, max_length=50)
    q: str | None = Field(
        default=None,
        max_length=100,
        description="Free-text constraint over only the explicitly selected page vehicles.",
    )
    make: str | None = Field(default=None, max_length=60)
    makes: VehicleInclusionValues | None = None
    model: str | None = Field(default=None, max_length=60)
    models: VehicleInclusionValues | None = None
    colour: str | None = Field(default=None, max_length=60)
    colours: VehicleInclusionValues | None = None
    fuelType: str | None = Field(default=None, max_length=40)
    fuelTypes: VehicleInclusionValues | None = None
    transmission: str | None = Field(default=None, max_length=40)
    transmissions: VehicleInclusionValues | None = None
    bodyStyle: str | None = Field(default=None, max_length=40)
    bodyStyles: VehicleInclusionValues | None = None
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
    minMileage: int | None = Field(default=None, ge=0)
    maxMileage: int | None = Field(default=None, ge=0)
    minYear: int | None = Field(default=None, ge=1900, le=2100)
    maxYear: int | None = Field(default=None, ge=1900, le=2100)
    sort: Literal[
        "newest", "priceAsc", "priceDesc", "mileageAsc", "mileageDesc"
    ] = "newest"
    limit: int = Field(default=3, ge=1, le=12)

    @field_validator("vehicleIds")
    @classmethod
    def valid_unique_vehicle_ids(cls, values):
        if any(not re.fullmatch(r"veh-[0-9]{3}", value) for value in values):
            raise ValueError("invalid vehicle ID")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def ordered_ranges(self):
        for lower, upper, label in (
            (self.minPricePence, self.maxPricePence, "price"),
            (self.minMileage, self.maxMileage, "mileage"),
            (self.minYear, self.maxYear, "year"),
        ):
            if lower is not None and upper is not None and lower > upper:
                raise ValueError(f"minimum {label} cannot exceed maximum {label}")
        return self


class StableId(ToolInput):
    id: str = Field(max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class VehicleComparison(ToolInput):
    vehicleIds: list[str] = Field(min_length=2, max_length=3)


class VehicleComparisonRuntime(VehicleComparison):
    """Comparison input enriched only by server-owned conversation state."""

    selectionBasis: str | None = Field(default=None, max_length=160)


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
    timeOfDay: Literal["morning", "afternoon"] | None = None
    schedulePreferenceMode: Literal["preserve", "replace", "clear"] | None = None
    make: str | None = Field(default=None, max_length=60)
    productType: Literal["PCP", "PCH"] | None = None
    workflowMode: Literal["booking", "amendment"] | None = None


class TestDriveSlotAgentInput(ToolInput):
    """Public selectors relevant to test-drive availability."""

    vehicleId: str | None = Field(default=None, pattern=r"^veh-[0-9]{3}$")
    dealershipId: str | None = Field(default=None, max_length=80)
    dealershipTown: str | None = Field(default=None, max_length=80)
    dateFrom: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    dateTo: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    timeOfDay: Literal["morning", "afternoon"] | None = None
    schedulePreferenceMode: Literal["preserve", "replace", "clear"] | None = Field(
        default=None,
        description=(
            "Preserve omitted active schedule preferences by default. Use replace when the "
            "customer supplies a new schedule, or clear when they ask to see available options "
            "without the previously unsuccessful day/time preference."
        ),
    )


class WorkshopSlotAgentInput(ToolInput):
    """Public selectors relevant to workshop availability."""

    dealershipId: str | None = Field(default=None, max_length=80)
    serviceTypeId: str | None = Field(default=None, max_length=80)
    serviceTypeName: str | None = Field(default=None, max_length=80)
    dealershipTown: str | None = Field(default=None, max_length=80)
    dateFrom: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    dateTo: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    timeOfDay: Literal["morning", "afternoon"] | None = Field(
        default=None,
        description="Use morning for morning, AM, or before-noon requests; afternoon otherwise.",
    )
    schedulePreferenceMode: Literal["preserve", "replace", "clear"] | None = Field(
        default=None,
        description=(
            "Preserve omitted active schedule preferences by default. Use replace when the "
            "customer supplies a new schedule, or clear when they ask for the actual available "
            "options after a preference returned no matches."
        ),
    )
    workflowMode: Literal["booking", "amendment"] | None = None


class OfferSearch(ToolInput):
    """Published-offer discovery, including an explicit all-results request."""

    make: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    productType: Literal["PCP", "PCH"] | None = None
    showAll: bool = False


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
    department: Literal["sales", "service", "parts"] | None = Field(
        default=None,
        description=(
            "Customer-supplied department context retained for a later contact continuation; "
            "it does not filter the dealership directory result."
        ),
    )


class ContactOptionsInput(ToolInput):
    """Runtime-only context for an application-owned contact-method chooser."""

    actionHandoff: ActionHandoff | None = None


class DealershipScopeQuery(ToolInput):
    dealershipId: str | None = Field(default=None, max_length=80)
    town: str | None = Field(default=None, max_length=80)
    department: Literal["sales", "service", "parts"] | None = Field(
        default=None,
        description=(
            "Customer-supplied department context retained for a later contact continuation; "
            "it does not filter the dealership department result."
        ),
    )


class HolidayOpeningHoursQuery(DealershipScopeQuery):
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class BusinessInformationQuery(ToolInput):
    topic: Literal["finance", "privacy", "part_exchange", "general"]
    question: str = Field(min_length=1, max_length=4_000)


class ServiceQuery(ToolInput):
    q: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "The customer's own workshop-service name or description. Preserve broad, "
            "ambiguous, misspelled, and unsupported descriptions so the live service catalogue "
            "can return a matched, ambiguous, or unavailable result."
        ),
    )
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
    """Runtime draft values merged by the server capability state machine."""

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


class AppointmentWorkflowDraftInput(WorkflowDraftInput):
    """Draft input enriched only from the selected trusted appointment result."""

    selectedStartsAt: str | None = Field(default=None, min_length=1, max_length=80)
    selectedDealershipId: str | None = Field(default=None, min_length=1, max_length=80)
    selectedDealershipName: str | None = Field(default=None, min_length=1, max_length=160)
    selectedServiceTypeId: str | None = Field(default=None, min_length=1, max_length=80)
    selectedServiceName: str | None = Field(default=None, min_length=1, max_length=160)
    dateFrom: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    dateTo: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    timeOfDay: Literal["morning", "afternoon"] | None = None
    make: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    q: str | None = Field(default=None, max_length=100)
    selectionEvidence: dict[str, str] | None = None

    @field_validator("selectionEvidence")
    @classmethod
    def valid_selection_evidence(
        cls, value: dict[str, str] | None
    ) -> dict[str, str] | None:
        if value is None:
            return None
        if set(value) != {"vehicleId", "basis", "matchedIdentity"}:
            raise ValueError("selection evidence has an invalid shape")
        if not re.fullmatch(r"veh-[0-9]{3}", value["vehicleId"]):
            raise ValueError("selection evidence has an invalid vehicle")
        if value["basis"] not in {
            "named_vehicle",
            "deictic_single_candidate",
            "trusted_action",
            "persisted_selection",
            "ai_resolved_trusted_reference",
        }:
            raise ValueError("selection evidence has an invalid basis")
        if not value["matchedIdentity"] or len(value["matchedIdentity"]) > 160:
            raise ValueError("selection evidence has an invalid identity")
        return value


class SalesEnquiryAgentInput(ToolInput):
    """Public sales-enquiry values the conversational agent may propose."""

    vehicleId: str | None = Field(default=None, pattern=r"^veh-[0-9]{3}$")
    dealershipId: str | None = Field(default=None, max_length=80)
    dealershipTown: str | None = Field(default=None, max_length=80)
    enquiryType: Literal["general", "availability", "finance", "part-exchange"] | None = None
    message: str | None = Field(default=None, min_length=5, max_length=2_000)


class TestDriveAgentInput(ToolInput):
    vehicleId: str | None = Field(default=None, pattern=r"^veh-[0-9]{3}$")
    dealershipId: str | None = Field(default=None, max_length=80)
    dealershipTown: str | None = Field(default=None, max_length=80)
    make: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    q: str | None = Field(default=None, max_length=100)
    slotId: str | None = Field(default=None, pattern=r"^td-slot-[0-9]{4}$")
    dateFrom: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    dateTo: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    timeOfDay: Literal["morning", "afternoon"] | None = None


class VehicleInterestAgentInput(ToolInput):
    vehicleId: str | None = Field(default=None, pattern=r"^veh-[0-9]{3}$")
    notes: str | None = Field(default=None, min_length=1, max_length=1_000)


class CallbackAgentInput(ToolInput):
    dealershipId: str | None = Field(default=None, max_length=80)
    dealershipTown: str | None = Field(default=None, max_length=80)
    department: Literal["sales", "service", "parts"] | None = None
    vehicleId: str | None = Field(default=None, pattern=r"^veh-[0-9]{3}$")
    reason: str | None = Field(default=None, min_length=5, max_length=1_000)
    preferredTime: str | None = Field(default=None, min_length=1, max_length=120)


class WorkshopBookingAgentInput(ToolInput):
    serviceTypeId: str | None = Field(default=None, max_length=80)
    dealershipId: str | None = Field(default=None, max_length=80)
    slotId: str | None = Field(default=None, pattern=r"^ws-slot-[0-9]{4}$")
    notes: str | None = Field(default=None, min_length=1, max_length=1_000)


class DealershipMessageAgentInput(ToolInput):
    dealershipId: str | None = Field(default=None, max_length=80)
    dealershipTown: str | None = Field(default=None, max_length=80)
    department: Literal["sales", "service", "parts", "general"] | None = None
    preferredContactMethod: Literal["email", "phone"] | None = None
    subject: str | None = Field(default=None, min_length=3, max_length=120)
    message: str | None = Field(default=None, min_length=5, max_length=2_000)


class PartExchangeAgentInput(ToolInput):
    dealershipId: str | None = Field(default=None, max_length=80)
    dealershipTown: str | None = Field(default=None, max_length=80)


class WorkshopAmendmentAgentInput(ToolInput):
    """Public booking changes the conversational agent may propose."""

    slotId: str | None = Field(default=None, pattern=r"^ws-slot-[0-9]{4}$")
    mileage: int | None = Field(default=None, ge=0, le=2_000_000)
    notes: str | None = Field(
        default=None,
        max_length=1_000,
        description="Relevant customer-authored notes; omit when no notes were supplied.",
    )


class WorkshopAmendmentInput(WorkshopAmendmentAgentInput):
    """Runtime amendment payload enriched from a trusted live appointment result."""

    selectedStartsAt: str | None = Field(default=None, min_length=1, max_length=80)
    selectedDealershipId: str | None = Field(default=None, min_length=1, max_length=80)
    selectedDealershipName: str | None = Field(default=None, min_length=1, max_length=160)
    selectedServiceTypeId: str | None = Field(default=None, min_length=1, max_length=80)
    selectedServiceName: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def complete_trusted_appointment(self):
        metadata = (
            self.selectedStartsAt,
            self.selectedDealershipId,
            self.selectedDealershipName,
            self.selectedServiceTypeId,
            self.selectedServiceName,
        )
        if self.slotId is not None and not all(metadata):
            raise ValueError("an appointment change requires complete trusted slot metadata")
        if self.slotId is None and any(metadata):
            raise ValueError("trusted slot metadata requires an appointment selection")
        return self


class WorkshopAmendmentSlotContext(ToolInput):
    """Live slot metadata resolved by the application, never by the model."""

    slotId: str = Field(pattern=r"^ws-slot-[0-9]{4}$")
    selectedStartsAt: str = Field(min_length=1, max_length=80)
    selectedDealershipId: str = Field(min_length=1, max_length=80)
    selectedDealershipName: str = Field(min_length=1, max_length=160)
    selectedServiceTypeId: str = Field(min_length=1, max_length=80)
    selectedServiceName: str = Field(min_length=1, max_length=160)


class DealershipMessageDraftInput(ToolInput):
    """Runtime fields accepted by the dealership-message capability."""

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
