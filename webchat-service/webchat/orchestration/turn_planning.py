from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from webchat.integrations.contracts import TurnPlan

TURN_PLAN_TOOL = "plan_customer_turn"

TurnIntent = Literal[
    "vehicle_search",
    "vehicle_more",
    "vehicle_compare",
    "vehicle_details",
    "vehicle_availability",
    "vehicle_preferences",
    "vehicle_preference_selection",
    "test_drive",
    "offers_list",
    "offer_details",
    "dealership_locations",
    "dealership_contact",
    "dealership_departments",
    "opening_hours",
    "workshop_locations",
    "workshop_services",
    "workshop_booking",
    "workshop_service_information",
    "workshop_booking_lookup",
    "workshop_booking_change",
    "workshop_booking_cancel",
    "part_exchange_estimate",
    "part_exchange_follow_up",
    "callback",
    "sales_enquiry",
    "vehicle_interest",
    "dealership_message",
    "business_information",
    "general_response",
    "clarification",
]


class TurnPlanPayload(BaseModel):
    """Bounded semantic plan. Optional fields are validated again by each domain tool."""

    model_config = ConfigDict(extra="forbid")

    intent: TurnIntent = Field(
        description=(
            "One canonical customer goal; never use general_response for a supported workflow. "
            "A new workshop appointment is workshop_booking. Viewing an existing appointment is "
            "workshop_booking_lookup; changing it is workshop_booking_change; cancelling it is "
            "workshop_booking_cancel. Preserve that distinction regardless of wording."
        )
    )
    response: str | None = Field(
        default=None,
        max_length=4_000,
        description="Customer-facing wording only for general_response, clarification, or a tool-fact summary.",
    )
    query: str | None = Field(
        default=None,
        max_length=200,
        description="Natural-language vehicle, offer, or general entity query when a stable ID is unavailable.",
    )
    vehicleId: str | None = Field(default=None, pattern=r"^veh-[0-9]{3}$")
    vehicleIds: list[str] | None = Field(default=None, min_length=2, max_length=3)
    vehicleQueries: list[str] | None = Field(default=None, min_length=2, max_length=3)
    offerId: str | None = Field(default=None, max_length=80)
    dealershipId: str | None = Field(default=None, max_length=80)
    town: str | None = Field(default=None, max_length=80)
    day: Literal[
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
    ] | None = None
    department: Literal["sales", "service", "parts", "general"] | None = None
    serviceTypeId: str | None = Field(default=None, max_length=80)
    serviceQuery: str | None = Field(
        default=None,
        max_length=200,
        description="The customer's own workshop-service wording; resolved against the live catalogue.",
    )
    slotId: str | None = Field(default=None, max_length=80)
    dateFrom: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    dateTo: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    registration: str | None = Field(default=None, max_length=20)
    mileage: int | None = Field(default=None, ge=0, le=2_000_000)
    condition: Literal["excellent", "good", "fair"] | None = None
    firstName: str | None = Field(default=None, max_length=100)
    lastName: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=30)
    reason: str | None = Field(default=None, max_length=1_000)
    message: str | None = Field(default=None, max_length=1_000)
    subject: str | None = Field(default=None, max_length=200)
    enquiryType: Literal["general", "availability", "finance", "part-exchange"] | None = None
    preferredContactMethod: Literal["email", "phone"] | None = None
    preferredTime: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=1_000)
    preferenceDimension: Literal[
        "startingPoint",
        "budgets",
        "mileages",
        "makes",
        "models",
        "fuelTypes",
        "transmissions",
        "bodyStyles",
    ] | None = Field(
        default=None,
        description=(
            "Exactly one application-owned preference choice group. Set a budget maps to budgets; "
            "low-mileage or mileage selection maps to mileages."
        ),
    )
    preferenceValue: str | int | None = Field(
        default=None,
        description=(
            "The live option the customer selected after a vehicle preference choice was shown. "
            "Use with vehicle_preference_selection; do not use it merely to request choices."
        ),
    )
    page: int | None = Field(default=None, ge=1, le=100)
    make: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    fuelType: str | None = Field(default=None, max_length=40)
    transmission: str | None = Field(default=None, max_length=40)
    bodyStyle: str | None = Field(default=None, max_length=40)
    availability: Literal["available", "reserved", "sold"] | None = None
    minPricePence: int | None = Field(default=None, ge=0)
    maxPricePence: int | None = Field(default=None, ge=0)
    maxMileage: int | None = Field(default=None, ge=0)
    minYear: int | None = Field(default=None, ge=1900, le=2100)
    sort: Literal["newest", "priceAsc", "priceDesc", "mileageAsc"] | None = None
    referenceScope: Literal["currentPage", "displayedChat", "activeSearch"] | None = Field(
        default=None,
        description=(
            "The bounded result set explicitly referenced by the customer. Use currentPage for "
            "references to vehicles visible on the website page (for example these, those, here, "
            "on this page, or among these); displayedChat for cards in the latest assistant "
            "response; and activeSearch only for continuation of the chat's active search."
        ),
    )
    resultLimit: int | None = Field(
        default=None,
        ge=1,
        le=12,
        description=(
            "Number of results directly requested or logically required. Use 1 for a singular "
            "superlative such as cheapest, newest, or lowest mileage."
        ),
    )
    productType: Literal["PCP", "PCH"] | None = None
    refineCurrentSearch: bool = Field(
        default=False,
        description=(
            "True only when the latest request changes, adds, or removes constraints from the "
            "active vehicle search. False starts a fresh search and cannot inherit old filters."
        ),
    )
    clearVehicleFilters: list[
        Literal[
            "query",
            "make",
            "model",
            "fuelType",
            "transmission",
            "bodyStyle",
            "dealershipId",
            "town",
            "minPricePence",
            "maxPricePence",
            "maxMileage",
            "minYear",
            "sort",
        ]
    ] | None = Field(
        default=None,
        max_length=14,
        description=(
            "Active vehicle-search constraints the customer explicitly removes, for example "
            "fuelType for 'any fuel' or maxPricePence for 'ignore the budget'."
        ),
    )
    reuseActiveEntity: bool = Field(
        default=False,
        description=(
            "True only when the customer explicitly refers to the active entity with wording such "
            "as it, that service, or the selected vehicle. Never use it when a new entity is named."
        ),
    )

    @field_validator("day", mode="before")
    @classmethod
    def normalize_day(cls, value):
        return value.strip().title() if isinstance(value, str) else value

    @field_validator("department", "condition", mode="before")
    @classmethod
    def normalize_lowercase_enum(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    def to_plan(self) -> TurnPlan:
        values = self.model_dump(exclude_none=True)
        intent = str(values.pop("intent"))
        response = str(values.pop("response", ""))
        return TurnPlan(intent=intent, arguments=values, response=response)


def turn_plan_definition() -> dict[str, Any]:
    schema = TurnPlanPayload.model_json_schema()
    schema.pop("title", None)
    return {
        "type": "function",
        "name": TURN_PLAN_TOOL,
        "description": (
            "Classify the customer's current communicative goal into one canonical intent and "
            "extract only the constraints they supplied or that are unambiguously resolved from "
            "trusted conversation/page context. This is a plan, not a customer-facing response. "
            "Use response only for general_response, clarification, or a concise answer based on "
            "tool facts already present. Workshop booking locations and dates are optional; never "
            "invent a location-selection step. A price, duration, or inclusion question is "
            "workshop_service_information, not workshop_booking. Changing or cancelling an existing "
            "booking uses the matching existing-booking intent and must preserve whether the "
            "requested continuation is lookup, amendment, or cancellation. A generic "
            "part-exchange estimate remains an "
            "estimate intent even when its three vehicle fields are missing. Set "
            "refineCurrentSearch only for a genuine follow-up to the active stock search; otherwise "
            "old filters are deliberately discarded. When the customer refers to vehicles visible "
            "on the host website with language such as these, those, among these, here, or on this "
            "page, set referenceScope=currentPage and rank/filter only that bounded set. Never turn "
            "a currentPage reference into a global stock search. For singular superlatives set the "
            "corresponding sort and resultLimit=1."
        ),
        "parameters": schema,
        "strict": False,
    }


def parse_turn_plan(arguments: dict[str, Any]) -> TurnPlan:
    return TurnPlanPayload.model_validate(arguments).to_plan()
