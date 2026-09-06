"""Application-owned protected-input activation and estimate tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from webchat.domain.field_guidance import field_prompt, input_guidance
from webchat.orchestration.tools.inputs import (
    BookingLookupForm,
    PartExchangeEstimate,
    PartExchangeEstimateForm,
)
from webchat.orchestration.tools.result import ToolResult


class EstimateGateway(Protocol):
    async def estimate_part_exchange(self, payload: dict[str, Any]) -> dict[str, Any]: ...


ToolMethod = Callable[[dict[str, Any]], Awaitable[ToolResult]]


class CollectorToolHandler:
    """Activate protected input and execute the deterministic valuation request."""

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
            "lookup": f"I can check that securely. {field_prompt('reference')}",
            "amend": f"I can help update that securely. {field_prompt('reference')}",
            "cancel": f"I can review that securely. {field_prompt('reference')}",
        }
        return ToolResult(
            descriptions[request.mode],
            "private_booking_lookup",
            {
                "version": 1,
                "kind": "booking_lookup",
                "mode": request.mode,
                "secureFields": ["reference", "lastName", "registration", "phone"],
                "secureInputReady": True,
                "inputGuidance": input_guidance(
                    ["reference", "lastName", "registration", "phone"]
                ),
            },
            {"lookupFormRequested": True, "mode": request.mode},
        )

    async def _part_exchange_form(self, arguments: dict[str, Any]) -> ToolResult:
        known = PartExchangeEstimateForm.model_validate(arguments).model_dump(exclude_none=True)
        return ToolResult(
            f"I can help with that estimate. {field_prompt('registration')}",
            "part_exchange_estimate_form",
            {
                "version": 1,
                "kind": "part_exchange_estimate",
                "values": known,
                "secureFields": [
                    field
                    for field in ("registration", "mileage", "condition")
                    if field not in known
                ],
                "secureInputReady": True,
                "inputGuidance": input_guidance(
                    [
                        field
                        for field in ("registration", "mileage", "condition")
                        if field not in known
                    ]
                ),
            },
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
