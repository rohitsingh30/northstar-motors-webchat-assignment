import pytest

from webchat.domain.vehicle_safety import immediate_vehicle_safety_guidance


@pytest.mark.parametrize(
    ("customer_text", "required"),
    [
        (
            "My brake warning light is on and the pedal feels soft. Can I drive to Stockport?",
            ("Do not drive", "recovery"),
        ),
        (
            "The temperature gauge is in the red and steam is coming from the bonnet.",
            ("Do not continue driving", "switch off", "coolant cap"),
        ),
        (
            "There is smoke and a flame in the engine bay at a petrol station.",
            ("Get everyone out", "alert staff", "emergency services"),
        ),
        (
            "There is a strong petrol smell and I think fuel is leaking.",
            ("Do not drive", "ignition sources", "recovery"),
        ),
        (
            "I have lost the steering while driving.",
            ("Do not continue driving", "recovery"),
        ),
    ],
)
def test_unambiguous_vehicle_hazards_receive_immediate_actions(
    customer_text: str, required: tuple[str, ...]
) -> None:
    guidance = immediate_vehicle_safety_guidance(customer_text)

    assert guidance is not None
    assert all(value.casefold() in guidance.casefold() for value in required)


@pytest.mark.parametrize(
    "customer_text",
    [
        "What does the amber service warning light mean?",
        "I would like to book a brake inspection.",
        "Show me cars with heated steering wheels.",
    ],
)
def test_benign_vehicle_questions_are_not_intercepted(customer_text: str) -> None:
    assert immediate_vehicle_safety_guidance(customer_text) is None
