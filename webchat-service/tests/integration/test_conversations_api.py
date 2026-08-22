from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from webchat.config import Settings
from webchat.integrations.contracts import ProviderReply, ToolCall, TurnPlan
from webchat.main import create_app
from webchat.orchestration.tool_registry import ToolResult

CONTEXT = {"path": "/", "section": "vehicles", "vehicleId": "veh-001", "title": "Used Cars"}


def client(tmp_path: Path) -> TestClient:
    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    return TestClient(create_app(settings))


def test_restore_hides_only_forms_completed_before_a_receipt(tmp_path: Path) -> None:
    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        messages = browser.app.state.messages
        messages.add(
            conversation_id,
            "assistant",
            "Complete the first form.",
            None,
            "draft",
            '{"kind":"vehicle_interest","draftId":"draft-old"}',
        )
        messages.add(
            conversation_id,
            "assistant",
            "Interest registered.",
            None,
            "receipt",
            '{"kind":"vehicle_interest","reference":"WAIT-OLD","status":"registered"}',
        )
        messages.add(conversation_id, "user", "Register my interest again.", None)
        messages.add(
            conversation_id,
            "assistant",
            "Complete the new form.",
            None,
            "draft",
            '{"kind":"vehicle_interest","draftId":"draft-new"}',
        )

        restored = browser.get(f"/api/chat/v1/conversations/{conversation_id}").json()

    assert [message["text"] for message in restored["messages"]] == [
        "Interest registered.",
        "Register my interest again.",
        "Complete the new form.",
    ]


def test_restore_reconstructs_legacy_receipt_and_keeps_new_repeat_form(tmp_path: Path) -> None:
    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        workflows = browser.app.state.workflow_repository
        old = workflows.create_or_replace(
            conversation_id, "vehicle_interest", {"vehicleId": "veh-028"}, "old", "collecting"
        )
        browser.app.state.messages.add(
            conversation_id,
            "assistant",
            "Complete the old form.",
            None,
            "draft",
            f'{{"kind":"vehicle_interest","draftId":"{old["id"]}"}}',
        )
        confirmed = workflows.create_or_replace(
            conversation_id,
            "vehicle_interest",
            {"vehicleId": "veh-028", "firstName": "Rohit"},
            "confirmed",
            "awaiting_confirmation",
        )
        attempt = workflows.begin_confirmation(conversation_id, confirmed["id"], str(uuid4()))
        workflows.succeed_attempt(
            attempt["id"],
            confirmed["id"],
            {"kind": "vehicle_interest", "status": "registered", "reference": "WAIT-OLD"},
        )
        newer = workflows.create_or_replace(
            conversation_id, "vehicle_interest", {"vehicleId": "veh-028"}, "new", "collecting"
        )
        browser.app.state.messages.add(conversation_id, "user", "Register me again.", None)
        browser.app.state.messages.add(
            conversation_id,
            "assistant",
            "Complete the new form.",
            None,
            "draft",
            f'{{"kind":"vehicle_interest","draftId":"{newer["id"]}"}}',
        )

        restored = browser.get(f"/api/chat/v1/conversations/{conversation_id}").json()

    assert [message["text"] for message in restored["messages"]] == [
        "Your interest has been registered.",
        "Register me again.",
        "Complete the new form.",
    ]


def test_create_restore_turn_replay_and_delete(tmp_path: Path) -> None:
    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        assert created.status_code == 201
        conversation_id = created.json()["conversationId"]
        cookie = created.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie

        client_message_id = str(uuid4())
        body = {"clientMessageId": client_message_id, "text": "Hello", "pageContext": CONTEXT}
        first = browser.post(f"/api/chat/v1/conversations/{conversation_id}/turns", json=body)
        replay = browser.post(f"/api/chat/v1/conversations/{conversation_id}/turns", json=body)
        assert first.status_code == 200
        assert replay.json() == first.json()
        assert len(first.json()["messages"]) == 2

        restored = browser.get(f"/api/chat/v1/conversations/{conversation_id}")
        assert [message["role"] for message in restored.json()["messages"]] == [
            "user",
            "assistant",
        ]

        deleted = browser.delete(f"/api/chat/v1/conversations/{conversation_id}")
        assert deleted.status_code == 204
        assert browser.get(f"/api/chat/v1/conversations/{conversation_id}").status_code == 404


