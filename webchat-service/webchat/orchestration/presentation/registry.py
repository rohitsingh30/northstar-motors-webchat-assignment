from __future__ import annotations

SUPPORTED_TOOL_RESULT_VIEWS = frozenset(
    {
        "business_information",
        "choice_list",
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
)


class RendererRegistry:
    """Reject unknown tool-result views without maintaining a tool-to-view matrix."""

    @staticmethod
    def can_render(result) -> bool:
        if result.view_payload is None or result.view_type is None:
            return False
        return result.view_type in SUPPORTED_TOOL_RESULT_VIEWS
