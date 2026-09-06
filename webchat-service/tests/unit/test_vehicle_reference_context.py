import json

from webchat.domain.conversation_state import ConversationState
from webchat.domain.models import Message
from webchat.orchestration.appointments import (
    appointment_collection_payload,
    customer_choice_context,
)
from webchat.orchestration.context import (
    ConversationHistoryBuilder,
    _planning_customer_text,
    _recent_customer_messages,
    _reconcile_pending_question,
)
from webchat.orchestration.references import (
    current_choice_reference_context,
    current_dealership_reference_context,
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


def test_history_exposes_candidates_without_inventing_focus_from_assistant_prose() -> None:
    comparison = _message(
        1,
        "vehicle_comparison",
        {
            "items": [
                {"id": "veh-001", "make": "BMW", "model": "1 Series"},
                {"id": "veh-002", "make": "BMW", "model": "X3"},
                {"id": "veh-003", "make": "MINI", "model": "Countryman"},
            ]
        },
    )
    messages = [
        comparison,
        _conversation_message(2, "user", "Lower monthly cost matters most to me."),
        _conversation_message(
            3,
            "assistant",
            "The strongest value here is the BMW 1 Series because it has the lowest monthly cost.",
        ),
        _conversation_message(4, "user", "Show me the vehicle"),
    ]

    context = ConversationHistoryBuilder(None).build("conversation-1", messages)

    reference_messages = [
        message["content"]
        for message in context.history
        if str(message.get("content") or "").startswith("Typed entity reference context")
    ]
    assert len(reference_messages) == 1
    assert '"displayedVehicles"' in reference_messages[0]
    assert "focusedVehicle" not in reference_messages[0]


def test_planning_prefill_context_is_customer_authored_bounded_and_excludes_latest() -> None:
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


def test_durable_dialogue_question_outranks_legacy_message_choice_metadata() -> None:
    workflow = {
        "version": 3,
        "activeWorkflow": "workshop_booking",
        "stage": "choosing_service",
        "entities": {},
        "constraints": {
            "missingPublicFields": ["serviceTypeId"],
            "acceptedInputFields": [],
        },
    }
    state = ConversationState.model_validate(
        {
            "stateVersion": 1,
            "agentWorkflow": workflow,
            "dialogue": {
                "activeQuestion": {
                    "questionId": "question-service",
                    "kind": "reference_choice",
                    "prompt": "Which service would you like to book?",
                    "goalIntent": "workshop_booking",
                    "candidateReferences": [
                        "service:brake-inspection",
                        "service:diagnostic",
                    ],
                    "candidateIntents": [],
                    "expectedFields": ["serviceTypeId"],
                    "originatingMessageId": "message-1",
                    "createdAtStateVersion": 1,
                }
            },
        }
    )

    class Store:
        @staticmethod
        def get_contexts(conversation_id):
            del conversation_id
            return {"initial": {}, "current": {}}

        @staticmethod
        def get_workflow_state(conversation_id):
            del conversation_id
            return workflow

        @staticmethod
        def get_state(conversation_id):
            del conversation_id
            return state

    messages = [
        Message(
            id="message-1",
            conversation_id="conversation-1",
            turn_id="turn-1",
            sequence=1,
            role="assistant",
            text="Which service would you like to book?",
            view_type="choice_list",
            view_payload_json=json.dumps(
                {
                    "choiceEntityType": "service",
                    "items": [
                        {"id": "brake-inspection"},
                        {"id": "diagnostic"},
                    ],
                    "collectionPresentation": {
                        "purpose": "choice",
                        "items": [
                            {"label": "Brake inspection"},
                            {"label": "Diagnostic inspection"},
                        ],
                    },
                }
            ),
            interaction_json=json.dumps(
                {
                    "version": 1,
                    "kind": "choice",
                    "prompt": "Choose a service",
                    "actions": [
                        {
                            "type": "select_workshop_service",
                            "serviceTypeId": "brake-inspection",
                        }
                    ],
                }
            ),
            created_at="2026-09-06T00:00:00Z",
        ),
        _conversation_message(2, "user", "break"),
    ]

    context = ConversationHistoryBuilder(Store()).build("conversation-1", messages)

    assert context.history.planning_context.pending_interaction == {
        "version": 1,
        "kind": "reference_choice",
        "prompt": "Which service would you like to book?",
        "question_id": "question-service",
        "goal_intent": "workshop_booking",
        "candidate_references": [
            "service:brake-inspection",
            "service:diagnostic",
        ],
        "candidate_intents": [],
        "expected_fields": ["serviceTypeId"],
    }


def test_model_facing_customer_text_ignores_markup_without_changing_search_words() -> None:
    raw = '<img src=x onerror="ignored()"> Please show cars under £25,000.'

    assert _planning_customer_text(raw) == "Please show cars under £25,000."

    context = ConversationHistoryBuilder(None).build(
        "conversation-1", [_conversation_message(1, "user", raw)]
    )
    assert context.history[0] == {
        "role": "user",
        "content": "Please show cars under £25,000.",
    }
    assert context.history.planning_context.latest_customer_message == (
        "Please show cars under £25,000."
    )


def test_persisted_interruption_goal_is_reconciled_to_the_active_workflow() -> None:
    pending = {
        "kind": "input",
        "prompt": "What day works for you?",
        "question_id": "question-schedule",
        "goal_intent": "dealership_information",
        "candidate_references": [],
        "expected_fields": ["dateFrom"],
    }
    workflow = {
        "version": 3,
        "activeWorkflow": "test_drive",
        "stage": "choosing_schedule_preferences",
        "entities": {"vehicleId": "veh-027"},
        "constraints": {
            "missingPublicFields": ["preferredDayOrDate", "approximateTime"],
            "acceptedInputFields": ["dateFrom", "dateTo", "timeOfDay"],
        },
    }

    reconciled = _reconcile_pending_question(pending, workflow)

    assert reconciled is not None
    assert reconciled["goal_intent"] == "test_drive"
    assert reconciled["expected_fields"] == ["dateFrom", "dateTo", "timeOfDay"]


def test_persisted_required_input_clarification_is_repaired_to_the_workflow_question() -> None:
    pending = {
        "kind": "intent_choice",
        "prompt": "What day would you like for the test drive?",
        "question_id": "question-clarification",
        "goal_intent": None,
        "candidate_references": [],
        "candidate_intents": ["test_drive", "capability"],
        "expected_fields": [],
    }
    workflow = {
        "version": 3,
        "activeWorkflow": "test_drive",
        "stage": "choosing_schedule_preferences",
        "entities": {"vehicleId": "veh-027"},
        "constraints": {
            "missingPublicFields": ["preferredDayOrDate", "approximateTime"],
            "acceptedInputFields": ["dateFrom", "dateTo", "timeOfDay"],
        },
    }

    reconciled = _reconcile_pending_question(pending, workflow)

    assert reconciled is not None
    assert reconciled["kind"] == "input"
    assert reconciled["goal_intent"] == "test_drive"
    assert reconciled["candidate_intents"] == []
    assert reconciled["expected_fields"] == ["dateFrom", "dateTo", "timeOfDay"]


def test_generic_dealership_choices_remain_trusted_reference_context() -> None:
    message = _message(
        1,
        "choice_list",
        {
            "choiceEntityType": "dealership",
            "items": [
                {"id": "northstar-bolton", "name": "Northstar Bolton", "town": "Bolton"},
                {
                    "id": "northstar-stockport",
                    "name": "Northstar Stockport",
                    "town": "Stockport",
                },
            ],
            "collectionPresentation": {
                "schemaVersion": 1,
                "layout": "chip_grid",
                "purpose": "choice",
                "items": [
                    {"label": "Northstar Bolton", "description": "Bolton"},
                    {"label": "Northstar Stockport", "description": "Stockport"},
                ],
            },
        },
    )

    assert [item["entityReference"] for item in current_choice_reference_context([message])] == [
        "dealership:northstar-bolton",
        "dealership:northstar-stockport",
    ]
    assert [item["dealershipId"] for item in current_dealership_reference_context([message])] == [
        "northstar-bolton",
        "northstar-stockport",
    ]


def test_workflow_enum_choices_expose_the_owning_input_field() -> None:
    message = _message(
        1,
        "choice_list",
        {
            "choiceEntityType": "workflow_option",
            "choiceField": "department",
            "items": [
                {"id": "sales", "name": "Sales"},
                {"id": "service", "name": "Service"},
                {"id": "parts", "name": "Parts"},
            ],
            "collectionPresentation": {
                "schemaVersion": 1,
                "layout": "chip_grid",
                "purpose": "choice",
                "items": [
                    {"label": "Sales", "description": None},
                    {"label": "Service", "description": None},
                    {"label": "Parts", "description": None},
                ],
            },
        },
    )

    assert current_choice_reference_context([message]) == [
        {
            "position": 1,
            "entityReference": "workflow_option:sales",
            "label": "Sales",
            "description": None,
            "inputField": "department",
            "value": "sales",
        },
        {
            "position": 2,
            "entityReference": "workflow_option:service",
            "label": "Service",
            "description": None,
            "inputField": "department",
            "value": "service",
        },
        {
            "position": 3,
            "entityReference": "workflow_option:parts",
            "label": "Parts",
            "description": None,
            "inputField": "department",
            "value": "parts",
        },
    ]
    assert current_dealership_reference_context([message]) == []


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


def test_reference_context_retains_bounded_prior_pages_from_the_same_search() -> None:
    filters = {"maxPricePence": 3_000_000, "sort": "priceAsc"}
    messages = [
        _message(
            1,
            "vehicle_list",
            {
                "items": [{"id": "veh-001", "model": "1 Series"}],
                "search": {"filters": filters, "page": 1},
            },
        ),
        _message(
            2,
            "vehicle_list",
            {
                "items": [{"id": "veh-026", "model": "3 Series"}],
                "search": {"filters": filters, "page": 2},
            },
        ),
    ]

    references = current_vehicle_reference_context(messages, retain_prior_pages=True)

    assert [(item["resultPage"], item["position"], item["vehicleId"]) for item in references] == [
        (1, 1, "veh-001"),
        (2, 1, "veh-026"),
    ]

    current = current_vehicle_reference_context(messages)
    assert [(item["resultPage"], item["position"], item["vehicleId"]) for item in current] == [
        (2, 1, "veh-026"),
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


def test_completed_receipt_replaces_an_older_vehicle_list_as_the_current_subject() -> None:
    messages = [
        _message(
            1,
            "vehicle_list",
            {
                "items": [
                    {"id": "veh-041", "make": "MINI", "model": "Cooper"},
                    {"id": "veh-005", "make": "MINI", "model": "Cooper"},
                    {"id": "veh-053", "make": "MINI", "model": "Cooper"},
                ]
            },
        ),
        _message(
            2,
            "receipt",
            {
                "kind": "test_drive",
                "vehicleId": "veh-005",
                "vehicleLabel": "MINI Cooper",
                "status": "confirmed",
            },
        ),
        _message(3, None, None),
    ]

    assert current_vehicle_reference_context(messages) == [
        {
            "position": 1,
            "vehicleId": "veh-005",
            "label": "MINI Cooper",
            "make": "MINI",
            "model": "Cooper",
        }
    ]


def test_comparison_and_follow_up_preserve_the_current_vehicle_context() -> None:
    messages = [
        _message(
            1,
            "grounded_presentation",
            {
                "cards": [
                    {
                        "type": "vehicle_comparison",
                        "data": {
                            "items": [
                                {"id": "veh-025", "model": "3 Series"},
                                {"id": "veh-049", "model": "Countryman"},
                            ]
                        },
                    }
                ],
                "quickReplies": [],
            },
        ),
        _message(
            2,
            "grounded_presentation",
            {
                "cards": [],
                "quickReplies": [{"label": "Extra space", "message": "Extra space matters most."}],
            },
        ),
    ]

    assert [item["vehicleId"] for item in current_vehicle_reference_context(messages)] == [
        "veh-025",
        "veh-049",
    ]


def test_cardless_follow_up_preserves_the_current_vehicle_search_state() -> None:
    messages = [
        _message(
            1,
            "grounded_presentation",
            {
                "cards": [
                    {
                        "type": "vehicle_preview",
                        "data": {
                            "items": [{"id": "veh-025", "model": "3 Series"}],
                            "search": {"filters": {"fuelType": "Hybrid"}, "page": 2},
                        },
                    }
                ],
                "quickReplies": [],
            },
        ),
        _message(
            2,
            "grounded_presentation",
            {"cards": [], "quickReplies": [{"label": "Show more", "message": "Show more"}]},
        ),
    ]

    assert current_vehicle_search_state(messages) == {
        "filters": {"fuelType": "Hybrid"},
        "page": 2,
    }


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


def test_cardless_follow_up_preserves_the_current_offer_context() -> None:
    messages = [
        _message(
            1,
            "grounded_presentation",
            {
                "cards": [
                    {
                        "type": "offer",
                        "data": {
                            "items": [
                                {
                                    "id": "offer-07",
                                    "title": "Jaguar F-PACE offer",
                                    "make": "Jaguar",
                                    "model": "F-PACE",
                                }
                            ]
                        },
                    }
                ],
                "quickReplies": [],
            },
        ),
        _message(2, "grounded_presentation", {"cards": [], "quickReplies": []}),
    ]

    assert [item["offerId"] for item in current_offer_reference_context(messages)] == ["offer-07"]


def test_cardless_follow_up_preserves_the_current_dealership_context() -> None:
    messages = [
        _message(
            1,
            "grounded_presentation",
            {
                "cards": [
                    {
                        "type": "dealership",
                        "data": {
                            "items": [
                                {
                                    "id": "northstar-liverpool",
                                    "name": "Northstar Liverpool",
                                    "town": "Liverpool",
                                }
                            ]
                        },
                    }
                ],
                "quickReplies": [],
            },
        ),
        _message(2, "grounded_presentation", {"cards": [], "quickReplies": []}),
    ]

    assert current_dealership_reference_context(messages) == [
        {
            "position": 1,
            "dealershipId": "northstar-liverpool",
            "name": "Northstar Liverpool",
            "town": "Liverpool",
            "postcode": None,
        }
    ]


def test_choice_context_exposes_only_trusted_ids_from_the_latest_collection() -> None:
    messages = [
        _message(
            1,
            "service_list",
            {
                "items": [
                    {"id": "brake-inspection", "name": "Brake inspection"},
                    {"id": "mot", "name": "MOT"},
                ],
                "collectionPresentation": {
                    "schemaVersion": 1,
                    "layout": "chip_grid",
                    "purpose": "choice",
                    "items": [
                        {
                            "label": "Brake inspection",
                            "description": "Brake condition and performance inspection.",
                        },
                        {"label": "MOT", "description": "Annual MOT inspection."},
                    ],
                },
            },
        )
    ]

    assert current_choice_reference_context(messages) == [
        {
            "position": 1,
            "entityReference": "service:brake-inspection",
            "label": "Brake inspection",
            "description": "Brake condition and performance inspection.",
        },
        {
            "position": 2,
            "entityReference": "service:mot",
            "label": "MOT",
            "description": "Annual MOT inspection.",
        },
    ]


def test_appointment_choice_context_uses_the_same_uk_local_time_the_customer_sees() -> None:
    message = _message(
        1,
        "trusted_slot_context",
        {
            "items": [
                {
                    "id": "td-slot-0241",
                    "startsAt": "2026-09-08T09:00:00Z",
                    "dealershipName": "Northstar Manchester",
                    "vehicleId": "veh-001",
                },
                {
                    "id": "td-slot-0401",
                    "startsAt": "2026-12-08T10:00:00Z",
                    "dealershipName": "Northstar Manchester",
                    "vehicleId": "veh-001",
                },
            ]
        },
    )

    choices = current_choice_reference_context([message])

    assert choices[0]["entityReference"] == "appointment:td-slot-0241"
    assert choices[0]["startsAt"] == "2026-09-08T09:00:00Z"
    assert choices[0]["localDate"] == "2026-09-08"
    assert choices[0]["localTime"] == "10:00"
    assert choices[0]["displayLabel"] == "Tue, 8 Sept 2026, 10:00"
    assert choices[0]["label"] == "Tue, 8 Sept 2026, 10:00"
    model_choices = customer_choice_context(choices)
    assert model_choices[0]["label"] == "Tue, 8 Sept 2026, 10:00"
    assert model_choices[0]["localTime"] == "10:00"
    assert "startsAt" not in model_choices[0]
    assert "localStartsAt" not in model_choices[0]
    assert choices[1]["localTime"] == "10:00"
    assert choices[1]["displayLabel"] == "Tue, 8 Dec 2026, 10:00"


def test_generic_appointment_choice_retains_namespace_and_operational_metadata() -> None:
    payload = appointment_collection_payload(
        {
            "version": 1,
            "items": [
                {
                    "id": "td-slot-0280",
                    "startsAt": "2026-09-10T16:00:00Z",
                    "dealershipId": "northstar-stockport",
                    "dealershipName": "Northstar Stockport",
                    "vehicleId": "veh-042",
                    "make": "MINI",
                    "model": "Countryman",
                }
            ],
        }
    )

    choices = current_choice_reference_context([_message(1, "choice_list", payload)])

    assert payload["choiceEntityType"] == "appointment"
    assert payload["choiceField"] == "slotId"
    assert payload["collectionPresentation"]["layout"] == "bullet_list"
    assert payload["choiceReplies"] == [
        {
            "label": "Choose this time",
            "text": "Thu, 10 Sept 2026, 17:00",
        }
    ]
    assert choices == [
        {
            "position": 1,
            "entityReference": "appointment:td-slot-0280",
            "label": "Thu, 10 Sept 2026, 17:00",
            "description": "Northstar Stockport · MINI Countryman",
            "startsAt": "2026-09-10T16:00:00Z",
            "timeZone": "Europe/London",
            "localStartsAt": "2026-09-10T17:00+01:00",
            "localDate": "2026-09-10",
            "localTime": "17:00",
            "displayLabel": "Thu, 10 Sept 2026, 17:00",
            "dealershipId": "northstar-stockport",
            "dealershipName": "Northstar Stockport",
            "vehicleId": "veh-042",
            "make": "MINI",
            "model": "Countryman",
        }
    ]


def test_choice_context_can_be_scoped_to_the_field_owned_by_an_interrupted_workflow() -> None:
    service = _message(
        1,
        "choice_list",
        {
            "selectionOnly": True,
            "choiceEntityType": "service",
            "choiceField": "serviceTypeId",
            "items": [{"id": "brake-inspection", "name": "Brake inspection"}],
            "collectionPresentation": {
                "schemaVersion": 1,
                "layout": "chip_grid",
                "purpose": "choice",
                "items": [{"label": "Brake inspection", "description": None}],
            },
        },
    )
    later_department = _message(
        2,
        "choice_list",
        {
            "selectionOnly": True,
            "choiceEntityType": "workflow_option",
            "choiceField": "department",
            "items": [{"id": "parts", "name": "Parts"}],
            "collectionPresentation": {
                "schemaVersion": 1,
                "layout": "chip_grid",
                "purpose": "choice",
                "items": [{"label": "Parts", "description": None}],
            },
        },
    )

    choices = current_choice_reference_context(
        [service, later_department],
        choice_field="serviceTypeId",
    )

    assert [item["entityReference"] for item in choices] == ["service:brake-inspection"]


def test_choice_context_preserves_the_option_numbers_rendered_by_the_producer() -> None:
    choice = _message(
        1,
        "choice_list",
        {
            "selectionOnly": True,
            "choiceEntityType": "vehicle",
            "items": [
                {"id": "veh-041", "position": 1},
                {"id": "veh-053", "position": 3},
            ],
            "collectionPresentation": {
                "schemaVersion": 1,
                "layout": "bullet_list",
                "purpose": "clarification",
                "items": [
                    {"label": "Option 1 — MINI Cooper"},
                    {"label": "Option 3 — MINI Cooper"},
                ],
            },
        },
    )

    assert [
        (item["position"], item["entityReference"])
        for item in current_choice_reference_context([choice])
    ] == [(1, "vehicle:veh-041"), (3, "vehicle:veh-053")]


def test_latest_five_vehicle_cards_replace_an_older_choice_as_the_ordinal_scope() -> None:
    older_choice = _message(
        1,
        "choice_list",
        {
            "selectionOnly": True,
            "choiceEntityType": "vehicle",
            "items": [{"id": "veh-042", "position": 1}],
            "collectionPresentation": {
                "schemaVersion": 1,
                "layout": "bullet_list",
                "purpose": "clarification",
                "items": [{"label": "Option 1 — MINI Countryman"}],
            },
        },
    )
    current_cards = _message(
        2,
        "grounded_presentation",
        {
            "version": 1,
            "cards": [
                {
                    "type": "vehicle_preview",
                    "data": {
                        "items": [
                            {
                                "id": f"veh-0{index:02}",
                                "optionNumber": index,
                                "year": 2026 - index,
                                "make": "Volvo",
                                "model": "XC40",
                            }
                            for index in range(1, 6)
                        ]
                    },
                }
            ],
        },
    )

    choices = current_choice_reference_context([older_choice, current_cards])

    assert [item["position"] for item in choices] == [1, 2, 3, 4, 5]
    assert [item["entityReference"] for item in choices] == [
        "vehicle:veh-001",
        "vehicle:veh-002",
        "vehicle:veh-003",
        "vehicle:veh-004",
        "vehicle:veh-005",
    ]
    assert all(item["make"] == "Volvo" for item in choices)