def test_rejects_unknown_fields_and_unauthorized_restore(tmp_path: Path) -> None:
    with client(tmp_path) as browser:
        invalid = browser.post(
            "/api/chat/v1/conversations",
            json={"pageContext": CONTEXT, "apiKey": "must-not-be-accepted"},
        )
        assert invalid.status_code == 422
        assert browser.get(f"/api/chat/v1/conversations/{uuid4()}").status_code == 404


def test_new_conversation_preserves_session_history(tmp_path: Path) -> None:
    with client(tmp_path) as browser:
        first = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        browser.post(
            f"/api/chat/v1/conversations/{first['conversationId']}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Find BMWs in Stockport",
                "pageContext": CONTEXT,
            },
        )
        second = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()

        history = browser.get("/api/chat/v1/conversations")

        assert history.status_code == 200
        assert [item["conversationId"] for item in history.json()["items"]] == [
            second["conversationId"],
            first["conversationId"],
        ]
        assert history.json()["items"][1]["title"] == "Find BMWs in Stockport"
        assert browser.get(
            f"/api/chat/v1/conversations/{first['conversationId']}"
        ).status_code == 200


def test_turn_supplies_starting_and_current_page_snapshots_as_data(tmp_path: Path) -> None:
    class RecordingProvider:
        def __init__(self):
            self.messages = []

        async def generate_turn(self, messages):
            self.messages = messages
            return ProviderReply("I can use the page context.")

    provider = RecordingProvider()
    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    starting = {
        **CONTEXT,
        "section": "offers",
        "vehicleId": None,
        "heading": "Current offers",
        "pageText": "Published new-car offers.",
    }
    current = {
        **CONTEXT,
        "vehicleId": "veh-019",
        "heading": "Volvo XC40",
        "dialogText": "Volvo XC40 vehicle details",
    }
    with TestClient(create_app(settings, provider=provider)) as browser:
        created = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": starting}
        ).json()
        response = browser.post(
            f"/api/chat/v1/conversations/{created['conversationId']}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Can I test drive this?",
                "pageContext": current,
            },
        )

    assert response.status_code == 200
    developer_content = [
        message["content"]
        for message in provider.messages
        if message.get("role") == "developer"
    ]
    assert any("Current page context" in value and "veh-019" in value for value in developer_content)
    assert any(
        "Page where this conversation started" in value and "Current offers" in value
        for value in developer_content
    )
    assert any("Page-context rule" in value for value in developer_content)


def test_contact_details_always_return_a_structured_dealership_view(tmp_path: Path) -> None:
    class FakeTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "list_dealerships"
            assert arguments == {}
            assert conversation_id
            payload = {
                "version": 1,
                "items": [{"name": "Northstar Stockport", "town": "Stockport"}],
                "suggestions": [],
            }
            return ToolResult("Here are our dealerships.", "dealership_list", payload, payload)

    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.orchestrator.tools = FakeTools()

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "show me dealership contact details",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assistant = response.json()["messages"][1]
    assert assistant["viewType"] == "dealership_list"
    assert assistant["view"]["items"][0]["name"] == "Northstar Stockport"


def test_existing_workshop_booking_request_always_returns_private_lookup_form(tmp_path: Path) -> None:
    class FakeTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "request_workshop_booking_lookup_form"
            assert arguments == {"mode": "amend"}
            assert conversation_id
            payload = {"version": 1, "mode": "amend"}
            return ToolResult("Enter booking details privately.", "private_booking_lookup", payload, payload)

    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.orchestrator.tools = FakeTools()

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "I need to change an existing workshop booking.",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assistant = response.json()["messages"][1]
    assert assistant["viewType"] == "private_booking_lookup"
    assert assistant["view"] == {"version": 1, "mode": "amend"}


