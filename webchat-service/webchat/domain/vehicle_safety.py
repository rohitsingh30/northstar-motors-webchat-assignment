"""Deterministic first-response guidance for unambiguous active vehicle hazards."""

import re

_FIRE = re.compile(
    r"\b(?:flames?|on fire|engine[- ]bay fire)\b|"
    r"\bsmoke\b[\s\S]{0,50}\b(?:bonnet|engine bay|engine compartment)\b|"
    r"\b(?:bonnet|engine bay|engine compartment)\b[\s\S]{0,50}\bsmoke\b",
    re.IGNORECASE,
)
_OVERHEATING = re.compile(
    r"\b(?:temperature (?:gauge )?(?:is )?(?:in the )?red|overheating|steam(?:ing)?)\b",
    re.IGNORECASE,
)
_BRAKE_LOSS = re.compile(
    r"\b(?:soft|spongy|no pressure|to the floor)\b[\s\S]{0,80}\bbrake|"
    r"\bbrake[\s\S]{0,80}\b(?:soft|spongy|no pressure|to the floor)\b",
    re.IGNORECASE,
)
_FUEL_LEAK = re.compile(
    r"\b(?:fuel|petrol|diesel)\b[\s\S]{0,30}\b(?:leak|leaking|strong smell|smell)\b|"
    r"\b(?:leak|leaking|strong smell)\b[\s\S]{0,30}\b(?:fuel|petrol|diesel)\b",
    re.IGNORECASE,
)
_STEERING_LOSS = re.compile(
    r"\b(?:lost|loss of|no|failed|failure|very heavy)\b[\s\S]{0,30}\bsteering\b|"
    r"\bsteering\b[\s\S]{0,30}\b(?:lost|gone|failed|very heavy)\b",
    re.IGNORECASE,
)


def immediate_vehicle_safety_guidance(text: str) -> str | None:
    """Return approved harm-reduction wording, without attempting a diagnosis."""

    value = str(text or "")
    if _FIRE.search(value):
        return (
            "Get everyone out and move well away from the vehicle and traffic. Do not open the "
            "bonnet or try to restart the engine. If you are at a petrol station, alert staff "
            "immediately. Contact the emergency services from a safe place, then arrange "
            "professional recovery."
        )
    if _FUEL_LEAK.search(value):
        return (
            "Do not drive or restart the vehicle. Switch off the engine if it is safe to do so, "
            "keep people and ignition sources away, and arrange professional recovery. If there "
            "is fire or immediate danger, move away and contact the emergency services."
        )
    if _OVERHEATING.search(value):
        return (
            "Do not continue driving. Stop somewhere safe, switch off the engine, and keep clear "
            "of traffic. Do not open the bonnet if there is fire, and never open the coolant cap "
            "while the engine is hot. Let the vehicle cool and arrange roadside or professional "
            "recovery."
        )
    if _BRAKE_LOSS.search(value):
        return (
            "Do not drive the vehicle. A soft or ineffective brake pedal needs urgent professional "
            "inspection. Keep the vehicle somewhere safe and arrange roadside recovery rather "
            "than driving it to a dealership."
        )
    if _STEERING_LOSS.search(value):
        return (
            "Do not continue driving. Stop in a safe place if you can do so without putting anyone "
            "at risk, switch off the engine, and arrange roadside recovery and professional "
            "inspection."
        )
    return None
