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


def remove_repeated_intent(
    suggestions: list[dict[str, str]], user_text: str
) -> list[dict[str, str]]:
    """Remove a follow-up that merely restates the user's current request."""
    current_intent = _suggestion_intent(user_text)
    if not current_intent:
        return suggestions
    return [
        suggestion
        for suggestion in suggestions
        if _suggestion_intent(suggestion.get("text", "")) != current_intent
    ]


def _suggestion_intent(text: str) -> str | None:
    words = set(text.lower().replace("-", " ").split())
    if {"opening", "hours"}.issubset(words):
        return "opening_hours"
    if "workshop" in words and words.intersection({"location", "locations", "where", "address"}):
        return "workshop_locations"
    if words.intersection({"appointment", "appointments", "slot", "slots", "times"}) and words.intersection(
        {"workshop", "service", "find", "available"}
    ):
        return "workshop_appointment"
    if "booking" in words and words.intersection({"find", "existing", "lookup", "amend", "cancel"}):
        return "booking_lookup"
    if words.intersection({"contact", "phone", "email", "address"}) and words.intersection(
        {"dealership", "dealer", "location", "locations"}
    ):
        return "dealership_details"
    return None


def vehicle_search_suggestions(
    *, query: dict[str, Any], item_count: int, page: int, page_size: int, total: int
) -> list[dict[str, str]]:
    suggestions: list[dict[str, str]] = []
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
    if query.get("sort") != "priceAsc":
        suggestions.append(chip("Cheapest first", "show me the cheapest matching vehicles first"))
    return suggestions[:4]


def no_vehicle_results_suggestions() -> list[dict[str, str]]:
    return [
        chip("Show all cars", "show me all available cars"),
        chip("Cheapest cars", "show me the cheapest available cars"),
    ][:2]


def vehicle_comparison_suggestions() -> list[dict[str, str]]:
    return [
        chip("Show cheapest cars", "show me the cheapest available cars"),
        chip("Find another car", "help me find another car"),
    ][:2]


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
    return suggestions[:2]


def dealership_suggestions() -> list[dict[str, str]]:
    return [
        chip("Opening hours", "show me the opening hours"),
        chip("Departments", "what departments do the dealerships have?"),
        chip("Request a callback", "please have the dealership call me"),
    ][:2]


def unknown_dealership_suggestions() -> list[dict[str, str]]:
    return [
        chip("Show all locations", "show me all dealership locations"),
    ]


def opening_hours_suggestions() -> list[dict[str, str]]:
    return [
        chip("Dealership details", "show me dealership contact details"),
        chip("Request a callback", "please have the dealership call me"),
    ][:2]


def service_suggestions() -> list[dict[str, str]]:
    return [
        chip("Find an appointment", "find a workshop appointment"),
        chip("Find my booking", "find my existing workshop booking"),
    ][:2]


def service_type_suggestions(services: list[dict[str, Any]]) -> list[dict[str, str]]:
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
                    {"type": "select_workshop_service", "serviceTypeId": service_id},
                )
            )
    return suggestions


def workshop_location_suggestions() -> list[dict[str, str]]:
    """Offer next steps after locations, without repeating the fulfilled request."""
    return [
        chip("Book a service", "I want to book a workshop appointment"),
        chip("Find my booking", "find my existing workshop booking"),
    ][:2]


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
            f"an appointment for {service_name}"
            if service_name
            else "a workshop appointment"
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
        suggestions.append(
            chip(f"Try {town}", f"Find {appointment} in {town}", action)
        )
        if len(suggestions) == 3:
            break
    suggestions.append(
        chip(
            "Choose another service",
            "find a workshop appointment",
            {"type": "show_workshop_services"},
        )
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
        ]
    )
    return suggestions[:3]


def vehicle_clarification_suggestions(
    user_text: str, assistant_text: str
) -> list[dict[str, str]]:
    """Offer answers to the decision being asked, or broad discovery refinements."""
    user = user_text.lower()
    assistant = assistant_text.lower()
    discovery_terms = (
        "find another car",
        "what kind of car",
        "what matters most",
        "starting point",
        "narrow",
        "preferences",
    )
    # Broad refinement chips belong only to an explicit car-discovery prompt. A
    # workflow may legitimately ask for mileage, condition, or another attribute
    # without becoming a vehicle-search conversation.
    if not any(term in assistant for term in discovery_terms):
        return []
    if vehicle_preference_dimension(assistant):
        return []
    suggestions: list[dict[str, str]] = []
    if "budget" not in user:
        suggestions.append(chip("Set a budget", "I would like to set a budget"))
    if not any(term in user for term in ("fuel", "petrol", "diesel", "hybrid", "electric")):
        suggestions.append(chip("Choose fuel", "I would like to choose a fuel type"))
    if not any(term in user for term in ("body style", "suv", "hatchback", "saloon", "estate")):
        suggestions.append(chip("Choose body style", "I would like to choose a body style"))
    if not any(term in user for term in ("automatic", "manual")):
        suggestions.append(chip("Choose gearbox", "I would like to choose a gearbox"))
    return suggestions[:4]


def vehicle_preference_dimension(assistant_text: str) -> str | None:
    """Identify a preference dimension without embedding its allowed values."""
    text = assistant_text.lower()
    dimensions = (
        ("transmissions", ("gearbox", "automatic", "manual")),
        ("fuelTypes", ("fuel", "petrol", "diesel", "hybrid", "electric")),
        ("bodyStyles", ("body style", "suv", "hatchback", "saloon", "estate")),
        ("makes", ("make", "manufacturer", "brand")),
        ("models", ("model",)),
    )
    matches = [
        (min(text.find(term) for term in terms if text.find(term) >= 0), dimension)
        for dimension, terms in dimensions
        if any(term in text for term in terms)
    ]
    return min(matches, default=(0, None))[1]


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
    values = [str(suggestion["label"]).strip() for suggestion in suggestions if suggestion.get("label")]
    if len(values) == 1:
        return f"Northstar currently has {values[0]} {label} options. Choose it below to see matching cars."
    article = "an" if label.startswith(("a", "e", "i", "o", "u")) else "a"
    return f"Choose {article} {label} below."