@pytest.mark.parametrize(
    ("mode", "tool_name", "view_type", "kind"),
    [
        ("amend", "prepare_workshop_amendment", "draft", "workshop_amend"),
        ("cancel", "prepare_workshop_cancellation", "confirmation", "workshop_cancel"),
    ],
)
def test_verified_booking_continues_directly_to_requested_workflow(
    tmp_path: Path, mode: str, tool_name: str, view_type: str, kind: str
) -> None:
    class FakeWorkflows:
        async def lookup_booking(self, conversation_id, proof):
            assert "mode" not in proof
            return {
                "booking": {
                    "reference": "WORK-10001",
                    "startsAt": "2026-08-24T09:00:00Z",
                    "dealershipId": "northstar-manchester",
                    "dealershipName": "Northstar Manchester",
                    "serviceTypeId": "mot",
                    "serviceTypeName": "MOT",
                    "status": "confirmed",
                }
            }

    class FakeTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == tool_name
            assert arguments == {}
            payload = {
                "version": 1,
                "draftId": "draft-workshop-001",
                "kind": kind,
                "status": "collecting" if mode == "amend" else "awaiting_confirmation",
                "summary": {
                    "bookingReference": "WORK-10001",
                    "service": "MOT",
                    "dealership": "Northstar Manchester",
                },
            }
            return ToolResult("Continue securely.", view_type, payload, payload)

    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.workflows = FakeWorkflows()
        browser.app.state.tools = FakeTools()
        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/workshop-booking-lookup",
            json={
                "reference": "WORK-10001",
                "lastName": "Taylor",
                "registration": "AB12 CDE",
                "phone": "07700900123",
                "mode": mode,
            },
        )

    assert response.status_code == 200
    assert response.json()["viewType"] == view_type
    assert response.json()["view"]["kind"] == kind


def test_verified_booking_lookup_returns_details_with_no_second_question(tmp_path: Path) -> None:
    class FakeWorkflows:
        async def lookup_booking(self, conversation_id, proof):
            return {
                "booking": {
                    "reference": "WORK-10001",
                    "startsAt": "2026-08-24T09:00:00Z",
                    "dealershipName": "Northstar Manchester",
                    "serviceTypeName": "MOT",
                    "status": "confirmed",
                }
            }

    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.workflows = FakeWorkflows()
        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/workshop-booking-lookup",
            json={
                "reference": "WORK-10001",
                "lastName": "Taylor",
                "registration": "AB12 CDE",
                "phone": "07700900123",
                "mode": "lookup",
            },
        )

    assert response.status_code == 200
    assert response.json()["viewType"] == "workshop_booking_details"
    assert response.json()["view"]["status"] == "confirmed"


def test_facet_read_still_returns_interactive_choice_chips(tmp_path: Path) -> None:
    class FacetProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            self.calls += 1
            if self.calls == 1:
                return ProviderReply(
                    "",
                    [ToolCall("load-fuel-facets", "get_vehicle_facets", {})],
                )
            return ProviderReply("Which fuel type would you like?\n- Diesel\n- Electric")

    class FakeTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "get_vehicle_facets"
            assert arguments == {}
            assert conversation_id
            return ToolResult(
                "Loaded current vehicle search options.",
                None,
                None,
                {"fuelTypes": ["Diesel", "Electric"]},
            )

    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.orchestrator.provider = FacetProvider()
        browser.app.state.orchestrator.tools = FakeTools()

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "I would like to choose a fuel type",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assistant = response.json()["messages"][1]
    assert assistant["text"] == "Choose a fuel type below."
    assert assistant["viewType"] == "suggestion_list"
    assert [item["label"] for item in assistant["view"]["suggestions"]] == [
        "Diesel",
        "Electric",
    ]


