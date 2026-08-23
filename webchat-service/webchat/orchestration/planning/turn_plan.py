"""Versioned domain-goal schema for semantic provider output."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from webchat.integrations.contracts import TurnPlan

TURN_PLAN_TOOL = "plan_customer_turn"

PreferenceDimension = Literal[
    "startingPoint",
    "budgets",
    "mileages",
    "makes",
    "models",
    "fuelTypes",
    "transmissions",
    "bodyStyles",
]


class PlanPayload(BaseModel):
    """Common envelope; each domain subclass exposes only its own fields."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[2] = 2
    response: str | None = Field(
        default=None,
        max_length=4_000,
        description=(
            "Customer-facing wording only for conversation goals or a concise "
            "summary of trusted tool facts already present."
        ),
    )


class VehiclePlanPayload(PlanPayload):
    domain: Literal["vehicle"]
    goal: Literal[
        "search",
        "continue_search",
        "compare",
        "view_details",
        "check_availability",
        "choose_preferences",
        "apply_preference",
    ]
    query: str | None = Field(default=None, max_length=200)
    vehicleId: str | None = Field(default=None, pattern=r"^veh-[0-9]{3}$")
    vehicleIds: list[str] | None = Field(default=None, min_length=2, max_length=3)
    vehicleQueries: list[str] | None = Field(default=None, min_length=2, max_length=3)
    preferenceDimension: PreferenceDimension | None = None
    preferenceValue: str | int | None = None
    page: int | None = Field(default=None, ge=1, le=100)
    make: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    fuelType: str | None = Field(default=None, max_length=40)
    transmission: str | None = Field(default=None, max_length=40)
    bodyStyle: str | None = Field(default=None, max_length=40)
    availability: Literal["available", "reserved", "sold"] | None = None
    dealershipId: str | None = Field(default=None, max_length=80)
    town: str | None = Field(default=None, max_length=80)
    minPricePence: int | None = Field(default=None, ge=0)
    maxPricePence: int | None = Field(default=None, ge=0)
    maxMileage: int | None = Field(default=None, ge=0)
    minYear: int | None = Field(default=None, ge=1900, le=2100)
    sort: Literal["newest", "priceAsc", "priceDesc", "mileageAsc"] | None = None
    referenceScope: Literal["currentPage", "displayedChat", "activeSearch"] | None = None
    resultLimit: int | None = Field(default=None, ge=1, le=12)
    resolveAgainstLiveFacets: bool = Field(
        default=False,
        description=(
            "Application-selected search preflight for an unresolved catalogue term. "
            "Do not set unless trusted application routing requests it."
        ),
    )
    refineCurrentSearch: bool = False
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
    ] | None = Field(default=None, max_length=14)
    reuseActiveEntity: bool = False

    @model_validator(mode="after")
    def valid_preference(self):
        if self.goal != "apply_preference":
            return self
        dimension_fields = {
            "budgets": self.maxPricePence,
            "mileages": self.maxMileage,
            "makes": self.make,
            "models": self.model,
            "fuelTypes": self.fuelType,
            "transmissions": self.transmission,
            "bodyStyles": self.bodyStyle,
        }
        if not self.preferenceDimension or (
            self.preferenceValue in (None, "")
            and dimension_fields.get(self.preferenceDimension) in (None, "")
        ):
            raise ValueError(
                "vehicle.apply_preference requires a preference dimension and value"
            )
        return self


class OfferPlanPayload(PlanPayload):
    domain: Literal["offer"]
    goal: Literal["browse", "view_details", "enquire"]
    offerId: str | None = Field(default=None, max_length=80)
    make: str | None = Field(default=None, max_length=60)
    productType: Literal["PCP", "PCH"] | None = None
    message: str | None = Field(default=None, max_length=1_000)


class TestDrivePlanPayload(PlanPayload):
    domain: Literal["test_drive"]
    goal: Literal["book"]
    vehicleId: str | None = Field(default=None, pattern=r"^veh-[0-9]{3}$")
    query: str | None = Field(default=None, max_length=200)
    reuseActiveEntity: bool = False


class SalesPlanPayload(PlanPayload):
    domain: Literal["sales"]
    goal: Literal["enquire", "request_callback", "register_vehicle_interest"]
    vehicleId: str | None = Field(default=None, pattern=r"^veh-[0-9]{3}$")
    offerId: str | None = Field(default=None, max_length=80)
    dealershipId: str | None = Field(default=None, max_length=80)
    department: Literal["sales", "service", "parts", "general"] | None = None
    enquiryType: Literal["general", "availability", "finance", "part-exchange"] | None = None
    reason: str | None = Field(default=None, max_length=1_000)
    message: str | None = Field(default=None, max_length=1_000)
    preferredTime: str | None = Field(default=None, max_length=120)
    firstName: str | None = Field(default=None, max_length=100)
    lastName: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=30)
    reuseActiveEntity: bool = False


