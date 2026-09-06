from webchat.orchestration.orchestrator import _application_fallback_text
from webchat.orchestration.tools.result import ToolResult


def test_fallback_preserves_the_trusted_tool_summary_without_interpreting_customer_words() -> None:
    result = ToolResult("Found 3 matching vehicles.", "vehicle_list", {"items": []}, {})

    text = _application_fallback_text([result], {})

    assert text == "Found 3 matching vehicles."


def test_test_drive_slot_fallback_keeps_the_customer_on_the_live_choice() -> None:
    result = ToolResult(
        "Found matching test-drive appointments.",
        "test_drive_slot_picker",
        {"items": [{"id": "td-slot-0001"}]},
        {},
    )

    text = _application_fallback_text(
        [result],
        {
            "activeWorkflow": "test_drive",
            "stage": "choosing_time",
            "constraints": {"missingPublicFields": ["slotId"]},
        },
    )

    assert text == "Here are the available appointment times. Which one would you like?"


def test_broad_empty_test_drive_fallback_does_not_ask_for_another_date() -> None:
    result = ToolResult(
        "Found 0 results.",
        "test_drive_slot_picker",
        {"items": [], "suggestions": [{"label": "Find another car"}]},
        {},
    )

    text = _application_fallback_text(
        [result],
        {
            "activeWorkflow": "test_drive",
            "stage": "no_availability",
            "constraints": {
                "missingPublicFields": [],
                "resolution": {
                    "kind": "test_drive_slots",
                    "status": "empty",
                    "scope": "broad",
                    "continuation": "offer_vehicle_alternatives",
                },
            },
        },
    )

    assert "No online test-drive times" in text
    assert "another date" not in text
    assert "approximate time" not in text


def test_workshop_location_recovery_fallback_asks_for_location_not_time() -> None:
    result = ToolResult(
        "No appointments are currently available.",
        "slot_list",
        {"items": [], "suggestions": [{"label": "Try Stockport"}]},
        {},
    )

    text = _application_fallback_text(
        [result],
        {
            "activeWorkflow": "workshop_booking",
            "stage": "choosing_dealership",
            "constraints": {
                "missingPublicFields": ["dealershipId"],
                "resolution": {
                    "kind": "workshop_slots",
                    "status": "empty",
                    "scope": "broad",
                    "continuation": "choose_alternative_location",
                },
            },
        },
    )

    assert text.endswith("Which available alternative location would you prefer?")
    assert "workflow" not in text
    assert "recovery" not in text


def test_viewless_workflow_transition_cannot_fall_through_to_global_continuation() -> None:
    result = ToolResult(
        "The selected service and workshop are ready for appointment scheduling.",
        None,
        None,
        {
            "resolution": {
                "kind": "workshop_slots",
                "status": "ready",
                "scope": "broad",
                "continuation": "request_schedule_preferences",
            }
        },
    )

    text = _application_fallback_text(
        [result],
        {
            "activeWorkflow": "workshop_booking",
            "stage": "choosing_schedule_preferences",
            "constraints": {
                "missingPublicFields": ["preferredDayOrDate", "approximateTime"],
                "resolution": result.facts["resolution"],
            },
        },
    )

    assert text == (
        "The workshop service and location are selected. "
        "What day or date would suit you, and approximately what time?"
    )
    assert "ready for appointment scheduling" not in text
    assert "What else can I help" not in text


def test_test_drive_location_fallback_names_the_live_alternative_context() -> None:
    result = ToolResult(
        "No test-drive times are available at the requested dealership.",
        "test_drive_slot_picker",
        {
            "items": [
                {
                    "id": "td-slot-0042",
                    "dealershipName": "Northstar Stockport",
                }
            ]
        },
        {},
    )

    text = _application_fallback_text(
        [result],
        {
            "activeWorkflow": "test_drive",
            "stage": "choosing_slot",
            "constraints": {
                "missingPublicFields": ["slotId"],
                "resolution": {
                    "kind": "test_drive_slots",
                    "status": "empty",
                    "scope": "broad",
                    "continuation": "offer_location_alternatives",
                },
            },
        },
    )

    assert "Northstar Stockport" in text
    assert text.endswith("Would you like that appointment?")
    assert "day or date" not in text
