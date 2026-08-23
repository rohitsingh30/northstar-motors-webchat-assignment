"""Strict input models for allow-listed business tools."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VehicleSearch(ToolInput):
    page: int = Field(default=1, ge=1, le=100)
    q: str | None = Field(default=None, max_length=100)
    make: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    fuelType: str | None = Field(default=None, max_length=40)
    transmission: str | None = Field(default=None, max_length=40)
    bodyStyle: str | None = Field(default=None, max_length=40)
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
    day: Literal[
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ] | None = None
    town: str | None = Field(default=None, max_length=80)
    department: Literal["sales", "service", "parts"] | None = None


class DealershipQuery(ToolInput):
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