class DealershipPlanPayload(PlanPayload):
    domain: Literal["dealership"]
    goal: Literal[
        "find",
        "view_contact",
        "view_departments",
        "view_opening_hours",
        "send_message",
    ]
    dealershipId: str | None = Field(default=None, max_length=80)
    town: str | None = Field(default=None, max_length=80)
    day: Literal[
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
    ] | None = None
    department: Literal["sales", "service", "parts", "general"] | None = None
    subject: str | None = Field(default=None, max_length=200)
    message: str | None = Field(default=None, max_length=2_000)
    preferredContactMethod: Literal["email", "phone"] | None = None
    firstName: str | None = Field(default=None, max_length=100)
    lastName: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=30)


class WorkshopPlanPayload(PlanPayload):
    domain: Literal["workshop"]
    goal: Literal[
        "find_locations",
        "browse_services",
        "check_service",
        "book_service",
        "find_booking",
        "change_booking",
        "cancel_booking",
    ]
    dealershipId: str | None = Field(default=None, max_length=80)
    town: str | None = Field(default=None, max_length=80)
    serviceTypeId: str | None = Field(default=None, max_length=80)
    serviceQuery: str | None = Field(
        default=None,
        max_length=200,
        description="The customer's own named-service wording, resolved against the live catalogue.",
    )
    slotId: str | None = Field(default=None, max_length=80)
    dateFrom: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    dateTo: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    registration: str | None = Field(default=None, max_length=20)
    mileage: int | None = Field(default=None, ge=0, le=2_000_000)
    notes: str | None = Field(default=None, max_length=1_000)
    firstName: str | None = Field(default=None, max_length=100)
    lastName: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=30)
    reuseActiveEntity: bool = False

    @model_validator(mode="after")
    def check_service_has_target(self):
        if self.goal == "check_service" and not (
            self.serviceTypeId or self.serviceQuery or self.reuseActiveEntity
        ):
            raise ValueError(
                "workshop.check_service requires a service query, service ID, or active-service reference"
            )
        return self


class PartExchangePlanPayload(PlanPayload):
    domain: Literal["part_exchange"]
    goal: Literal["estimate", "request_follow_up"]
    dealershipId: str | None = Field(default=None, max_length=80)
    vehicleId: str | None = Field(default=None, pattern=r"^veh-[0-9]{3}$")
    registration: str | None = Field(default=None, max_length=20)
    mileage: int | None = Field(default=None, ge=0, le=1_000_000)
    condition: Literal["excellent", "good", "fair"] | None = None
    firstName: str | None = Field(default=None, max_length=100)
    lastName: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=30)


class BusinessPlanPayload(PlanPayload):
    domain: Literal["business"]
    goal: Literal[
        "finance_information",
        "privacy_information",
        "part_exchange_information",
        "general_information",
    ]


class ConversationPlanPayload(PlanPayload):
    domain: Literal["conversation"]
    goal: Literal["respond", "clarify"]

    @model_validator(mode="after")
    def response_is_present(self):
        if not str(self.response or "").strip():
            raise ValueError("conversation goals require a customer-facing response")
        return self


TurnPlanPayload = Annotated[
    VehiclePlanPayload
    | OfferPlanPayload
    | TestDrivePlanPayload
    | SalesPlanPayload
    | DealershipPlanPayload
    | WorkshopPlanPayload
    | PartExchangePlanPayload
    | BusinessPlanPayload
    | ConversationPlanPayload,
    Field(discriminator="domain"),
]
TURN_PLAN_ADAPTER = TypeAdapter(TurnPlanPayload)


def turn_plan_definition() -> dict[str, Any]:
    schema = TURN_PLAN_ADAPTER.json_schema()
    schema.pop("title", None)
    return {
        "type": "function",
        "name": TURN_PLAN_TOOL,
        "description": (
            "Classify the latest customer turn into one versioned business domain and goal, then "
            "extract only supplied constraints or trusted references. This is a semantic plan, "
            "not a customer-facing business response. Domain and goal must be a valid pair."
        ),
        "parameters": schema,
        "strict": False,
    }


def parse_turn_plan(arguments: dict[str, Any]) -> TurnPlan:
    payload = TURN_PLAN_ADAPTER.validate_python(arguments)
    values = payload.model_dump(exclude_none=True, exclude_defaults=True)
    version = int(values.pop("version", 2))
    domain = str(values.pop("domain"))
    goal = str(values.pop("goal"))
    response = str(values.pop("response", ""))
    return TurnPlan(
        domain=domain,
        goal=goal,
        arguments=values,
        response=response,
        version=version,
    )
