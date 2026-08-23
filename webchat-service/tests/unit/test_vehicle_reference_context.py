import json

from webchat.domain.models import Message
from webchat.orchestration.context import _recent_customer_messages
from webchat.orchestration.references import (
    current_offer_reference_context,
    current_vehicle_reference_context,
    current_vehicle_search_state,
    page_vehicle_identities,
    page_vehicle_reference_context,
)


def _message(sequence: int, view_type: str | None, payload: dict | None) -> Message:
    return Message(
        id=f"message-{sequence}",
        conversation_id="conversation-1",
        turn_id=f"turn-{sequence}",
        sequence=sequence,
        role="assistant",
        text="result",
        view_type=view_type,
        view_payload_json=json.dumps(payload) if payload else None,
        created_at="2026-08-22T00:00:00Z",
    )


def _conversation_message(sequence: int, role: str, text: str) -> Message:
    return Message(
        id=f"message-{sequence}",
        conversation_id="conversation-1",
        turn_id=f"turn-{sequence}",
        sequence=sequence,
        role=role,
        text=text,
        view_type=None,
        view_payload_json=None,
        created_at="2026-08-22T00:00:00Z",
    )


def test_reviewer_prefill_context_is_customer_authored_bounded_and_excludes_latest() -> None:
    messages = [
        _conversation_message(1, "user", "Please ask whether my service plan covers tyres."),
        _conversation_message(2, "assistant", "I can help contact a dealership."),
        _conversation_message(3, "user", "Where is the Stockport dealership?"),
        _conversation_message(4, "assistant", "Stockport details are shown."),
        _conversation_message(5, "user", "Send a message to the dealership."),
    ]

    assert _recent_customer_messages(messages) == [
        "Please ask whether my service plan covers tyres.",
        "Where is the Stockport dealership?",
    ]


def test_reference_context_exposes_only_the_current_ordered_vehicle_results() -> None:
    messages = [
        _message(1, "vehicle_list", {"items": [{"id": "veh-001", "model": "Old result"}]}),
        _message(
            2,
            "vehicle_list",
            {
                "items": [
                    {
                        "id": "veh-025",
                        "year": 2026,
                        "model": "Range Rover Evoque",
                        "mileage": 47_250,
                    },
                    {"id": "veh-049", "year": 2024, "model": "Sportage", "mileage": 22_250},
                ]
            },
        ),
    ]

    assert current_vehicle_reference_context(messages) == [
        {
            "position": 1,
            "vehicleId": "veh-025",
            "year": 2026,
            "make": None,
            "model": "Range Rover Evoque",
            "variant": None,
            "bodyStyle": None,
            "pricePence": None,
            "mileage": 47_250,
            "fuelType": None,
            "transmission": None,
            "colour": None,
            "availability": None,
            "dealershipTown": None,
        },
        {
            "position": 2,
            "vehicleId": "veh-049",
            "year": 2024,
            "make": None,
            "model": "Sportage",
            "variant": None,
            "bodyStyle": None,
            "pricePence": None,
            "mileage": 22_250,
            "fuelType": None,
            "transmission": None,
            "colour": None,
            "availability": None,
            "dealershipTown": None,
        },
    ]


def test_page_vehicle_projection_keeps_status_for_reference_resolution() -> None:
    vehicles = page_vehicle_reference_context(
        {
            "entities": [
                {
                    "type": "vehicle",
                    "id": "veh-013",
                    "label": "BMW 3 Series",
                    "attributes": {
                        "year": 2025,
                        "make": "BMW",
                        "model": "3 Series",
                        "variant": "320d M Sport",
                        "availability": "sold",
                        "dealershipTown": "Manchester",
                        "pricePence": 2_125_000,
                    },
                },
                {
                    "type": "vehicle",
                    "id": "veh-014",
                    "label": "BMW 3 Series",
                    "attributes": {
                        "year": 2026,
                        "make": "BMW",
                        "model": "3 Series",
                        "variant": "320d M Sport",
                        "availability": "available",
                        "dealershipTown": "Stockport",
                        "pricePence": 2_125_000,
                    },
                },
            ]
        }
    )

    projected = page_vehicle_identities(vehicles)

    assert projected[0] == {
        "position": 1,
        "vehicleId": "veh-013",
        "label": "BMW 3 Series",
        "year": 2025,
        "make": "BMW",
        "model": "3 Series",
        "variant": "320d M Sport",
        "pricePence": 2_125_000,
        "availability": "sold",
        "dealershipTown": "Manchester",
    }
    assert projected[1]["vehicleId"] == "veh-014"
    assert projected[1]["availability"] == "available"


def test_vehicle_detail_does_not_replace_the_current_ordered_results() -> None:
    messages = [
        _message(
            1,
            "vehicle_list",
            {
                "items": [
                    {"id": "veh-025", "model": "3 Series"},
                    {"id": "veh-049", "model": "XC40"},
                ]
            },
        ),
        _message(
            2,
            "vehicle_details",
            {"vehicle": {"id": "veh-025", "model": "3 Series"}},
        ),
    ]

    assert [item["vehicleId"] for item in current_vehicle_reference_context(messages)] == [
        "veh-025",
        "veh-049",
    ]


def test_search_state_comes_from_the_latest_vehicle_view() -> None:
    messages = [
        _message(
            1,
            "vehicle_list",
            {"search": {"filters": {"fuelType": "Hybrid"}, "page": 2}},
        )
    ]

    assert current_vehicle_search_state(messages) == {
        "filters": {"fuelType": "Hybrid"},
        "page": 2,
    }


def test_offer_reference_context_uses_the_latest_server_authored_offer_cards() -> None:
    messages = [
        _message(
            1,
            "offer_list",
            {
                "items": [
                    {
                        "id": "offer-07",
                        "title": "Jaguar F-PACE offer",
                        "make": "Jaguar",
                        "model": "F-PACE",
                        "productType": "PCP",
                        "monthlyPricePence": 57_400,
                        "expiresOn": "2026-11-18",
                    }
                ]
            },
        )
    ]

    assert current_offer_reference_context(messages) == [
        {
            "position": 1,
            "offerId": "offer-07",
            "title": "Jaguar F-PACE offer",
            "make": "Jaguar",
            "model": "F-PACE",
            "productType": "PCP",
            "monthlyPricePence": 57_400,
            "expiresOn": "2026-11-18",
        }
    ]
