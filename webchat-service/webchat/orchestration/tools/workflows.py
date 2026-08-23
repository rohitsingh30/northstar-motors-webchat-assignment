"""Prepare write-workflow drafts and confirmation views."""

from __future__ import annotations

from typing import Any, ClassVar, Protocol

from webchat.orchestration.tools.result import ToolResult


class WorkflowService(Protocol):
    def prepare(self, conversation_id: str, kind: str, fields: dict[str, Any]): ...


class WorkflowDealershipGateway(Protocol):
    async def get_business_information(self) -> dict[str, Any]: ...

    async def get_vehicle(self, vehicle_id: str) -> dict[str, Any]: ...

    async def list_dealerships(self) -> dict[str, Any]: ...


class WorkflowToolHandler:
    """Prepare write drafts and enrich their application-owned confirmation views."""

    TOOL_TO_KIND: ClassVar[dict[str, str]] = {
        "prepare_sales_enquiry": "sales_enquiry",
        "prepare_test_drive": "test_drive",
        "prepare_vehicle_interest": "vehicle_interest",
        "prepare_callback": "callback",
        "prepare_workshop_booking": "workshop_booking",
        "prepare_workshop_amendment": "workshop_amend",
        "prepare_workshop_cancellation": "workshop_cancel",
        "prepare_dealership_message": "dealership_message",
        "prepare_part_exchange": "part_exchange",
    }

    def __init__(
        self,
        dealership: WorkflowDealershipGateway,
        workflows: WorkflowService | None,
    ):
        self.dealership = dealership
        self.workflows = workflows

    def supports(self, name: str) -> bool:
        return name in self.TOOL_TO_KIND

    async def execute(
        self, name: str, arguments: dict[str, Any], conversation_id: str | None
    ) -> ToolResult:
        if self.workflows is None or conversation_id is None:
            raise ValueError("Workflow tools require a conversation")
        draft = self.workflows.prepare(
            conversation_id, self.TOOL_TO_KIND[name], arguments
        )
        payload = {
            "version": 1,
            "draftId": draft.id,
            "kind": draft.kind,
            "status": draft.status,
            "missingFields": draft.missing_fields,
            "summary": draft.summary,
        }
        await self._enrich(payload, draft.kind, draft.summary)
        return ToolResult(
            "Please review and confirm these details."
            if draft.status == "awaiting_confirmation"
            else "I need a few more details before asking for confirmation.",
            "confirmation" if draft.status == "awaiting_confirmation" else "draft",
            payload,
            payload,
        )

    async def _enrich(self, payload: dict, kind: str, summary: dict) -> None:
        business = await self.dealership.get_business_information()
        payload["privacyContact"] = business.get("privacyContact")
        if kind in {"vehicle_interest", "sales_enquiry"} and summary.get("vehicleId"):
            vehicle = await self.dealership.get_vehicle(summary["vehicleId"])
            payload["vehicle"] = {
                field: vehicle.get(field)
                for field in (
                    "id",
                    "year",
                    "make",
                    "model",
                    "variant",
                    "pricePence",
                    "mileage",
                    "fuelType",
                    "transmission",
                    "availability",
                    "dealershipTown",
                )
            }
        if kind in {
            "part_exchange",
            "callback",
            "sales_enquiry",
            "dealership_message",
        }:
            dealerships = await self.dealership.list_dealerships()
            payload["dealerships"] = [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "town": item.get("town"),
                }
                for item in dealerships.get("items", [])
            ]
