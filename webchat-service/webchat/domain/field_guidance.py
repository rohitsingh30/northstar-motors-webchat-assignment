"""Application-owned guidance for every conversational workflow input.

The model decides how to converse, but it must not invent identifier shapes, examples, or entry
rules. Collectors, application fallbacks, and the browser all derive their help from this contract.
"""

from __future__ import annotations

from typing import Any

FIELD_INPUT_GUIDANCE: dict[str, dict[str, Any]] = {
    "preferredDayOrDate": {
        "label": "preferred day or date",
        "prompt": "What day or date would suit you? For example, Monday or 8 September.",
    },
    "approximateTime": {
        "label": "approximate time",
        "prompt": "Roughly what time works best? For example, morning or afternoon.",
    },
    "slotId": {
        "label": "appointment time",
        "prompt": (
            "Confirm whether the offered appointment suits you, or choose from the available times."
        ),
    },
    "registration": {
        "label": "vehicle registration",
        "prompt": "What’s your vehicle registration?",
        "example": "AB12 CDE",
    },
    "mileage": {
        "label": "current mileage",
        "prompt": "What’s the vehicle’s current mileage? Enter the dashboard reading.",
        "example": "24,000 miles",
    },
    "condition": {
        "label": "vehicle condition",
        "prompt": "How would you describe the vehicle’s condition? Choose Excellent, Good, or Fair.",
        "choices": ["Excellent", "Good", "Fair"],
    },
    "firstName": {
        "label": "first and last name",
        "prompt": "What’s your first and last name?",
        "example": "Alex Morgan",
    },
    "lastName": {
        "label": "surname",
        "prompt": "What surname is the booking under? Enter it exactly as used for the booking.",
    },
    "email": {
        "label": "email address",
        "prompt": "What email address should Northstar use?",
        "example": "name@example.com",
    },
    "phone": {
        "label": "UK phone number",
        "prompt": "What’s the best UK phone number to reach you on?",
        "example": "07700 900123",
    },
    "reference": {
        "label": "booking reference",
        "prompt": ("What’s your booking reference? It is shown in your confirmation email."),
        "example": "WORK-12345",
        "whereToFind": "confirmation email",
    },
    "dealershipId": {
        "label": "dealership",
        "prompt": "Which dealership would you like? Give the town or choose a location shown.",
    },
    "department": {
        "label": "department",
        "prompt": "Which team do you need: Sales, Service, Parts, or General enquiries?",
        "choices": ["Sales", "Service", "Parts", "General enquiries"],
    },
    "reason": {
        "label": "reason for contact",
        "prompt": "What would you like the team to help with? A short description is enough.",
    },
    "message": {
        "label": "message",
        "prompt": "What should the dealership know? Include the specific question or help you need.",
    },
    "preferredContactMethod": {
        "label": "contact method",
        "prompt": "How would you prefer the dealership to reply: email or phone?",
        "choices": ["Email", "Phone"],
    },
}


def field_prompt(field: str) -> str:
    guidance = FIELD_INPUT_GUIDANCE.get(field)
    if not guidance:
        return f"Please provide {field}."
    prompt = str(guidance["prompt"])
    example = str(guidance.get("example") or "").strip()
    if example:
        prompt = f"{prompt} For example, {example}."
    return prompt


def input_guidance(fields: list[str] | tuple[str, ...]) -> dict[str, dict[str, Any]]:
    return {
        field: dict(FIELD_INPUT_GUIDANCE[field])
        for field in fields
        if field in FIELD_INPUT_GUIDANCE
    }
