import json
from types import SimpleNamespace

import pytest

from webchat.domain.interactions import (
    PendingInteraction,
    choice_interaction,
    parse_interaction,
    preceding_interaction,
    selected_choice_action,
    single_action_interaction,
)


def test_single_action_and_choice_contracts_require_typed_application_actions() -> None:
    single = single_action_interaction(
        "Would you like to see workshop services?",
        {"type": "show_workshop_services"},
    )
    choice = choice_interaction(
        "Choose a contact method.",
        [
            {"label": "Callback", "action": {"type": "start_callback"}},
            {"label": "Message", "action": {"type": "start_dealership_message"}},
        ],
    )

    assert single.kind == "single_action"
    assert choice is not None and choice.kind == "choice"
    assert choice_interaction(
        "Unsafe mixed choices.",
        [
            {"label": "Callback", "action": {"type": "start_callback"}},
            {"label": "Generated text only"},
        ],
    ) is None
    with pytest.raises(ValueError, match="safe action type"):
        PendingInteraction(
            kind="single_action",
            prompt="Unsafe",
            actions=[{"type": "../../execute"}],
        )


def test_only_the_immediately_preceding_assistant_interaction_is_pending() -> None:
    interaction = single_action_interaction(
        "Would you like to see workshop services?",
        {"type": "show_workshop_services"},
    )
    messages = [
        SimpleNamespace(role="user", text="What services do you have?", interaction_json=None),
        SimpleNamespace(
            role="assistant",
            text=interaction.prompt,
            interaction_json=interaction.as_json(),
        ),
        SimpleNamespace(role="user", text="yes", interaction_json=None),
    ]

    pending = preceding_interaction(messages)

    assert pending is not None
    assert pending[0] == interaction
    assert parse_interaction("not-json") is None


def test_natural_workshop_service_choice_preserves_its_typed_dealership_action() -> None:
    suggestions = [
        {
            "label": "Full service",
            "text": "Book service: Full service",
            "action": {
                "type": "try_workshop_location",
                "serviceTypeId": "full-service",
                "dealershipId": "northstar-stockport",
            },
        },
        {
            "label": "Tyre fitting",
            "text": "Book service: Tyre fitting",
            "action": {
                "type": "try_workshop_location",
                "serviceTypeId": "tyre-fitting",
                "dealershipId": "northstar-stockport",
            },
        },
    ]
    interaction = choice_interaction("Choose a service.", suggestions)
    assert interaction is not None
    messages = [
        SimpleNamespace(
            role="assistant",
            text=interaction.prompt,
            interaction_json=interaction.as_json(),
            view_payload_json=json.dumps({"suggestions": suggestions}),
        ),
        SimpleNamespace(role="user", text="I want to book tyre service"),
    ]

    assert selected_choice_action(messages) == {
        "type": "try_workshop_location",
        "serviceTypeId": "tyre-fitting",
        "dealershipId": "northstar-stockport",
    }


def test_natural_choice_resolution_is_generic_across_typed_workflows() -> None:
    suggestions = [
        {
            "label": "Request a callback",
            "text": "please have a dealership call me",
            "action": {"type": "start_callback"},
        },
        {
            "label": "Send a message",
            "text": "send a message to a dealership",
            "action": {"type": "start_dealership_message"},
        },
        {
            "label": "Opening hours",
            "text": "show me dealership opening hours",
            "action": {"type": "show_opening_hours"},
        },
    ]
    interaction = choice_interaction("How can we help?", suggestions)
    assert interaction is not None
    messages = [
        SimpleNamespace(
            role="assistant",
            text=interaction.prompt,
            interaction_json=interaction.as_json(),
            view_payload_json=json.dumps({"suggestions": suggestions}),
        ),
        SimpleNamespace(role="user", text="Could somebody call me please?"),
    ]

    assert selected_choice_action(messages) == {"type": "start_callback"}


@pytest.mark.parametrize("customer_text", ["book a service", "service please"])
def test_generic_workshop_request_does_not_guess_a_service(customer_text: str) -> None:
    suggestions = [
        {
            "label": "Full service",
            "action": {
                "type": "select_workshop_service",
                "serviceTypeId": "full-service",
            },
        },
        {
            "label": "Tyre fitting",
            "action": {
                "type": "select_workshop_service",
                "serviceTypeId": "tyre-fitting",
            },
        },
    ]
    interaction = choice_interaction("Choose a service.", suggestions)
    assert interaction is not None
    messages = [
        SimpleNamespace(
            role="assistant",
            text=interaction.prompt,
            interaction_json=interaction.as_json(),
            view_payload_json=json.dumps({"suggestions": suggestions}),
        ),
        SimpleNamespace(role="user", text=customer_text),
    ]

    assert selected_choice_action(messages) is None


def test_natural_choice_does_not_execute_a_negated_option() -> None:
    suggestions = [
        {
            "label": "Request a callback",
            "action": {"type": "start_callback"},
        },
        {
            "label": "Send a message",
            "action": {"type": "start_dealership_message"},
        },
    ]
    interaction = choice_interaction("How can we help?", suggestions)
    assert interaction is not None
    messages = [
        SimpleNamespace(
            role="assistant",
            text=interaction.prompt,
            interaction_json=interaction.as_json(),
            view_payload_json=json.dumps({"suggestions": suggestions}),
        ),
        SimpleNamespace(role="user", text="I don't want a callback"),
    ]

    assert selected_choice_action(messages) is None
