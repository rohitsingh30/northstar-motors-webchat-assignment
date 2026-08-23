"""Prepare write-workflow drafts and confirmation views."""

from __future__ import annotations

from typing import Any, ClassVar, Protocol

from webchat.orchestration.tools.helpers import dealership_in_town
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
    FORM_TO_KIND: ClassVar[dict[str, str]] = {
        "request_offer_enquiry_form": "sales_enquiry",
    }
    DEALERSHIP_FORM_KINDS: ClassVar[frozenset[str]] = frozenset(
        {"part_exchange", "callback", "sales_enquiry", "dealership_message"}
    )

    def __init__(
        self,
        dealership: WorkflowDealershipGateway,
        workflows: WorkflowService | None,
    ):
        self.dealership = dealership
        self.workflows = workflows

    def supports(self, name: str) -> bool:
        return name in self.TOOL_TO_KIND or name in self.FORM_TO_KIND

    async def execute(
        self, name: str, arguments: dict[str, Any], conversation_id: str | None
    ) -> ToolResult:
        if self.workflows is None or conversation_id is None:
            raise ValueError("Workflow tools require a conversation")
        kind = self.TOOL_TO_KIND.get(name) or self.FORM_TO_KIND[name]
        dealerships: list[dict[str, Any]] | None = None
        prepared_arguments = dict(arguments)
        if kind in self.DEALERSHIP_FORM_KINDS:
            response = await self.dealership.list_dealerships()
            dealerships = list(response.get("items", []))
            prepared_arguments = self._resolve_dealership(prepared_arguments, dealerships)
        if name in self.FORM_TO_KIND:
            payload = {
                "version": 1,
                "kind": kind,
                "status": "collecting",
                "summary": prepared_arguments,
            }
            await self._enrich(payload, kind, prepared_arguments, dealerships)
            return ToolResult(
                "Complete the sales enquiry form.",
                "draft",
                payload,
                payload,
            )
        draft = self.workflows.prepare(conversation_id, kind, prepared_arguments)
        payload = {
            "version": 1,
            "draftId": draft.id,
            "kind": draft.kind,
            "status": draft.status,
            "missingFields": draft.missing_fields,
            "summary": draft.summary,
        }
        await self._enrich(payload, draft.kind, draft.summary, dealerships)
        return ToolResult(
            "Please review and confirm these details."
            if draft.status == "awaiting_confirmation"
            else "I need a few more details before asking for confirmation.",
            "confirmation" if draft.status == "awaiting_confirmation" else "draft",
            payload,
            payload,
        )

    @staticmethod
    def _resolve_dealership(
        arguments: dict[str, Any], dealerships: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Resolve a semantic town and reject stale IDs before persisting a draft."""
        resolved = dict(arguments)
        town = resolved.pop("dealershipTown", None)
        if town:
            match = dealership_in_town(dealerships, str(town))
            if match and match.get("id"):
                resolved["dealershipId"] = match["id"]
            else:
                resolved.pop("dealershipId", None)
            return resolved

        live_ids = {str(item["id"]) for item in dealerships if item.get("id")}
        if resolved.get("dealershipId") not in live_ids:
            resolved.pop("dealershipId", None)
        return resolved

    async def _enrich(
        self,
        payload: dict,
        kind: str,
        summary: dict,
        dealerships: list[dict[str, Any]] | None = None,
    ) -> None:
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
        if kind in self.DEALERSHIP_FORM_KINDS:
            if dealerships is None:
                dealerships = list((await self.dealership.list_dealerships()).get("items", []))
            payload["dealerships"] = [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "town": item.get("town"),
                }
                for item in dealerships
            ]
