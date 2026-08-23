from types import SimpleNamespace

import pytest

from webchat.domain.interactions import (
    PendingInteraction,
    choice_interaction,
    parse_interaction,
    preceding_interaction,
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
