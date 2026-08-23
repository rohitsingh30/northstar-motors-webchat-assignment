"""Application-owned suggestion labels and structured actions."""

from __future__ import annotations

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
        ("fuelType", "Fuel"),
        ("transmission", "Gearbox"),
        ("bodyStyle", "Body style"),
        ("dealershipTown", "Location"),
    )
    for field, label in text_filters:
        if value := query.get(field):
            parts.append(f"{label}: {value}")
    numeric_filters = (
        ("minPricePence", "Minimum price", lambda value: f"£{int(value) / 100:,.0f}"),
        ("maxPricePence", "Maximum price", lambda value: f"£{int(value) / 100:,.0f}"),
        ("maxMileage", "Maximum mileage", lambda value: f"{int(value):,} miles"),
        ("minYear", "From year", lambda value: str(value)),
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


def vehicle_comparison_suggestions() -> list[dict[str, str]]:
    return [
        chip("Show cheapest cars", "show me the cheapest available cars"),
        chip("Find another car", "help me find another car"),
        chip("Current offers", "show me current offers"),
        chip("Part-exchange estimate", "I want an indicative part-exchange estimate"),
    ][:4]


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
    suggestions.extend(
        [
            chip("Find another car", "help me find another car"),
            chip("Part-exchange estimate", "I want an indicative part-exchange estimate"),
        ]
    )
    if len(suggestions) == 3:
        suggestions.append(chip("Current offers", "show me current offers"))
    return suggestions[:4]


def dealership_suggestions() -> list[dict[str, str]]:
    return [
        chip("Opening hours", "show me the opening hours"),
        chip("Departments", "what departments do the dealerships have?"),
        chip("Request a callback", "please have the dealership call me"),
        chip("Leave a message", "send a message to the dealership"),
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


def unknown_dealership_suggestions() -> list[dict[str, str]]:
    return [
        chip("Show all locations", "show me all dealership locations"),
        chip("Opening hours", "show me the opening hours"),
        chip("Departments", "what departments do the dealerships have?"),
        chip("Request a callback", "please have a dealership call me"),
    ][:4]


def opening_hours_suggestions() -> list[dict[str, str]]:
    return [
        chip("Dealership details", "show me dealership contact details"),
        chip("Request a callback", "please have the dealership call me"),
        chip("Departments", "what departments do the dealerships have?"),
        chip("Leave a message", "send a message to the dealership"),
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
    suggestions: list[dict[str, Any]] = []
    if offer and offer.get("id"):
        suggestions.append(
            chip(
                "Enquire about this offer",
                "I want to enquire about this offer",
                {"type": "start_offer_enquiry", "offerId": str(offer["id"])},
            )
        )
    suggestions.extend(
        [
            chip("Show available cars", "show me available cars"),
            chip("Part-exchange estimate", "I want an indicative part-exchange estimate"),
            chip("Finance information", "how does vehicle finance work?"),
            chip("Dealership locations", "show me all dealership locations"),
        ]
    )
    return suggestions[:4]


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