def test_selected_service_chip_always_returns_the_appointment_picker(tmp_path: Path) -> None:
    class FakeTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "list_workshop_slots"
            assert arguments == {"serviceTypeName": "Brake inspection"}
            assert conversation_id
            payload = {"version": 1, "items": [{"id": "ws-slot-0001"}]}
            return ToolResult("Found 1 result.", "slot_list", payload, payload)

    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.orchestrator.tools = FakeTools()

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Book service: Brake inspection",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assistant = response.json()["messages"][1]
    assert assistant["viewType"] == "slot_list"
    assert assistant["view"]["items"] == [{"id": "ws-slot-0001"}]


def test_free_text_live_service_selection_returns_the_appointment_picker(tmp_path: Path) -> None:
    class BookingProvider:
        async def generate_turn(self, messages):
            return ProviderReply(
                "",
                [
                    ToolCall(
                        "book-live-service",
                        "list_workshop_slots",
                        {"serviceTypeName": "Tyre fitting"},
                    )
                ],
            )

    class FakeTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            self.calls.append((name, arguments))
            assert conversation_id
            assert name == "list_workshop_slots"
            assert arguments == {"serviceTypeName": "Tyre fitting"}
            payload = {"version": 1, "items": [{"id": "ws-slot-0002"}]}
            return ToolResult("Found 1 result.", "slot_list", payload, payload)

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=BookingProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        tools = FakeTools()
        browser.app.state.orchestrator.tools = tools

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "I want to do tyre fitting",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assert tools.calls == [
        ("list_workshop_slots", {"serviceTypeName": "Tyre fitting"}),
    ]
    assistant = response.json()["messages"][1]
    assert assistant["viewType"] == "slot_list"


def test_live_service_name_does_not_restart_the_service_catalogue(tmp_path: Path) -> None:
    class BookingProvider:
        async def generate_turn(self, messages):
            return ProviderReply(
                "",
                [ToolCall("book-mot", "list_workshop_slots", {"serviceTypeName": "MOT"})],
            )

    class FakeTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            self.calls.append((name, arguments))
            assert conversation_id
            assert name == "list_workshop_slots"
            assert arguments == {"serviceTypeName": "MOT"}
            payload = {"version": 1, "items": [{"id": "ws-slot-0003"}]}
            return ToolResult("Found 1 result.", "slot_list", payload, payload)

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=BookingProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        tools = FakeTools()
        browser.app.state.orchestrator.tools = tools

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "I want MOT",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assert tools.calls == [
        ("list_workshop_slots", {"serviceTypeName": "MOT"}),
    ]
    assert response.json()["messages"][1]["viewType"] == "slot_list"


def test_typed_workshop_plan_cannot_turn_into_an_optional_location_question(
    tmp_path: Path,
) -> None:
    class PlannerProvider:
        async def generate_turn(self, messages):
            return ProviderReply(
                "",
                plan=TurnPlan(
                    "workshop_booking",
                    {"serviceQuery": "tyres"},
                    "Which town would you like the appointment in?",
                ),
            )

    class FakeTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            self.calls.append((name, arguments))
            payload = {"version": 1, "items": [{"id": "ws-slot-0004"}]}
            return ToolResult("Found 1 result.", "slot_list", payload, payload)

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=PlannerProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        tools = FakeTools()
        browser.app.state.orchestrator.tools = tools

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "I want the tyre work done",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assert tools.calls == [("list_workshop_slots", {"serviceTypeName": "tyres"})]
    assert response.json()["messages"][1]["viewType"] == "slot_list"


