"""Convert provider replies and tool facts into persisted assistant responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from webchat.integrations.contracts import ProviderReply
from webchat.orchestration.presentation.suggestions import (
    vehicle_clarification_suggestions,
    vehicle_facet_prompt,
    vehicle_facet_suggestions,
    vehicle_preference_dimension,
    vehicle_starting_point_suggestions,
)
from webchat.orchestration.tools.contracts import ToolExecutor

RENDERABLE_VIEW_TYPES = {
    "business_information",
    "confirmation",
    "dealership_list",
    "draft",
    "offer_list",
    "opening_hours",
    "part_exchange_estimate_form",
    "part_exchange_estimate",
    "private_booking_lookup",
    "service_list",
    "slot_list",
    "suggestion_list",
    "test_drive_slot_picker",
    "vehicle_comparison",
    "vehicle_details",
    "vehicle_availability",
    "vehicle_list",
    "workshop_location_list",
    "workshop_booking_details",
}

DIRECT_ANSWER_TOOLS = {
    "get_business_information",
    "get_service_information",
    "get_vehicle_availability",
}


@dataclass(frozen=True)
class PresentedResponse:
    text: str
    view_type: str | None
    view_payload: dict[str, Any] | None


class ResponsePresenter:
    """Turn provider text and live facts into one persisted assistant response."""

    def __init__(self, tools: ToolExecutor | None):
        self.tools = tools

    async def present(
        self,
        reply: ProviderReply,
        last_tool_result,
        *,
        conversation_id: str,
        user_text: str,
        planned_reply: bool,
        planned_suggestion_dimension: str | None,
    ) -> PresentedResponse:
        render_last_result = bool(
            last_tool_result is not None and is_renderable(last_tool_result)
        )
        view_type = last_tool_result.view_type if render_last_result else None
        view_payload = last_tool_result.view_payload if render_last_result else None
        text = reply.text
        if view_payload is not None:
            return PresentedResponse(text, view_type, view_payload)

        suggestions: list[dict] = []
        dimension = planned_suggestion_dimension
        if dimension == "startingPoint":
            suggestions = vehicle_starting_point_suggestions()
        elif dimension and self.tools is not None:
            suggestions = await self._facet_suggestions(dimension, conversation_id)
        if suggestions and dimension != "startingPoint":
            text = vehicle_facet_prompt(dimension, suggestions)

        if not suggestions and not planned_reply:
            dimension = vehicle_preference_dimension(text)
            if dimension and self.tools is not None:
                suggestions = await self._facet_suggestions(dimension, conversation_id)
            if suggestions:
                text = vehicle_facet_prompt(dimension, suggestions)

        if not suggestions and not planned_reply:
            suggestions = vehicle_clarification_suggestions(user_text, text)
        if suggestions:
            view_type = "suggestion_list"
            view_payload = {"version": 1, "suggestions": suggestions}
        return PresentedResponse(text, view_type, view_payload)

    async def _facet_suggestions(
        self, dimension: str, conversation_id: str
    ) -> list[dict[str, Any]]:
        facets = await self.tools.execute("get_vehicle_facets", {}, conversation_id)
        return vehicle_facet_suggestions(dimension, facets.facts.get(dimension, []))


def is_renderable(result) -> bool:
    return bool(
        result.view_payload is not None and result.view_type in RENDERABLE_VIEW_TYPES
    )
