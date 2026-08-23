"""Application-owned form and estimate tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from webchat.orchestration.tools.inputs import (
    BookingLookupForm,
    PartExchangeEstimate,
    PartExchangeEstimateForm,
)
from webchat.orchestration.tools.result import ToolResult


class EstimateGateway(Protocol):
    async def estimate_part_exchange(self, payload: dict[str, Any]) -> dict[str, Any]: ...


ToolMethod = Callable[[dict[str, Any]], Awaitable[ToolResult]]


class FormToolHandler:
    """Own application forms and the deterministic valuation request."""

    def __init__(self, dealership: EstimateGateway):
        self.dealership = dealership
        self.routes: dict[str, ToolMethod] = {
            "request_workshop_booking_lookup_form": self._booking_lookup,
            "request_part_exchange_estimate_form": self._part_exchange_form,
            "estimate_part_exchange": self._part_exchange_estimate,
        }

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        return await self.routes[name](arguments)

    async def _booking_lookup(self, arguments: dict[str, Any]) -> ToolResult:
        request = BookingLookupForm.model_validate(arguments)
        descriptions = {
            "lookup": "Enter all four booking details to view the appointment securely.",
            "amend": "Enter all four booking details to continue directly to the change form.",
            "cancel": "Enter all four booking details to review the cancellation securely.",
        }
        return ToolResult(
            descriptions[request.mode],
            "private_booking_lookup",
            {"version": 1, "mode": request.mode},
            {"lookupFormRequested": True, "mode": request.mode},
        )

    async def _part_exchange_form(self, arguments: dict[str, Any]) -> ToolResult:
        known = PartExchangeEstimateForm.model_validate(arguments).model_dump(
            exclude_none=True
        )
        return ToolResult(
            "Enter the remaining vehicle details in the form below.",
            "part_exchange_estimate_form",
            {"version": 1, "values": known},
            {"estimateFormRequested": True, "values": known},
        )

    async def _part_exchange_estimate(self, arguments: dict[str, Any]) -> ToolResult:
        estimate = PartExchangeEstimate.model_validate(arguments)
        data = await self.dealership.estimate_part_exchange(estimate.model_dump())
        return ToolResult(
            "Here is the platform's indicative part-exchange range.",
            "part_exchange_estimate",
            {"version": 1, **data},
            data,
        )
