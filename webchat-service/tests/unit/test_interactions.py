from types import SimpleNamespace

import pytest

from webchat.domain.interactions import (
    ActionHandoff,
    PendingInteraction,
    choice_interaction,
    interaction_from_view,
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


def test_action_handoff_context_survives_a_server_owned_choice_chain() -> None:
    handoff = ActionHandoff(
        sourceWorkflow="part_exchange",
        topic="part_exchange",
        customerReason="Will you pick up my car?",
        transition="handoff",
    )
    interaction = choice_interaction(
        "Choose a contact method.",
        [
            {"label": "Callback", "action": {"type": "start_callback"}},
            {"label": "Message", "action": {"type": "start_dealership_message"}},
        ],
        handoff=handoff,
    )

    assert interaction is not None
    restored = parse_interaction(interaction.as_json())
    assert restored is not None
    assert restored.handoff == handoff


def test_confirmation_interaction_carries_the_protected_draft_identity() -> None:
    interaction = interaction_from_view(
        "Review before confirming.",
        "confirmation",
        {"draftId": "draft-1", "kind": "callback"},
    )
    assert interaction is not None
    assert interaction.kind == "protected_confirmation"
    assert interaction.draft_id == "draft-1"
    assert interaction.workflow_kind == "callback"