@pytest.mark.parametrize(
    "wording",
    [
        "How much does tyre fitting cost?",
        "What would I pay to have my tyres fitted?",
        "Can you tell me the price and duration for replacing my tyres?",
    ],
)
def test_service_information_questions_never_open_appointment_times(
    tmp_path: Path, wording: str
) -> None:
    class InformationProvider:
        async def generate_turn(self, messages):
            return ProviderReply(
                "",
                [ToolCall("service-information", "get_service_information", {"q": wording})],
            )

    class FakeTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            self.calls.append((name, arguments))
            assert name == "get_service_information"
            assert conversation_id
            return ToolResult(
                "Tyre fitting is priced on request and takes about 90 minutes. "
                "Tyre replacement and balancing.",
                None,
                None,
                {"service": {"name": "Tyre fitting"}},
            )

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=InformationProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        tools = FakeTools()
        browser.app.state.orchestrator.tools = tools
        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": wording,
                "pageContext": CONTEXT,
            },
        )

    assistant = response.json()["messages"][1]
    assert assistant["text"].startswith("Tyre fitting is priced on request")
    assert "viewType" not in assistant
    assert tools.calls == [("get_service_information", {"q": wording})]


def test_typed_service_choice_opens_slots_without_parsing_button_text(tmp_path: Path) -> None:
    class FakeTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "list_workshop_slots"
            assert arguments == {"serviceTypeId": "tyre-fitting"}
            assert conversation_id
            payload = {"version": 1, "items": [{"id": "ws-slot-0004"}]}
            return ToolResult("Found 1 result.", "slot_list", payload, payload)

    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.orchestrator.tools = FakeTools()
        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Use this workshop service",
                "pageContext": CONTEXT,
                "action": {
                    "type": "select_workshop_service",
                    "serviceTypeId": "tyre-fitting",
                },
            },
        )

    assert response.json()["messages"][1]["viewType"] == "slot_list"


def test_typed_fuel_choice_applies_the_filter_instead_of_searching_the_chip_text(
    tmp_path: Path,
) -> None:
    class UnexpectedProvider:
        async def generate_turn(self, messages):
            raise AssertionError("A typed preference action must not return to the model")

    class FakeTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            self.calls.append((name, arguments))
            payload = {
                "version": 1,
                "items": [],
                "page": 1,
                "pageSize": 3,
                "total": 0,
                "search": {"filters": arguments, "page": 1},
                "suggestions": [],
            }
            return ToolResult("Vehicles", "vehicle_list", payload, payload)

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=UnexpectedProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.conversations.update_workflow_state(
            conversation_id,
            {
                "version": 1,
                "domain": "vehicle",
                "intent": "vehicle_preferences",
                "stage": "choosing_preference",
                "entities": {"preferenceDimension": "fuelTypes"},
                "constraints": {"bodyStyle": "SUV"},
            },
        )
        tools = FakeTools()
        browser.app.state.orchestrator.tools = tools

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "I prefer Hybrid",
                "pageContext": CONTEXT,
                "action": {
                    "type": "apply_vehicle_preference",
                    "vehicleFilter": "fuelType",
                    "vehicleFilterValue": "Hybrid",
                },
            },
        )

    assert response.json()["status"] == "completed"
    assert tools.calls == [
        ("search_vehicles", {"bodyStyle": "SUV", "fuelType": "Hybrid"})
    ]


def test_offer_enquiry_action_uses_live_offer_context_and_sales_form(tmp_path: Path) -> None:
    class UnexpectedProvider:
        async def generate_turn(self, messages):
            raise AssertionError("A typed offer action must not return to the model")

    class FakeTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            self.calls.append((name, arguments))
            if name == "get_offer":
                offer = {
                    "id": "offer-07",
                    "make": "Jaguar",
                    "model": "F-PACE",
                    "productType": "PCP",
                }
                return ToolResult(
                    "Offer",
                    "offer_list",
                    {"version": 1, "items": [offer]},
                    offer,
                )
            assert name == "prepare_sales_enquiry"
            payload = {
                "version": 1,
                "kind": "sales_enquiry",
                "status": "collecting",
                "summary": arguments,
                "dealerships": [],
            }
            return ToolResult("Complete the form.", "draft", payload, payload)

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=UnexpectedProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        tools = FakeTools()
        browser.app.state.orchestrator.tools = tools

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "I want to enquire about this offer",
                "pageContext": CONTEXT,
                "action": {"type": "start_offer_enquiry", "offerId": "offer-07"},
            },
        )

    assert response.json()["messages"][1]["viewType"] == "draft"
    assert tools.calls == [
        ("get_offer", {"id": "offer-07"}),
        (
            "prepare_sales_enquiry",
            {
                "enquiryType": "finance",
                "message": "I am interested in the currently published Jaguar F-PACE PCP offer.",
            },
        ),
    ]


