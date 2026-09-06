"""Single strict schema for every application-owned conversational action."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TurnAction(BaseModel):
    """A visible reply may request only one of these deterministic application operations."""

    model_config = ConfigDict(extra="forbid")

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
        "confirm_active_draft",
        "cancel_active_draft",
        "confirm_active_interaction",
        "cancel_active_interaction",
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
    def matching_identifier(self) -> TurnAction:
        identifiers = {
            "vehicleId": self.vehicleId,
            "serviceTypeId": self.serviceTypeId,
            "dealershipId": self.dealershipId,
            "offerId": self.offerId,
            "vehicleFilter": self.vehicleFilter,
            "vehicleFilterValue": self.vehicleFilterValue,
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
            "confirm_active_draft": set(),
            "cancel_active_draft": set(),
            "confirm_active_interaction": set(),
            "cancel_active_interaction": set(),
        }[self.type]
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
