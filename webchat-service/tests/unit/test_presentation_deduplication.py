import json

from webchat.domain.models import Message
from webchat.orchestration.contracts.semantics import TurnUnderstanding
from webchat.orchestration.orchestrator import (
    _read_only_presentation_fingerprint,
    _turn_requests_result_presentation,
    _visible_read_only_presentations,
)


def _message(
    sequence: int,
    *,
    role: str = "assistant",
    text: str = "result",
    view_type: str | None = None,
    view: dict | None = None,
) -> Message:
    return Message(
        id=f"message-{sequence}",
        conversation_id="conversation-1",
        turn_id=f"turn-{sequence}",
        sequence=sequence,
        role=role,
        text=text,
        view_type=view_type,
        view_payload_json=json.dumps(view) if view is not None else None,
        created_at="2026-09-04T00:00:00Z",
    )


def test_visible_read_only_presentations_cover_cards_and_collection_views() -> None:
    offer = {"items": [{"id": "offer-1", "title": "BMW finance offer"}]}
    dealership = {
        "items": [{"id": "dealer-1", "name": "Stockport"}],
        "collectionPresentationRendered": True,
    }
    messages = [
        _message(
            1,
            view_type="grounded_presentation",
            view={
                "version": 1,
                "cards": [{"type": "offer", "data": offer}],
                "quickReplies": [],
            },
        ),
        _message(2, view_type="dealership_list", view=dealership),
    ]

    visible = _visible_read_only_presentations(messages)

    assert _read_only_presentation_fingerprint("offer", offer) in visible
    assert (
        _read_only_presentation_fingerprint(
            "dealership_list",
            {"items": dealership["items"]},
        )
        in visible
    )
    assert (
        _read_only_presentation_fingerprint(
            "offer",
            {"items": [{"id": "offer-1", "title": "Updated offer"}]},
        )
        not in visible
    )


def test_confirmation_and_receipt_cards_are_never_deduplicated() -> None:
    assert _read_only_presentation_fingerprint("confirmation", {"draftId": "draft-1"}) is None
    assert _read_only_presentation_fingerprint("receipt", {"bookingId": "booking-1"}) is None


def test_typed_result_presentation_owns_repeat_detection_for_all_wording() -> None:
    explicit = TurnUnderstanding(
        dialogueAct="continue_goal",
        goalRelation="active",
        intentKinds=[],
        resultPresentation="explicit_request",
        ambiguity="none",
        confidence="high",
    )
    contextual = explicit.model_copy(update={"resultPresentation": "default"})

    assert _turn_requests_result_presentation(explicit) is True
    assert _turn_requests_result_presentation(contextual) is False


def test_missing_typed_understanding_never_parses_customer_language() -> None:
    assert _turn_requests_result_presentation(None) is False
