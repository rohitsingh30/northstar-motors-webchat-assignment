"""Application-owned compatibility between declared customer intents and business tools.

The hosted planner must declare *why* it is calling a tool.  This registry prevents a factually
valid but semantically wrong operation (for example, filtering page vehicles for a comparison)
from reaching execution.
"""

from __future__ import annotations

_TOOL_INTENTS: dict[str, frozenset[str]] = {
    "cancel_active_capability": frozenset({"capability"}),
    "resume_paused_capability": frozenset({"capability"}),
    "search_vehicles": frozenset({"vehicle_search"}),
    "reset_vehicle_search": frozenset({"vehicle_search", "vehicle_refinement"}),
    "refine_vehicle_search": frozenset({"vehicle_refinement"}),
    "select_page_vehicles": frozenset({"vehicle_search", "vehicle_refinement"}),
    "get_vehicle_facets": frozenset({"vehicle_search", "vehicle_refinement"}),
    "show_vehicle_preferences": frozenset({"vehicle_search", "vehicle_refinement"}),
    "get_vehicle": frozenset({"vehicle_detail"}),
    "get_vehicle_availability": frozenset({"vehicle_availability"}),
    "resolve_vehicle_availability": frozenset({"vehicle_availability"}),
    "compare_vehicles": frozenset({"vehicle_comparison"}),
    "compare_vehicle_models": frozenset({"vehicle_comparison"}),
    "list_offers": frozenset({"offer_discovery"}),
    "get_offer": frozenset({"offer_detail"}),
    "list_dealerships": frozenset({"dealership_discovery", "dealership_detail"}),
    "show_dealership_contact_options": frozenset({"dealership_detail"}),
    "list_dealership_departments": frozenset({"dealership_discovery"}),
    "find_dealership_departments": frozenset({"dealership_detail"}),
    "get_dealership": frozenset({"dealership_detail"}),
    "get_opening_hours": frozenset({"opening_hours"}),
    "list_opening_hours": frozenset({"opening_hours"}),
    "list_holiday_opening_hours": frozenset({"holiday_opening_hours"}),
    "get_business_information": frozenset({"business_information"}),
    "get_service_information": frozenset({"service_detail"}),
    "list_service_types": frozenset({"service_discovery", "workshop_booking"}),
    "list_workshop_locations": frozenset({"dealership_discovery", "workshop_booking"}),
    "list_test_drive_slots": frozenset({"test_drive"}),
    "list_workshop_slots": frozenset(
        {"workshop_availability", "workshop_booking", "booking_amendment"}
    ),
    "refine_workshop_slots": frozenset(
        {"workshop_availability", "workshop_booking", "booking_amendment"}
    ),
    "request_workshop_booking_lookup_form": frozenset(
        {"booking_lookup", "booking_amendment", "booking_cancellation"}
    ),
    "request_part_exchange_estimate_form": frozenset({"part_exchange"}),
    "estimate_part_exchange": frozenset({"part_exchange"}),
    "request_offer_enquiry_form": frozenset({"sales_enquiry"}),
    "prepare_sales_enquiry": frozenset({"sales_enquiry"}),
    "prepare_test_drive": frozenset({"test_drive"}),
    "prepare_vehicle_interest": frozenset({"vehicle_interest"}),
    "prepare_callback": frozenset({"callback"}),
    "prepare_workshop_booking": frozenset({"workshop_booking"}),
    "prepare_workshop_amendment": frozenset({"booking_amendment"}),
    "prepare_workshop_cancellation": frozenset({"booking_cancellation"}),
    "prepare_dealership_message": frozenset({"dealership_message"}),
    "prepare_part_exchange": frozenset({"part_exchange"}),
}


def supported_intents(tool_name: str) -> frozenset[str]:
    """Return the closed intent set for an application tool.

    Optional external read-only MCP tools use the generic capability intent because their names
    are configuration-defined rather than repository-defined.
    """

    return _TOOL_INTENTS.get(tool_name, frozenset({"capability"}))


def primary_intent(tool_name: str) -> str:
    """Compatibility inference for injected test providers that predate intent declarations."""

    return min(supported_intents(tool_name))
