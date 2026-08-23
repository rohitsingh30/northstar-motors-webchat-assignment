from __future__ import annotations

from webchat.orchestration.catalogue import UnifiedToolCatalog

DEFAULT_RENDERERS = frozenset(
    {
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
)


class RendererRegistry:
    """Validate that tool results use application-owned renderer contracts."""

    def __init__(self, catalogue: UnifiedToolCatalog | None = None):
        self._catalogue = catalogue
        self._renderers = (
            {
                renderer
                for definition in catalogue.definitions()
                for renderer in definition.allowed_renderers
            }
            if catalogue is not None
            else set(DEFAULT_RENDERERS)
        )

    def can_render(self, tool_name: str | None, result) -> bool:
        if result.view_payload is None or result.view_type is None:
            return False
        if self._catalogue is None or tool_name is None:
            return result.view_type in self._renderers
        return result.view_type in self._catalogue.get(tool_name).allowed_renderers
