"""Application-owned suggestion labels and structured actions."""

from __future__ import annotations

import re
from typing import Any


def chip(
    label: str,
    text: str | None = None,
    action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a safe, display-label plus conversational-text suggestion."""
    suggestion: dict[str, Any] = {"label": label, "text": text or label}
    if action:
        suggestion["action"] = action
    return suggestion


def vehicle_search_suggestions(
    *, query: dict[str, Any], item_count: int, page: int, page_size: int, total: int
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    if page * page_size < total:
        suggestions.append(
            chip(
                "Show me more",
                "show me more",
                {"type": "next_vehicle_page"},
            )
        )
    if 2 <= item_count <= 4:
        suggestions.append(
            chip(
                "Compare these vehicles",
                "compare the vehicles currently shown",
                {"type": "compare_displayed_vehicles"},
            )
        )
    if item_count and query.get("availability") in {None, "available"}:
        suggestions.append(
            chip(
                "Book a test drive",
                "book a test drive for one of these vehicles",
            )
        )
    suggestions.extend(vehicle_filter_controls(query) or vehicle_discovery_controls())
    if query.get("sort") != "priceAsc":
        suggestions.append(chip("Cheapest first", "show me the cheapest matching vehicles first"))
    if len(suggestions) == 3:
        suggestions.append(chip("Find another car", "help me find another car"))
    return suggestions[:4]


def current_page_vehicle_suggestions(item_count: int) -> list[dict[str, Any]]:
    """Offer typed next steps when a search was explicitly scoped to page vehicles."""
    suggestions: list[dict[str, Any]] = []
    if item_count >= 2:
        suggestions.append(
            chip(
                "Compare these vehicles",
                "compare the vehicles currently shown",
                {"type": "compare_displayed_vehicles"},
            )
        )
    if item_count:
        suggestions.append(
            chip(
                "Book a test drive",
                "book a test drive for one of these vehicles",
            )
        )
    suggestions.append(
        chip(
            "Search full inventory",
            "search the full vehicle inventory with these filters",
            {"type": "search_vehicle_inventory"},
        )
    )
    suggestions.extend(
        [
            chip("Find another car", "help me find another car"),
            chip("Current offers", "show me current offers"),
        ]
    )
    if len(suggestions) == 3:
        suggestions.append(chip("Cheapest cars", "show me the cheapest available cars"))
    return suggestions[:4]


def no_vehicle_results_suggestions(query: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    suggestions = vehicle_filter_controls(query or {}) or vehicle_discovery_controls()
    suggestions.extend(
        [
            chip(
                "Show all cars",
                "show me all available cars",
                {"type": "reset_vehicle_search"},
            ),
            chip("Cheapest cars", "show me the cheapest available cars"),
        ]
    )
    return suggestions[:4]


def vehicle_filter_controls(query: dict[str, Any]) -> list[dict[str, Any]]:
    """Offer deterministic change/clear controls for the most useful active filter."""
    controls = {
        "fuelType": "fuel",
        "make": "make",
        "model": "model",
        "bodyStyle": "body style",
        "transmission": "gearbox",
        "maxPricePence": "budget",
        "maxMileage": "mileage",
    }
    for filter_name, label in controls.items():
        if query.get(filter_name) in (None, ""):
            continue
        return [
            chip(
                f"Change {label}",
                f"change my current {label} filter",
                {
                    "type": "choose_vehicle_filter",
                    "vehicleFilter": filter_name,
                },
            ),
            chip(
                f"Any {label}",
                f"show matching cars with any {label}",
                {
                    "type": "clear_vehicle_filter",
                    "vehicleFilter": filter_name,
                },
            ),
        ]
    return []


def vehicle_discovery_controls() -> list[dict[str, Any]]:
    """Offer deterministic starting refinements when no filter is active."""
    return [
        chip(
            "Choose fuel",
            "I would like to choose a fuel type",
            {"type": "choose_vehicle_filter", "vehicleFilter": "fuelType"},
        ),
        chip(
            "Set budget",
            "I would like to set a budget",
            {"type": "choose_vehicle_filter", "vehicleFilter": "maxPricePence"},
        ),
    ]


def vehicle_filter_summary(query: dict[str, Any]) -> str:
    """Describe active search constraints using stable customer-facing labels."""
    parts: list[str] = []
    text_filters = (
        ("q", "Search"),
        ("make", "Make"),
        ("model", "Model"),
        ("colour", "Colour"),
        ("fuelType", "Fuel"),
        ("transmission", "Gearbox"),
        ("bodyStyle", "Body style"),
        ("dealershipTown", "Location"),
    )
    for field, label in text_filters:
        if value := query.get(field):
            parts.append(f"{label}: {value}")
    inclusion_filters = (
        ("makes", "Makes"),
        ("models", "Models"),
        ("colours", "Colours"),
        ("fuelTypes", "Fuels"),
        ("transmissions", "Gearboxes"),
        ("bodyStyles", "Body styles"),
    )
    for field, label in inclusion_filters:
        values = query.get(field)
        if values:
            parts.append(f"{label}: {', '.join(str(value) for value in values)}")
    numeric_filters = (
        ("minPricePence", "Minimum price", lambda value: f"£{int(value) / 100:,.0f}"),
        ("maxPricePence", "Maximum price", lambda value: f"£{int(value) / 100:,.0f}"),
        ("minMileage", "Minimum mileage", lambda value: f"{int(value):,} miles"),
        ("maxMileage", "Maximum mileage", lambda value: f"{int(value):,} miles"),
        ("minYear", "From year", lambda value: str(value)),
        ("maxYear", "Up to year", lambda value: str(value)),
    )
    for field, label, formatter in numeric_filters:
        if query.get(field) is not None:
            parts.append(f"{label}: {formatter(query[field])}")
    exclusion_filters = (
        ("excludedMakes", "Excluding makes"),
        ("excludedModels", "Excluding models"),
        ("excludedFuelTypes", "Excluding fuels"),
        ("excludedTransmissions", "Excluding gearboxes"),
        ("excludedBodyStyles", "Excluding body styles"),
    )
    for field, label in exclusion_filters:
        values = query.get(field)
        if values:
            parts.append(f"{label}: {', '.join(str(value) for value in values)}")
    if query.get("availability") and query["availability"] != "available":
        parts.append(f"Availability: {str(query['availability']).title()}")
    sort_labels = {
        "priceAsc": "lowest price first",
        "priceDesc": "highest price first",
        "mileageAsc": "lowest mileage first",
    }
    if query.get("sort") in sort_labels:
        parts.append(f"Order: {sort_labels[query['sort']]}")
    return "; ".join(parts)


def vehicle_comparison_suggestions(
    vehicles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Expose safe vehicle-bound actions for the AI to select after a comparison.

    Generic conversational next steps belong to the response composer as quick replies. The
    application only supplies these candidates because a test-drive click must carry a trusted
    vehicle ID.
    """

    suggestions: list[dict[str, Any]] = []
    for vehicle in vehicles[:4]:
        vehicle_id = str(vehicle.get("id") or "")
        if not re.fullmatch(r"veh-[0-9]{3}", vehicle_id):
            continue
        identity = " ".join(
            part
            for part in (
                str(vehicle.get("make") or "").strip(),
                str(vehicle.get("model") or "").strip(),
            )
            if part
        )
        if not identity:
            continue
        suggestions.append(
            chip(
                f"Book {identity}",
                f"Book a test drive for the {identity}",
                {"type": "select_test_drive_vehicle", "vehicleId": vehicle_id},
            )
        )
    # A comparison question is a complete set, not an optional shortcut menu. Three compared
    # vehicles therefore need a fourth honest exit rather than dropping one vehicle to satisfy
    # the global two-or-four chip layout contract.
    if len(suggestions) == 3:
        suggestions.append(chip("Find another car", "help me find another car"))
    return suggestions


def vehicle_resolution_suggestions(vehicles: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Expose only materially distinct trusted candidates for a comparison slot."""

    return [
        chip(
            f"{vehicle.get('make', '')} {vehicle.get('model', '')}".strip(),
            f"Use the {vehicle.get('make', '')} {vehicle.get('model', '')} for the comparison".strip(),
        )
        for vehicle in vehicles[:4]
        if vehicle.get("make") and vehicle.get("model")
    ]


def vehicle_availability_suggestions(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Use the platform's permitted next actions for the checked vehicle status."""
    vehicle_id = str(data.get("vehicleId") or "")
    if not vehicle_id:
        return []
    suggestions: list[dict[str, Any]] = []
    if data.get("canBookTestDrive"):
        suggestions.append(
            chip(
                "Book a test drive",
                "Book a test drive for this vehicle",
                {"type": "select_test_drive_vehicle", "vehicleId": vehicle_id},
            )
        )
    if data.get("canRegisterInterest"):
        suggestions.append(
            chip(
                "Register interest",
                "Register my interest in this vehicle",
                {"type": "start_vehicle_interest", "vehicleId": vehicle_id},
            )
        )
    if data.get("canEnquire"):
        suggestions.append(
            chip(
                "Send a sales enquiry",
                "Send a sales enquiry about this vehicle",
                {"type": "start_sales_enquiry", "vehicleId": vehicle_id},
            )
        )
    suggestions.append(chip("Find another car", "help me find another car"))
    if len(suggestions) == 3:
        suggestions.append(chip("Current offers", "show me current offers"))
    return suggestions[:4]


def test_drive_no_availability_suggestions(vehicle_id: str) -> list[dict[str, Any]]:
    """Offer exits from a no-slot outcome without restarting the same failed action."""

    return [
        chip(
            "Find another car",
            "help me choose another car for the test drive",
            {"type": "reset_vehicle_search"},
        ),
        chip(
            "Send a sales enquiry",
            "Send a sales enquiry about this vehicle",
            {"type": "start_sales_enquiry", "vehicleId": vehicle_id},
        ),
    ]


def workshop_booking_status_suggestions(status: str | None) -> list[dict[str, Any]]:
    """Offer only next steps that are valid for the booking's trusted status."""

    normalized = str(status or "").strip().casefold()
    if normalized == "confirmed":
        return [
            chip("Edit booking", "edit this workshop booking"),
            chip("Cancel booking", "cancel this workshop booking"),
        ]
    if normalized in {"cancelled", "canceled"}:
        return [
            chip("Book an appointment", "book a new workshop appointment"),
            chip("Find another booking", "find another existing workshop booking"),
        ]
    return []


def workshop_booking_status_prompt(status: str | None) -> str | None:
    """Return the explicit question owned by the status-specific reply set."""

    normalized = str(status or "").strip().casefold()
    if normalized == "confirmed":
        return "Would you like to edit or cancel this booking?"
    if normalized in {"cancelled", "canceled"}:
        return "Would you like to book another appointment or find another booking?"
    return None


def dealership_suggestions() -> list[dict[str, Any]]:
    return [
        chip(
            "Opening hours",
            "show me the opening hours",
            {"type": "show_opening_hours"},
        ),
        chip("Departments", "what departments do the dealerships have?"),
        chip(
            "Request a callback",
            "please have the dealership call me",
            {"type": "start_callback"},
        ),
        chip(
            "Leave a message",
            "send a message to the dealership",
            {"type": "start_dealership_message"},
        ),
    ][:4]


def dealership_contact_suggestions() -> list[dict[str, Any]]:
    """Offer every safe contact path without selecting one for the customer."""
    return [
        chip(
            "Request a callback",
            "please have a dealership call me",
            {"type": "start_callback"},
        ),
        chip(
            "Send a message",
            "send a message to a dealership",
            {"type": "start_dealership_message"},
        ),
        chip(
            "Contact details",
            "show me dealership contact details",
            {"type": "show_dealerships"},
        ),
        chip(
            "Opening hours",
            "show me dealership opening hours",
            {"type": "show_opening_hours"},
        ),
    ]


def unknown_dealership_suggestions() -> list[dict[str, Any]]:
    return [
        chip("Show all locations", "show me all dealership locations"),
        chip("Opening hours", "show me the opening hours"),
        chip("Departments", "what departments do the dealerships have?"),
        chip(
            "Request a callback",
            "please have a dealership call me",
            {"type": "start_callback"},
        ),
    ][:4]


def opening_hours_suggestions() -> list[dict[str, Any]]:
    return [
        chip(
            "Dealership details",
            "show me dealership contact details",
            {"type": "show_dealerships"},
        ),
        chip(
            "Request a callback",
            "please have the dealership call me",
            {"type": "start_callback"},
        ),
        chip("Departments", "what departments do the dealerships have?"),
        chip(
            "Leave a message",
            "send a message to the dealership",
            {"type": "start_dealership_message"},
        ),
    ][:4]


def service_type_suggestions(
    services: list[dict[str, Any]], dealership_id: str | None = None
) -> list[dict[str, str]]:
    """Expose each live service as an actionable choice when a service is required."""
    suggestions: list[dict[str, str]] = []
    for service in services[:8]:
        name = str(service.get("name", "")).strip()
        service_id = str(service.get("id", "")).strip()
        if name and service_id:
            suggestions.append(
                chip(
                    name,
                    f"Book service: {name}",
                    (
                        {
                            "type": "try_workshop_location",
                            "serviceTypeId": service_id,
                            "dealershipId": dealership_id,
                        }
                        if dealership_id
                        else {"type": "select_workshop_service", "serviceTypeId": service_id}
                    ),
                )
            )
    return suggestions


def workshop_location_suggestions() -> list[dict[str, str]]:
    """Offer next steps after locations, without repeating the fulfilled request."""
    return [
        chip("Book a service", "I want to book a workshop appointment"),
        chip("Find my booking", "find my existing workshop booking"),
        chip("Service types", "what service types do you support?"),
        chip("Request a callback", "please have the service department call me"),
    ][:4]


def workshop_booking_location_suggestions(
    locations: list[dict[str, Any]], service_type_id: str
) -> list[dict[str, Any]]:
    """Grounded location selections for an active booking, outside visual cards."""
    suggestions: list[dict[str, Any]] = []
    for location in locations[:4]:
        dealership_id = str(location.get("id") or "").strip()
        label = str(location.get("town") or location.get("name") or "").strip()
        if dealership_id and label:
            suggestions.append(
                chip(
                    label,
                    f"Use the workshop in {label}",
                    {
                        "type": "try_workshop_location",
                        "serviceTypeId": service_type_id,
                        "dealershipId": dealership_id,
                    },
                )
            )
    return suggestions


def workshop_no_availability_suggestions(
    slots: list[dict[str, Any]], service_name: str | None, service_type_id: str | None = None
) -> list[dict[str, str]]:
    """Offer live alternative locations when a selected workshop has no slots."""
    suggestions: list[dict[str, str]] = []
    seen_towns: set[str] = set()
    for slot in slots:
        town = str(slot.get("dealershipTown") or "").strip()
        town_key = town.casefold()
        if not town or town_key in seen_towns:
            continue
        seen_towns.add(town_key)
        appointment = (
            f"an appointment for {service_name}" if service_name else "a workshop appointment"
        )
        dealership_id = str(slot.get("dealershipId") or "").strip()
        action = (
            {
                "type": "try_workshop_location",
                "serviceTypeId": service_type_id,
                "dealershipId": dealership_id,
            }
            if service_type_id and dealership_id
            else None
        )
        suggestions.append(chip(f"Try {town}", f"Find {appointment} in {town}", action))
        if len(suggestions) == 3:
            break
    suggestions.append(
        chip(
            "Choose another service",
            "find a workshop appointment",
            {"type": "show_workshop_services"},
        )
    )
    if len(suggestions) == 1:
        suggestions.append(chip("Workshop locations", "show me workshop locations"))
    elif len(suggestions) == 3:
        suggestions.append(
            chip("Request a callback", "please have the service department call me")
        )
    return suggestions[:4]


def offer_suggestions(offer: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if not offer or not offer.get("id"):
        return []
    return [
        chip(
            "Enquire about this offer",
            "I want to enquire about this offer",
            {"type": "start_offer_enquiry", "offerId": str(offer["id"])},
        )
    ]


def offer_selection_suggestions(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Provide the AI with offer-bound choices that answer an offer-selection question."""

    suggestions: list[dict[str, Any]] = []
    for offer in offers[:4]:
        offer_id = str(offer.get("id") or "")
        if not offer_id:
            continue
        identity = " ".join(
            part
            for part in (
                str(offer.get("make") or "").strip(),
                str(offer.get("model") or "").strip(),
            )
            if part
        )
        if not identity:
            continue
        suggestions.append(
            chip(
                identity,
                f"Show me the {identity} offer",
                {"type": "view_offer", "offerId": offer_id},
            )
        )
    return suggestions


def vehicle_facet_suggestions(dimension: str, values: list[Any]) -> list[dict[str, str]]:
    """Turn current inventory facets into conversational selection chips."""
    if dimension == "budgets":
        return [
            chip(
                f"Under £{int(value) / 100:,.0f}",
                f"Show me cars under £{int(value) / 100:,.0f}",
                {
                    "type": "apply_vehicle_preference",
                    "vehicleFilter": "maxPricePence",
                    "vehicleFilterValue": int(value),
                },
            )
            for value in values[:4]
            if isinstance(value, int) and value > 0
        ]
    if dimension == "mileages":
        return [
            chip(
                f"Under {int(value):,} miles",
                f"Show me cars under {int(value):,} miles",
                {
                    "type": "apply_vehicle_preference",
                    "vehicleFilter": "maxMileage",
                    "vehicleFilterValue": int(value),
                },
            )
            for value in values[:4]
            if isinstance(value, int) and value > 0
        ]
    phrase = {
        "transmissions": "I prefer {value} transmission",
        "fuelTypes": "I prefer {value}",
        "bodyStyles": "I prefer the {value} body style",
        "makes": "Show me {value} cars",
        "models": "Show me {value}",
    }.get(dimension, "{value}")
    filter_name = {
        "transmissions": "transmission",
        "fuelTypes": "fuelType",
        "bodyStyles": "bodyStyle",
        "makes": "make",
        "models": "model",
    }.get(dimension)
    return [
        chip(
            str(value),
            phrase.format(value=value),
            {
                "type": "apply_vehicle_preference",
                "vehicleFilter": str(filter_name),
                "vehicleFilterValue": str(value),
            },
        )
        for value in values[:8]
        if filter_name and str(value).strip()
    ]


def vehicle_starting_point_suggestions() -> list[dict[str, str]]:
    """Application-owned next actions for broad vehicle discovery."""
    return [
        chip("Set a budget", "I would like to set a budget"),
        chip("Choose fuel", "I would like to choose a fuel type"),
        chip("Choose body style", "I would like to choose a body style"),
        chip("Choose gearbox", "I would like to choose a gearbox"),
    ]


def vehicle_facet_prompt(dimension: str, suggestions: list[dict[str, str]]) -> str:
    """Keep a preference question truthful to the live options shown beneath it."""
    labels = {
        "budgets": "maximum budget",
        "mileages": "maximum mileage",
        "transmissions": "gearbox",
        "fuelTypes": "fuel type",
        "bodyStyles": "body style",
        "makes": "manufacturer",
        "models": "model",
    }
    label = labels.get(dimension, "option")
    values = [
        str(suggestion["label"]).strip() for suggestion in suggestions if suggestion.get("label")
    ]
    if len(values) == 1:
        return f"Northstar currently has {values[0]} {label} options. Choose it below to see matching cars."
    article = "an" if label.startswith(("a", "e", "i", "o", "u")) else "a"
    return f"Choose {article} {label} below."