def test_vehicle_result_actions_use_the_current_structured_state(tmp_path: Path) -> None:
    class FakeTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            self.calls.append((name, arguments))
            assert conversation_id
            if name == "search_vehicles":
                page = arguments.get("page", 1)
                items = [
                    {"id": f"veh-{number:03d}", "model": f"Car {number}"}
                    for number in range((page - 1) * 3 + 1, page * 3 + 1)
                ]
                payload = {
                    "version": 1,
                    "items": items,
                    "page": page,
                    "pageSize": 3,
                    "total": 9,
                    "search": {
                        "filters": {"fuelType": "Hybrid", "sort": "priceAsc"},
                        "page": page,
                    },
                    "suggestions": [],
                }
                return ToolResult("Vehicles", "vehicle_list", payload, payload)
            assert name == "compare_vehicles"
            payload = {"version": 1, "items": [], "suggestions": []}
            return ToolResult("Comparison", "vehicle_comparison", payload, payload)

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings)) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        tools = FakeTools()
        browser.app.state.orchestrator.tools = tools

        first = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Show me cars",
                "pageContext": CONTEXT,
            },
        )
        more = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "show me more",
                "pageContext": CONTEXT,
                "action": {"type": "next_vehicle_page"},
            },
        )
        compared = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "compare the vehicles currently shown",
                "pageContext": CONTEXT,
                "action": {"type": "compare_displayed_vehicles"},
            },
        )

    assert first.json()["messages"][1]["viewType"] == "vehicle_list"
    assert more.json()["messages"][1]["view"]["page"] == 2
    assert tools.calls[-2] == (
        "search_vehicles",
        {"fuelType": "Hybrid", "sort": "priceAsc", "page": 2},
    )
    assert tools.calls[-1] == (
        "compare_vehicles",
        {"vehicleIds": ["veh-004", "veh-005", "veh-006"]},
    )
    assert compared.json()["messages"][1]["viewType"] == "vehicle_comparison"


def test_inline_test_drive_options_do_not_add_chat_messages(tmp_path: Path) -> None:
    class FakeTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "list_test_drive_slots"
            assert arguments == {"vehicleId": "veh-001"}
            assert conversation_id
            payload = {"version": 1, "vehicleId": "veh-001", "items": []}
            return ToolResult("Found 0 results.", "test_drive_slot_picker", payload, payload)

    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.tools = FakeTools()

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/test-drive-options",
            json={"vehicleId": "veh-001"},
        )
        restored = browser.get(f"/api/chat/v1/conversations/{conversation_id}")

    assert response.status_code == 200
    assert response.json()["viewType"] == "test_drive_slot_picker"
    assert restored.json()["messages"] == []


def test_test_drive_contact_details_are_validated_before_draft_creation(tmp_path: Path) -> None:
    class UnexpectedTools:
        async def execute(self, name, arguments, conversation_id):
            raise AssertionError("Invalid contact details must not reach the booking workflow")

    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.tools = UnexpectedTools()

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/test-drive-drafts",
            json={
                "slotId": "td-slot-0001",
                "firstName": "J1",
                "lastName": "S",
                "email": "not-an-email",
                "phone": "1234567",
            },
        )

    assert response.status_code == 422
    fields = {item["loc"][-1] for item in response.json()["detail"]}
    assert fields == {"firstName", "lastName", "email", "phone"}


