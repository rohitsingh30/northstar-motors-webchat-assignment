from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from webchat.config import Settings
from webchat.integrations.contracts import ProviderReply, ToolCall
from webchat.main import create_app
from webchat.orchestration.tools.result import ToolResult

CONTEXT = {"path": "/", "section": "vehicles", "vehicleId": "veh-001", "title": "Used Cars"}


class ExpectedToolProvider:
    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name

    async def generate_turn(self, messages):
        if any(message.get("role") == "tool" for message in messages):
            return ProviderReply("Here are the current details.")
        return ProviderReply("", [ToolCall("semantic-tool", self.tool_name, {})])


class ContractTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, name, arguments, conversation_id):
        self.calls.append((name, arguments))
        assert conversation_id
        if name == "get_vehicle_facets":
            return ToolResult(
                "Loaded facets.",
                None,
                None,
                {
                    "makes": ["BMW", "Volvo"],
                    "models": ["1 Series", "3 Series"],
                    "fuelTypes": ["Hybrid"],
                    "transmissions": ["Automatic"],
                    "bodyStyles": ["SUV"],
                },
            )
        if name == "list_service_types":
            items = [
                {"id": "mot", "name": "MOT"},
                {"id": "tyre-fitting", "name": "Tyre fitting"},
            ]
            return ToolResult(
                "Services.",
                "service_list",
                {"version": 1, "items": items, "suggestions": []},
                {"items": items},
            )
        view_type = {
            "get_business_information": "business_information",
            "compare_vehicle_models": "vehicle_comparison",
            "estimate_part_exchange": "part_exchange_estimate",
            "list_dealership_departments": "dealership_list",
            "list_dealerships": "dealership_list",
            "list_offers": "offer_list",
            "list_opening_hours": "opening_hours",
            "list_test_drive_slots": "test_drive_slot_picker",
            "list_workshop_locations": "workshop_location_list",
            "list_workshop_slots": "slot_list",
            "prepare_callback": "draft",
            "prepare_dealership_message": "draft",
            "prepare_part_exchange": "draft",
            "prepare_sales_enquiry": "draft",
            "prepare_vehicle_interest": "draft",
            "request_workshop_booking_lookup_form": "private_booking_lookup",
            "request_part_exchange_estimate_form": "part_exchange_estimate_form",
            "search_vehicles": "vehicle_list",
        }[name]
        payload = {"version": 1, "items": []}
        if view_type == "draft":
            payload = {"version": 1, "draftId": "draft-001", "kind": name, "status": "collecting"}
        return ToolResult(f"Completed {name}.", view_type, payload, payload)


@pytest.mark.parametrize(
    ("text", "expected_tool", "expected_view"),
    [
        ("Show me cars under £35,000", "search_vehicles", "vehicle_list"),
        ("Find hybrid SUVs below £45,000", "search_vehicles", "vehicle_list"),
        ("Compare the BMW 1 Series and BMW 3 Series", "compare_vehicle_models", "vehicle_comparison"),
        ("What new-car offers are currently published?", "list_offers", "offer_list"),
        ("Where are your workshop locations?", "list_workshop_locations", "workshop_location_list"),
        ("I need to change an existing workshop booking", "request_workshop_booking_lookup_form", "private_booking_lookup"),
        ("Show me dealership contact details", "list_dealerships", "dealership_list"),
        ("What departments does the dealership have?", "list_dealership_departments", "dealership_list"),
        ("What are your opening hours this Saturday?", "list_opening_hours", "opening_hours"),
        ("Leave a message for the service department", "prepare_dealership_message", "draft"),
        ("Please arrange a callback about this car", "prepare_callback", "draft"),
        (
            "Give me a part-exchange estimate for AB19 XYZ, 45,000 miles, in good condition",
            "estimate_part_exchange",
            "part_exchange_estimate",
        ),
        (
            "Give me an indicative part-exchange estimate",
            "request_part_exchange_estimate_form",
            "part_exchange_estimate_form",
        ),
        ("I want to make a part-exchange enquiry", "prepare_part_exchange", "draft"),
        ("I have a general enquiry about buying this car", "prepare_sales_enquiry", "draft"),
        ("This vehicle is reserved; register my interest", "prepare_vehicle_interest", "draft"),
        ("I would like to test drive this vehicle", "list_test_drive_slots", "test_drive_slot_picker"),
    ],
)
def test_semantic_tool_results_cannot_degrade_to_unstructured_prose(
    tmp_path: Path, text: str, expected_tool: str, expected_view: str
) -> None:
    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=ExpectedToolProvider(expected_tool))) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        tools = ContractTools()
        browser.app.state.orchestrator.tools = tools

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": text,
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["messages"][1]["viewType"] == expected_view
    assert tools.calls[-1][0] == expected_tool


class TerminalToolProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_turn(self, messages):
        self.calls += 1
        if self.calls > 1:
            raise AssertionError("A renderable terminal tool must end the model loop")
        return ProviderReply("", [ToolCall("offers", "list_offers", {})])


def test_model_renderable_tool_result_is_terminal(tmp_path: Path) -> None:
    provider = TerminalToolProvider()
    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=provider)) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.orchestrator.tools = ContractTools()
        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Tell me what is currently promoted",
                "pageContext": CONTEXT,
            },
        )

    assert response.json()["messages"][1]["viewType"] == "offer_list"
    assert provider.calls == 1


@pytest.mark.parametrize(
    ("text", "tool_name", "arguments", "view_type"),
    [
        (
            "I want to book a workshop appointment",
            "list_service_types",
            {},
            "service_list",
        ),
        (
            "I need an MOT in Manchester",
            "list_workshop_slots",
            {"serviceTypeName": "MOT", "dealershipTown": "Manchester"},
            "slot_list",
        ),
    ],
)
def test_free_form_service_intent_uses_semantic_tool_choice_and_structured_view(
    tmp_path: Path,
    text: str,
    tool_name: str,
    arguments: dict,
    view_type: str,
) -> None:
    class SemanticProvider:
        async def generate_turn(self, messages):
            if any(message.get("role") == "tool" for message in messages):
                return ProviderReply("Choose a service below.")
            return ProviderReply("", [ToolCall("semantic-service", tool_name, arguments)])

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=SemanticProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        tools = ContractTools()
        browser.app.state.orchestrator.tools = tools
        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": text,
                "pageContext": CONTEXT,
            },
        )

    assert response.json()["status"] == "completed"
    assert response.json()["messages"][1]["viewType"] == view_type
    assert tools.calls[-1] == (tool_name, arguments)


@pytest.mark.parametrize(
    ("text", "expected_tool", "expected_view"),
    [
        (
            "I want an indicative part-exchange estimate",
            "request_part_exchange_estimate_form",
            "part_exchange_estimate_form",
        ),
        (
            "I want to book a workshop appointment",
            "list_service_types",
            "service_list",
        ),
        (
            "show me the cheapest available cars",
            "search_vehicles",
            "vehicle_list",
        ),
        (
            "What service types do you support?",
            "list_service_types",
            "service_list",
        ),
        (
            "Find workshop availability for an annual service next week",
            "list_workshop_slots",
            "slot_list",
        ),
        (
            "How does finance work?",
            "get_business_information",
            "business_information",
        ),
        (
            "How do you use my personal data?",
            "get_business_information",
            "business_information",
        ),
        (
            "How is the part-exchange estimate calculated?",
            "get_business_information",
            "business_information",
        ),
    ],
)
def test_default_offline_provider_returns_structured_views_for_sample_questions(
    tmp_path: Path,
    text: str,
    expected_tool: str,
    expected_view: str,
) -> None:
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings)) as browser:
        created = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()
        tools = ContractTools()
        browser.app.state.orchestrator.tools = tools
        response = browser.post(
            f"/api/chat/v1/conversations/{created['conversationId']}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": text,
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["messages"][1]["viewType"] == expected_view
    assert tools.calls[-1][0] == expected_tool
