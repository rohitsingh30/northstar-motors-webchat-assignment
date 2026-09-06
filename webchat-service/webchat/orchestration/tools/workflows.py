"""Prepare write-workflow drafts and confirmation views."""

from __future__ import annotations

from typing import Any, ClassVar, Protocol

from webchat.domain.capabilities import CapabilityRegistry
from webchat.domain.field_guidance import input_guidance
from webchat.orchestration.contracts.semantics import REFERENCE_FIELD_NAMESPACES
from webchat.orchestration.tools.helpers import dealership_in_town
from webchat.orchestration.tools.result import ToolResult


class WorkflowService(Protocol):
    def prepare(
        self,
        conversation_id: str,
        kind: str,
        fields: dict[str, Any],
        *,
        expected_draft_id: str | None = None,
        clear_fields: tuple[str, ...] = (),
    ): ...

    def cancel_active(self, conversation_id: str) -> dict[str, Any]: ...


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
    CONTROL_TOOLS: ClassVar[frozenset[str]] = frozenset(
        {"cancel_active_capability", "resume_paused_capability"}
    )
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
        return name in self.TOOL_TO_KIND or name in self.FORM_TO_KIND or name in self.CONTROL_TOOLS

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        conversation_id: str | None,
        *,
        replacement_draft_id: str | None = None,
    ) -> ToolResult:
        if self.workflows is None or conversation_id is None:
            raise ValueError("Workflow tools require a conversation")
        if name == "cancel_active_capability":
            result = self.workflows.cancel_active(conversation_id)
            return ToolResult(
                "The unfinished request has been cancelled."
                if result.get("cancelled")
                else "There is no unfinished request to cancel.",
                None,
                None,
                result,
            )
        if name == "resume_paused_capability":
            return ToolResult(
                "The paused request is active again.",
                None,
                None,
                {"resumed": True},
            )
        kind = self.TOOL_TO_KIND.get(name) or self.FORM_TO_KIND[name]
        dealerships: list[dict[str, Any]] | None = None
        prepared_arguments = dict(arguments)
        scheduling_preferences = {}
        vehicle_search_preferences = {}
        selection_evidence = None
        clear_fields: tuple[str, ...] = ()
        if kind == "test_drive":
            replacing_vehicle = not prepared_arguments.get("vehicleId") and any(
                prepared_arguments.get(key) is not None for key in ("make", "model", "q")
            )
            if replacing_vehicle:
                clear_fields = (
                    "vehicleId",
                    "slotId",
                    "selectedStartsAt",
                    "selectedDealershipId",
                    "selectedDealershipName",
                )
            scheduling_preferences = {
                key: prepared_arguments.pop(key)
                for key in ("dateFrom", "dateTo", "timeOfDay")
                if prepared_arguments.get(key) is not None
            }
            vehicle_search_preferences = {
                key: prepared_arguments.pop(key)
                for key in ("make", "model", "q", "dealershipId", "dealershipTown")
                if prepared_arguments.get(key) is not None
            }
            selection_evidence = prepared_arguments.pop("selectionEvidence", None)
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
                "I’ll collect the sales-enquiry details here.",
                "draft",
                payload,
                payload,
            )
        prepare_options: dict[str, Any] = {"expected_draft_id": replacement_draft_id}
        if clear_fields:
            prepare_options["clear_fields"] = clear_fields
        draft = self.workflows.prepare(
            conversation_id,
            kind,
            prepared_arguments,
            **prepare_options,
        )
        payload = {
            "version": 1,
            "draftId": draft.id,
            "kind": draft.kind,
            "status": draft.status,
            "missingFields": draft.missing_fields,
            "summary": draft.summary,
        }
        if scheduling_preferences:
            payload["schedulingPreferences"] = scheduling_preferences
        if vehicle_search_preferences:
            payload["vehicleSearchPreferences"] = vehicle_search_preferences
        if selection_evidence:
            payload["selectionEvidence"] = selection_evidence
        if draft.kind in CapabilityRegistry.SPECS and draft.status == "collecting":
            spec = CapabilityRegistry.get(draft.kind)
            missing = set(draft.missing_fields)
            missing_public = spec.next_missing_public(missing)
            secure_fields = [
                field
                for field in spec.secure_fields
                if field in missing or field in spec.optional_fields
            ]
            payload.update(
                {
                    "missingPublicFields": missing_public,
                    "secureFields": secure_fields,
                    "secureInputReady": not missing_public,
                }
            )
            # Composer-visible guidance is limited to the current public question. Protected
            # collectors own later private fields and must not leak them into an earlier turn.
            payload["inputGuidance"] = input_guidance(
                [
                    field
                    for field in missing_public
                    if field not in REFERENCE_FIELD_NAMESPACES
                ]
            )
            self._attach_public_choice(payload, draft.kind, missing_public, dealerships)
        await self._enrich(payload, draft.kind, draft.summary, dealerships)
        return ToolResult(
            "Please review these details. Would you like to confirm the request or change anything?"
            if draft.status == "awaiting_confirmation"
            else "I need a few more details before asking for confirmation.",
            "confirmation" if draft.status == "awaiting_confirmation" else "draft",
            payload,
            payload,
        )

    @staticmethod
    def _attach_public_choice(
        payload: dict,
        kind: str,
        missing_public: list[str],
        dealerships: list[dict[str, Any]] | None,
    ) -> None:
        """Attach authoritative options whenever the next public question is finite."""

        if not missing_public:
            return
        field = missing_public[0]
        source = CapabilityRegistry.choice_source(kind, field)
        if source is None:
            return
        if source == "dealership_directory":
            items = [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "town": item.get("town"),
                }
                for item in dealerships or []
                if item.get("id") and item.get("name")
            ]
            choice_entity_type = "dealership"
            presentation_items = [
                {
                    "label": str(item["name"]),
                    "description": str(item["town"]) if item.get("town") else None,
                }
                for item in items
            ]
        else:
            items = [
                {"id": value, "name": label}
                for value, label in CapabilityRegistry.static_choices(source)
            ]
            choice_entity_type = "workflow_option"
            presentation_items = [
                {"label": str(item["name"]), "description": None} for item in items
            ]
        if not items:
            return
        payload.update(
            {
                "items": items,
                "selectionOnly": True,
                "choiceEntityType": choice_entity_type,
                "choiceField": field,
                "collectionViewType": "choice_list",
                "collectionPresentation": {
                    "schemaVersion": 1,
                    "layout": (
                        "bullet_list"
                        if any(item.get("description") for item in presentation_items)
                        else "chip_grid"
                    ),
                    "purpose": "choice",
                    "items": presentation_items,
                },
            }
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
        if kind in {"vehicle_interest", "sales_enquiry", "test_drive"} and summary.get("vehicleId"):
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
                    "dealershipId",
                    "dealershipName",
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