def test_test_drive_contact_phone_is_normalized_before_draft_creation(tmp_path: Path) -> None:
    class FakeTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "prepare_test_drive"
            assert arguments["phone"] == "07123456789"
            assert conversation_id
            payload = {"version": 1, "draftId": "draft-001"}
            return ToolResult("Ready", "confirmation", payload, payload)

    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.tools = FakeTools()

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/test-drive-drafts",
            json={
                "slotId": "td-slot-0001",
                "firstName": "Jo",
                "lastName": "Smith",
                "email": "jo@example.com",
                "phone": "+44 7123 456789",
            },
        )

    assert response.status_code == 200
    assert response.json()["view"]["draftId"] == "draft-001"


def test_inline_workshop_options_do_not_add_chat_messages(tmp_path: Path) -> None:
    class FakeTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "list_workshop_slots"
            assert arguments == {"serviceTypeId": "full-service"}
            assert conversation_id
            payload = {"version": 1, "items": []}
            return ToolResult("Found 0 results.", "slot_list", payload, payload)

    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.tools = FakeTools()

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/workshop-options",
            json={"serviceTypeId": "full-service"},
        )
        restored = browser.get(f"/api/chat/v1/conversations/{conversation_id}")

    assert response.status_code == 200
    assert response.json()["viewType"] == "slot_list"
    assert restored.json()["messages"] == []


def test_workshop_details_are_validated_and_normalized_before_draft_creation(
    tmp_path: Path,
) -> None:
    class FakeTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "prepare_workshop_booking"
            assert arguments["phone"] == "07123456789"
            assert arguments["mileage"] == 42000
            assert conversation_id
            payload = {"version": 1, "draftId": "draft-workshop-001"}
            return ToolResult("Ready", "confirmation", payload, payload)

    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.tools = FakeTools()

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/workshop-drafts",
            json={
                "slotId": "ws-slot-0001",
                "firstName": "Jo",
                "lastName": "Smith",
                "email": "jo@example.com",
                "phone": "+44 7123 456789",
                "registration": "AB19 XYZ",
                "mileage": 42000,
            },
        )

    assert response.status_code == 200
    assert response.json()["view"]["draftId"] == "draft-workshop-001"


def test_part_exchange_details_are_validated_before_draft_creation(tmp_path: Path) -> None:
    class FakeTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "prepare_part_exchange"
            assert arguments["mileage"] == 45000
            assert arguments["phone"] == "07123456789"
            assert conversation_id
            payload = {"version": 1, "draftId": "draft-part-exchange-001"}
            return ToolResult("Ready", "confirmation", payload, payload)

    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.tools = FakeTools()

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/part-exchange-drafts",
            json={
                "dealershipId": "northstar-manchester",
                "registration": "AB19 XYZ",
                "mileage": 45000,
                "condition": "good",
                "firstName": "Jo",
                "lastName": "Smith",
                "email": "jo@example.com",
                "phone": "+44 7123 456789",
            },
        )

    assert response.status_code == 200
    assert response.json()["view"]["draftId"] == "draft-part-exchange-001"


def test_part_exchange_estimate_form_submits_without_contact_details(tmp_path: Path) -> None:
    class FakeTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "estimate_part_exchange"
            assert arguments == {
                "registration": "AB19 XYZ",
                "mileage": 45000,
                "condition": "good",
            }
            assert conversation_id
            payload = {
                "version": 1,
                "estimateLowPence": 1_355_000,
                "estimateHighPence": 1_520_000,
            }
            return ToolResult("Estimate ready", "part_exchange_estimate", payload, payload)

    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.tools = FakeTools()

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/part-exchange-estimates",
            json={
                "registration": "AB19 XYZ",
                "mileage": 45000,
                "condition": "good",
            },
        )

    assert response.status_code == 200
    assert response.json()["viewType"] == "part_exchange_estimate"
    assert response.json()["view"]["estimateLowPence"] == 1_355_000
