from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from webchat.config import Settings
from webchat.integrations.contracts import ProviderReply, ToolCall
from webchat.integrations.dealership import DealershipError
from webchat.integrations.fake_llm import FakeLlmProvider
from webchat.main import create_app
from webchat.orchestration.contracts.plan import InteractionProposal
from webchat.orchestration.contracts.response import (
    BulletSegment,
    FactSegment,
    GroundedMessageDraft,
    GroundedResponseDraft,
    LinkSegment,
    ListBlock,
    ListItem,
    ParagraphBlock,
    TextSegment,
)
from webchat.orchestration.tools.result import ToolResult

CONTEXT = {"path": "/", "section": "vehicles", "vehicleId": "veh-001", "title": "Used Cars"}


class ExpectedToolProvider:
    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name

    async def generate_turn(self, messages):
        if any(message.get("role") == "tool" for message in messages):
            return ProviderReply("Here are the current details.")
        arguments = {
            "compare_vehicle_models": {"queries": ["BMW 1 Series", "BMW 3 Series"]},
            "estimate_part_exchange": {
                "registration": "AB19 XYZ",
                "mileage": 45_000,
                "condition": "good",
            },
            "prepare_vehicle_interest": {"vehicleId": "veh-001"},
            "list_test_drive_slots": {"vehicleId": "veh-001"},
        }.get(self.tool_name, {})
        return ProviderReply("", [ToolCall("semantic-tool", self.tool_name, arguments)])


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
                {
                    "version": 1,
                    "items": items,
                    "suggestions": [],
                    "collectionPresentation": {
                        "schemaVersion": 1,
                        "layout": "bullet_list",
                        "purpose": "information",
                        "items": [{"label": item["name"]} for item in items],
                    },
                },
                {"items": items},
            )
        if name == "list_workshop_slots" and not (
            arguments.get("serviceTypeId") or arguments.get("serviceTypeName")
        ):
            items = [
                {"id": "mot", "name": "MOT"},
                {"id": "tyre-fitting", "name": "Tyre fitting"},
            ]
            payload = {
                "version": 1,
                "selectionOnly": True,
                "choiceEntityType": "service",
                "choiceField": "serviceTypeId",
                "collectionViewType": "choice_list",
                "items": items,
                "suggestions": [],
                "collectionPresentation": {
                    "schemaVersion": 1,
                    "layout": "chip_grid",
                    "purpose": "choice",
                    "items": [{"label": item["name"]} for item in items],
                },
            }
            return ToolResult(
                "Which workshop service would you like to book?",
                "service_list",
                payload,
                payload,
            )
        view_type = {
            "get_business_information": "business_information",
            "compare_vehicle_models": "vehicle_comparison",
            "estimate_part_exchange": "part_exchange_estimate",
            "list_dealership_departments": "dealership_list",
            "list_dealerships": "dealership_list",
            "list_offers": "offer_list",
            "list_opening_hours": "opening_hours",
            "list_holiday_opening_hours": "opening_hours",
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
        (
            "Compare the BMW 1 Series and BMW 3 Series",
            "compare_vehicle_models",
            "vehicle_comparison",
        ),
        ("What new-car offers are currently published?", "list_offers", "offer_list"),
        ("Where are your workshop locations?", "list_workshop_locations", "workshop_location_list"),
        (
            "I need to change an existing workshop booking",
            "request_workshop_booking_lookup_form",
            "private_booking_lookup",
        ),
        ("Show me dealership contact details", "list_dealerships", "dealership_list"),
        (
            "What departments does the dealership have?",
            "list_dealership_departments",
            "department_information",
        ),
        ("What are your opening hours this Saturday?", "list_opening_hours", "opening_hours"),
        ("Are you open on the bank holiday?", "list_holiday_opening_hours", "opening_hours"),
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
        (
            "I would like to test drive this vehicle",
            "list_test_drive_slots",
            "test_drive_slot_picker",
        ),
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
    payload = response.json()
    assert payload["status"] == "completed"
    _assert_conversational_result(payload, expected_view)
    assert tools.calls[-1][0] == expected_tool


class TerminalToolProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_turn(self, messages):
        self.calls += 1
        if any(message.get("role") == "tool" for message in messages):
            return ProviderReply("Here are the current offers.")
        return ProviderReply("", [ToolCall("offers", "list_offers", {})])


def test_renderable_tool_result_returns_to_the_composer(tmp_path: Path) -> None:
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

    _assert_conversational_result(response.json(), "offer_list")
    assert provider.calls == 2


def test_test_drive_draft_always_progresses_through_live_slot_resolution(
    tmp_path: Path,
) -> None:
    class ViewingProvider:
        async def generate_turn(self, messages):
            if any(message.get("role") == "tool" for message in messages):
                return ProviderReply("Here are the available appointment times.")
            latest = messages.planning_context.latest_customer_message.lower()
            arguments = (
                {
                    "dateFrom": "2026-09-07",
                    "dateTo": "2026-09-07",
                    "timeOfDay": "afternoon",
                }
                if "monday" in latest
                else {"vehicleId": "veh-001"}
            )
            return ProviderReply(
                "",
                [ToolCall("viewing", "prepare_test_drive", arguments)],
            )

    class ViewingTools:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        async def execute(self, name, arguments, conversation_id):
            self.calls.append((name, arguments))
            if name == "prepare_test_drive":
                payload = {
                    "version": 1,
                    "draftId": "draft-001",
                    "kind": "test_drive",
                    "status": "collecting",
                    "missingPublicFields": ["slotId"],
                    "secureFields": ["firstName", "lastName", "email", "phone"],
                    "secureInputReady": False,
                    "summary": {"vehicleId": arguments["vehicleId"]},
                }
                return ToolResult("Draft saved.", "draft", payload, payload)
            assert name == "list_test_drive_slots"
            payload = {
                "version": 1,
                "vehicleId": arguments["vehicleId"],
                "items": [
                    {
                        "id": "td-slot-0001",
                        "startsAt": "2026-09-07T14:00:00+01:00",
                        "endsAt": "2026-09-07T14:30:00+01:00",
                    }
                ],
            }
            return ToolResult(
                "Found a matching appointment.",
                "test_drive_slot_picker",
                payload,
                payload,
            )

    tools = ViewingTools()
    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=ViewingProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.orchestrator.tools = tools

        started = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "I'd like to arrange a viewing for this BMW.",
                "pageContext": CONTEXT,
            },
        )
        refined = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Monday afternoon",
                "pageContext": CONTEXT,
            },
        )

    assert started.status_code == 200
    assert refined.status_code == 200
    assert [name for name, _ in tools.calls] == [
        "prepare_test_drive",
        "list_test_drive_slots",
        "list_test_drive_slots",
    ]
    assert tools.calls[-1][1] == {
        "vehicleId": "veh-001",
        "dateFrom": "2026-09-07",
        "dateTo": "2026-09-07",
        "timeOfDay": "afternoon",
    }
    assert refined.json()["messages"][-1]["viewType"] == "trusted_slot_context"


@pytest.mark.parametrize(
    ("text", "tool_name", "arguments", "view_type"),
    [
        (
            "I want to book a workshop appointment",
            "list_workshop_slots",
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

    payload = response.json()
    assert payload["status"] == "completed"
    _assert_conversational_result(payload, view_type)
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
            "list_workshop_slots",
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
    # Pin the isolated test provider explicitly. A developer's uncommitted .env may contain
    # hosted credentials and must not change this offline contract test into a network test.
    with TestClient(create_app(settings, provider=FakeLlmProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
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
    payload = response.json()
    assert payload["status"] == "completed"
    _assert_conversational_result(payload, expected_view)
    assert tools.calls[-1][0] == expected_tool


def _assert_conversational_result(payload: dict, legacy_view: str) -> None:
    assistant = [message for message in payload["messages"] if message["role"] == "assistant"]
    assert assistant
    public_workflow_views = {
        "draft",
        "private_booking_lookup",
        "part_exchange_estimate_form",
        "slot_list",
        "test_drive_slot_picker",
    }
    if legacy_view in public_workflow_views:
        # Public workflow progress is authoritative server state, not a browser collector.
        # The response-level workflow activation is intentionally reserved for secure_input.
        assert payload["workflow"] is None
        assert payload["stateVersion"] > 0
        assert assistant[-1]["text"]
        assert all(message.get("viewType") != "secure_input" for message in assistant)
        return
    if legacy_view == "service_list" and any(
        message.get("viewType") in {"service_list", "choice_list"}
        and isinstance(message.get("view", {}).get("collectionPresentation"), dict)
        for message in assistant
    ):
        choice = next(
            message
            for message in assistant
            if message.get("viewType") in {"service_list", "choice_list"}
        )
        if choice["viewType"] == "choice_list":
            assert choice["view"]["choiceEntityType"] == "service"
            assert choice["view"]["choiceField"] == "serviceTypeId"
        assert payload["cards"] == []
        return
    simple_fact_views = {"business_information", "department_information"}
    if legacy_view in simple_fact_views:
        assert all(message.get("viewType") != legacy_view for message in assistant)
        assert assistant[0]["text"]
        return
    assert payload["cards"]
    assert any(message.get("viewType") == "grounded_presentation" for message in assistant)
    assert all(message.get("viewType") != legacy_view for message in assistant)


def _grounded_card_reply(messages, text: str) -> ProviderReply | None:
    trusted = messages.planning_context.trusted_tool_facts
    if not trusted:
        return None
    cards = [
        card["reference"]
        for result in trusted["results"]
        for card in result.get("availableCards", [])
    ]
    return ProviderReply(
        "",
        response_draft=GroundedResponseDraft(
            messages=[
                GroundedMessageDraft(
                    purpose="answer",
                    segments=[TextSegment(type="text", text=text)],
                )
            ],
            cardReferences=cards,
        ),
    )


def test_semantic_bullet_segments_survive_turn_response_and_restoration(
    tmp_path: Path,
) -> None:
    class BulletProvider:
        async def generate_turn(self, messages):
            trusted = messages.planning_context.trusted_tool_facts
            if not trusted:
                return ProviderReply(
                    "",
                    [ToolCall("service", "get_service_information", {"q": "MOT"})],
                )
            facts = trusted["results"][0]["facts"]
            by_field = {fact["field"]: fact["factId"] for fact in facts}
            return ProviderReply(
                "",
                response_draft=GroundedResponseDraft(
                    messages=[
                        GroundedMessageDraft(
                            purpose="answer",
                            blocks=[
                                ParagraphBlock(
                                    type="paragraph",
                                    segments=[TextSegment(type="text", text="MOT details:")],
                                ),
                                ListBlock(
                                    type="list",
                                    items=[
                                        ListItem(
                                            segments=[
                                                TextSegment(type="text", text="Price: "),
                                                FactSegment(
                                                    type="fact",
                                                    factId=by_field["service.priceFromPence"],
                                                ),
                                            ]
                                        ),
                                        ListItem(
                                            segments=[
                                                TextSegment(type="text", text="Duration: "),
                                                FactSegment(
                                                    type="fact",
                                                    factId=by_field["service.durationMinutes"],
                                                ),
                                            ]
                                        ),
                                    ],
                                ),
                            ],
                        )
                    ]
                ),
            )

    class BulletTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "get_service_information"
            assert arguments == {"q": "MOT"}
            assert conversation_id
            service = {
                "id": "mot",
                "name": "MOT",
                "priceFromPence": 5499,
                "durationMinutes": 60,
            }
            return ToolResult(
                "ignored tool prose",
                None,
                None,
                {"resolution": {"status": "matched"}, "service": service},
            )

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=BulletProvider())) as browser:
        browser.app.state.orchestrator.tools = BulletTools()
        conversation_id = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()["conversationId"]
        path = f"/api/chat/v1/conversations/{conversation_id}"
        response = browser.post(
            f"{path}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "How much is an MOT and how long does it take?",
                "pageContext": CONTEXT,
            },
        ).json()
        restored = browser.get(path).json()

    assistant = next(
        message for message in response["messages"] if message["role"] == "assistant"
    )
    expected_types = ["text", "bullet", "text", "fact", "bullet", "text", "fact"]
    assert [segment["type"] for segment in assistant["segments"]] == expected_types
    assert [block["type"] for block in assistant["blocks"]] == ["paragraph", "list"]
    assert len(assistant["blocks"][1]["items"]) == 2
    restored_answer = next(
        message
        for message in restored["messages"]
        if message["role"] == "assistant" and message.get("purpose") == "answer"
    )
    assert restored_answer["segments"] == assistant["segments"]
    assert restored_answer["blocks"] == assistant["blocks"]
    assert restored["messages"][-1]["purpose"] == "follow_up"


def test_vehicle_navigation_requires_latest_isolated_confirmation(tmp_path: Path) -> None:
    class NavigationProvider:
        async def generate_turn(self, messages):
            trusted = messages.planning_context.trusted_tool_facts
            if trusted and trusted.get("navigationConfirmationActivation"):
                suggestions = [
                    suggestion["reference"]
                    for result in trusted["results"]
                    for suggestion in result.get("availableSuggestions", [])
                ]
                return ProviderReply(
                    "",
                    response_draft=GroundedResponseDraft(
                        messages=[
                            GroundedMessageDraft(
                                purpose="follow_up",
                                segments=[
                                    TextSegment(
                                        type="text",
                                        text="Would you like me to open the full vehicle details?",
                                    )
                                ],
                            )
                        ],
                        suggestionReferences=suggestions,
                    ),
                )
            grounded = _grounded_card_reply(messages, "I found two current vehicles.")
            if grounded:
                return grounded
            latest = messages.planning_context.latest_customer_message.casefold()
            if "open" in latest:
                return ProviderReply(
                    "",
                    interaction_proposal=InteractionProposal(
                        kind="open_vehicle_detail",
                        entityReference="vehicle:veh-002",
                    ),
                )
            if "but" in latest or latest.strip() == "yes":
                return ProviderReply("I’ll keep this conversation in the chat.")
            return ProviderReply("", [ToolCall("search", "search_vehicles", {})])

    class NavigationTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "search_vehicles"
            assert arguments == {}
            assert conversation_id
            items = [
                {"id": "veh-001", "year": 2025, "make": "BMW", "model": "1 Series"},
                {"id": "veh-002", "year": 2024, "make": "MINI", "model": "Cooper"},
            ]
            return ToolResult(
                "ignored tool prose",
                "vehicle_list",
                {"version": 1, "items": items},
                {"items": items},
            )

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=NavigationProvider())) as browser:
        browser.app.state.orchestrator.tools = NavigationTools()

        def start_navigation():
            conversation_id = browser.post(
                "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
            ).json()["conversationId"]
            path = f"/api/chat/v1/conversations/{conversation_id}/turns"
            search = browser.post(
                path,
                json={
                    "clientMessageId": str(uuid4()),
                    "text": "Show me two vehicles",
                    "pageContext": CONTEXT,
                },
            ).json()
            proposal = browser.post(
                path,
                json={
                    "clientMessageId": str(uuid4()),
                    "text": "Open the second one",
                    "pageContext": CONTEXT,
                },
            ).json()
            return path, search, proposal

        path, search, proposal = start_navigation()
        assert search["cards"][0]["type"] == "vehicle_preview"
        assert proposal["clientActions"] == []
        assert proposal["pendingInteraction"]["kind"] == "open_vehicle_detail"
        assert "open the full vehicle details" in proposal["messages"][1]["text"]

        confirmed = browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "yes",
                "pageContext": CONTEXT,
            },
        ).json()
        assert confirmed["clientActions"][0]["vehicleId"] == "veh-002"
        assert confirmed["clientActions"][0]["sameSiteUrl"] == "/?vehicle=veh-002"

        interrupted_path, _search, _proposal = start_navigation()
        interrupted = browser.post(
            interrupted_path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "yes, but is it available?",
                "pageContext": CONTEXT,
            },
        ).json()
        stale_yes = browser.post(
            interrupted_path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "yes",
                "pageContext": CONTEXT,
            },
        ).json()
        assert interrupted["clientActions"] == []
        assert stale_yes["clientActions"] == []


def test_grounded_confirmation_uses_one_deterministic_idempotent_boundary(
    tmp_path: Path,
) -> None:
    class ConfirmationProvider:
        async def generate_turn(self, messages):
            grounded = _grounded_card_reply(messages, "Please review the callback request.")
            if grounded:
                return grounded
            return ProviderReply("", [ToolCall("callback", "prepare_callback", {})])

    class ConfirmationTools:
        async def execute(self, name, arguments, conversation_id):
            assert (name, arguments) == ("prepare_callback", {})
            return ToolResult(
                "ignored tool prose",
                "confirmation",
                {
                    "version": 1,
                    "kind": "callback",
                    "draftId": "draft-001",
                    "summary": {"dealershipId": "dealer-stockport"},
                },
                {"kind": "callback", "draftId": "draft-001"},
            )

    class ConfirmationWorkflows:
        def __init__(self):
            self.calls = []

        async def confirm(self, conversation_id, draft_id, action_id, expected_kind):
            self.calls.append((conversation_id, draft_id, action_id, expected_kind))
            return {"kind": "callback", "status": "confirmed", "reference": "CALL-001"}

    workflows = ConfirmationWorkflows()
    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=ConfirmationProvider())) as browser:
        browser.app.state.orchestrator.tools = ConfirmationTools()
        browser.app.state.orchestrator.workflows = workflows
        conversation_id = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()["conversationId"]
        path = f"/api/chat/v1/conversations/{conversation_id}/turns"
        review = browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "Request a callback",
                "pageContext": CONTEXT,
            },
        ).json()
        action_id = str(uuid4())
        confirmed = browser.post(
            path,
            json={
                "clientMessageId": action_id,
                "text": "go ahead",
                "pageContext": CONTEXT,
            },
        ).json()
        replay = browser.post(
            path,
            json={
                "clientMessageId": action_id,
                "text": "go ahead",
                "pageContext": CONTEXT,
            },
        ).json()

    assert review["cards"][0]["type"] == "confirmation"
    assert confirmed["cards"][0]["type"] == "receipt"
    assert confirmed["cards"][0]["data"]["reference"] == "CALL-001"
    assert confirmed["pendingInteraction"] is None
    assert replay["turnId"] == confirmed["turnId"]
    assert len(workflows.calls) == 1
    assert workflows.calls[0][1:] == ("draft-001", action_id, "callback")


def test_cancelled_workshop_receipt_offers_rebooking_next_steps(tmp_path: Path) -> None:
    class CancellationProvider:
        async def generate_turn(self, messages):
            grounded = _grounded_card_reply(messages, "Please review the cancellation.")
            if grounded:
                return grounded
            return ProviderReply(
                "",
                [ToolCall("cancel-workshop", "prepare_workshop_cancellation", {})],
            )

    class CancellationTools:
        async def execute(self, name, arguments, conversation_id):
            assert (name, arguments) == ("prepare_workshop_cancellation", {})
            assert conversation_id
            payload = {
                "version": 1,
                "kind": "workshop_cancel",
                "draftId": "draft-workshop-cancel-001",
                "status": "awaiting_confirmation",
                "summary": {"bookingReference": "WORK-10001"},
            }
            return ToolResult("Review", "confirmation", payload, payload)

    class CancellationWorkflows:
        async def confirm(self, *args):
            del args
            return {
                "kind": "workshop_cancel",
                "status": "cancelled",
                "reference": "WORK-10001",
            }

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=CancellationProvider())) as browser:
        browser.app.state.orchestrator.tools = CancellationTools()
        browser.app.state.orchestrator.workflows = CancellationWorkflows()
        conversation_id = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()["conversationId"]
        browser.app.state.conversations.update_workflow_state(
            conversation_id,
            {
                "version": 3,
                "activeWorkflow": "existing_workshop_booking",
                "stage": "verified",
                "entities": {},
                "constraints": {"bookingStatus": "confirmed"},
            },
        )
        path = f"/api/chat/v1/conversations/{conversation_id}/turns"
        browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "Cancel this workshop booking",
                "pageContext": CONTEXT,
            },
        )
        completed = browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "yes",
                "pageContext": CONTEXT,
            },
        ).json()

    assistant = [message for message in completed["messages"] if message["role"] == "assistant"]
    assert assistant[0]["viewType"] == "receipt"
    assert assistant[-1]["text"] == (
        "Would you like to book another appointment or find another booking?"
    )
    assert [item["label"] for item in assistant[-1]["view"]["quickReplies"]] == [
        "Book an appointment",
        "Find another booking",
    ]


def test_updated_workshop_receipt_offers_management_next_steps(tmp_path: Path) -> None:
    class AmendmentProvider:
        async def generate_turn(self, messages):
            grounded = _grounded_card_reply(messages, "Please review the booking update.")
            if grounded:
                return grounded
            return ProviderReply(
                "",
                [
                    ToolCall(
                        "amend-workshop",
                        "prepare_workshop_amendment",
                        {"notes": "Please check the brakes"},
                    )
                ],
            )

    class AmendmentTools:
        async def execute(self, name, arguments, conversation_id):
            assert (name, arguments) == (
                "prepare_workshop_amendment",
                {"notes": "Please check the brakes"},
            )
            assert conversation_id
            payload = {
                "version": 1,
                "kind": "workshop_amend",
                "draftId": "draft-workshop-amend-001",
                "status": "awaiting_confirmation",
                "summary": {"bookingReference": "WORK-10001"},
            }
            return ToolResult("Review", "confirmation", payload, payload)

    class AmendmentWorkflows:
        async def confirm(self, *args):
            del args
            return {
                "kind": "workshop_amend",
                "status": "confirmed",
                "reference": "WORK-10001",
            }

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=AmendmentProvider())) as browser:
        browser.app.state.orchestrator.tools = AmendmentTools()
        browser.app.state.orchestrator.workflows = AmendmentWorkflows()
        conversation_id = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()["conversationId"]
        browser.app.state.conversations.update_workflow_state(
            conversation_id,
            {
                "version": 3,
                "activeWorkflow": "existing_workshop_booking",
                "stage": "verified",
                "entities": {},
                "constraints": {"bookingStatus": "confirmed"},
            },
        )
        path = f"/api/chat/v1/conversations/{conversation_id}/turns"
        browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "Add a note to this workshop booking",
                "pageContext": CONTEXT,
            },
        )
        completed = browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "yes",
                "pageContext": CONTEXT,
            },
        ).json()

    assistant = [message for message in completed["messages"] if message["role"] == "assistant"]
    assert assistant[0]["viewType"] == "receipt"
    assert assistant[-1]["text"] == "Would you like to edit or cancel this booking?"
    assert [item["label"] for item in assistant[-1]["view"]["quickReplies"]] == [
        "Edit booking",
        "Cancel booking",
    ]


def test_stale_confirmation_returns_public_slot_choice_to_the_ai(tmp_path: Path) -> None:
    class RecoveryProvider:
        async def generate_turn(self, messages):
            trusted = messages.planning_context.trusted_tool_facts
            if trusted:
                is_recovery = trusted["results"][0]["tool"] == "protected_confirmation"
                text = (
                    "That appointment is no longer available. Which day or time suits you instead?"
                    if is_recovery
                    else "Please review the workshop request."
                )
                cards = [
                    card["reference"]
                    for result in trusted["results"]
                    for card in result.get("availableCards", [])
                ]
                return ProviderReply(
                    "",
                    response_draft=GroundedResponseDraft(
                        messages=[
                                GroundedMessageDraft(
                                    purpose="workflow_prompt" if is_recovery else "answer",
                                    segments=(
                                        [
                                            TextSegment(type="text", text=text),
                                            BulletSegment(type="bullet"),
                                            TextSegment(
                                                type="text",
                                                text="A preferred day or date",
                                            ),
                                            BulletSegment(type="bullet"),
                                            TextSegment(
                                                type="text",
                                                text="An approximate time",
                                            ),
                                        ]
                                        if is_recovery
                                        else [TextSegment(type="text", text=text)]
                                    ),
                                )
                        ],
                        cardReferences=cards,
                    ),
                )
            return ProviderReply(
                "",
                [ToolCall("workshop", "prepare_workshop_booking", {})],
            )

    class RecoveryTools:
        async def execute(self, name, arguments, conversation_id):
            assert (name, arguments) == ("prepare_workshop_booking", {})
            assert conversation_id
            payload = {
                "version": 1,
                "kind": "workshop_booking",
                "draftId": "draft-workshop-001",
                "summary": {
                    "serviceTypeId": "mot",
                    "dealershipId": "northstar-manchester",
                    "slotId": "ws-slot-0001",
                },
            }
            return ToolResult("Review", "confirmation", payload, payload)

    class RecoveryWorkflows:
        async def confirm(self, *args):
            del args
            raise DealershipError(
                409,
                "SLOT_UNAVAILABLE",
                "That workshop appointment is no longer available.",
                recovery={
                    "journey": "workshop",
                    "viewType": "slot_list",
                    "view": {
                        "version": 1,
                        "items": [
                            {
                                "id": "ws-slot-0002",
                                "serviceTypeId": "mot",
                                "dealershipId": "northstar-manchester",
                            }
                        ],
                    },
                },
            )

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=RecoveryProvider())) as browser:
        browser.app.state.orchestrator.tools = RecoveryTools()
        browser.app.state.orchestrator.workflows = RecoveryWorkflows()
        conversation_id = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()["conversationId"]
        path = f"/api/chat/v1/conversations/{conversation_id}/turns"
        browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "Book the selected workshop appointment",
                "pageContext": CONTEXT,
            },
        )
        response = browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "yes",
                "pageContext": CONTEXT,
            },
        ).json()
        state = browser.app.state.conversations.get_state(conversation_id)
        latest_interaction = browser.app.state.protected_interactions.latest(conversation_id)

    recovery_message = next(
        message for message in response["messages"] if message["role"] == "assistant"
    )
    assert response["pendingInteraction"] is None
    assert recovery_message["viewType"] == "trusted_slot_context"
    assert recovery_message["view"]["items"][0]["id"] == "ws-slot-0002"
    assert state.agentWorkflow["stage"] == "choosing_time"
    assert state.pendingInteraction.status == "unavailable"
    assert latest_interaction.status == "unavailable"


def test_completed_write_keeps_a_receipt_when_ai_composition_fails(tmp_path: Path) -> None:
    class ReceiptFailureProvider:
        async def generate_turn(self, messages):
            trusted = messages.planning_context.trusted_tool_facts
            if trusted:
                if trusted["results"][0]["tool"] == "protected_confirmation":
                    raise TimeoutError("composer unavailable after write")
                return _grounded_card_reply(messages, "Please review the callback request.")
            return ProviderReply("", [ToolCall("callback", "prepare_callback", {})])

    class ReceiptTools:
        async def execute(self, name, arguments, conversation_id):
            assert (name, arguments) == ("prepare_callback", {})
            assert conversation_id
            payload = {
                "version": 1,
                "kind": "callback",
                "draftId": "draft-callback-001",
                "summary": {"dealershipId": "northstar-manchester"},
            }
            return ToolResult("Review", "confirmation", payload, payload)

    class ReceiptWorkflows:
        async def confirm(self, *args):
            del args
            return {"kind": "callback", "status": "confirmed", "reference": "CALL-001"}

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=ReceiptFailureProvider())) as browser:
        browser.app.state.orchestrator.tools = ReceiptTools()
        browser.app.state.orchestrator.workflows = ReceiptWorkflows()
        conversation_id = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()["conversationId"]
        path = f"/api/chat/v1/conversations/{conversation_id}/turns"
        browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "Request a callback",
                "pageContext": CONTEXT,
            },
        )
        completed = browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "go ahead",
                "pageContext": CONTEXT,
            },
        ).json()

    receipt = next(message for message in completed["messages"] if message["role"] == "assistant")
    assert completed["status"] == "completed"
    assert completed["error"] is None
    assert completed["pendingInteraction"] is None
    assert receipt["viewType"] == "receipt"
    assert receipt["view"]["reference"] == "CALL-001"


def test_compound_knowledge_and_live_read_share_one_grounded_composition(
    tmp_path: Path,
) -> None:
    approved = {
        "id": "customer.pch",
        "title": "PCH",
        "text": "PCH means Personal Contract Hire.",
        "source": "docs/CUSTOMER-KNOWLEDGE.md#PCH",
    }

    class CompoundProvider:
        async def generate_turn(self, messages):
            trusted = messages.planning_context.trusted_tool_facts
            if not trusted:
                return ProviderReply(
                    "",
                    [ToolCall("offers", "list_offers", {"productType": "PCH"})],
                    approved_content=(approved,),
                )
            knowledge = next(
                result for result in trusted["results"] if result["tool"] == "approved_content"
            )
            offers = next(result for result in trusted["results"] if result["tool"] == "list_offers")
            return ProviderReply(
                "",
                response_draft=GroundedResponseDraft(
                    messages=[
                        GroundedMessageDraft(
                            purpose="answer",
                            segments=[
                                FactSegment(
                                    type="fact",
                                    factId=knowledge["facts"][0]["factId"],
                                )
                            ],
                        ),
                        GroundedMessageDraft(
                            purpose="answer",
                            segments=[
                                TextSegment(type="text", text="Here are the current PCH offers.")
                            ],
                        ),
                    ],
                    cardReferences=[offers["availableCards"][0]["reference"]],
                ),
            )

    class OfferTools:
        async def execute(self, name, arguments, conversation_id):
            assert (name, arguments) == ("list_offers", {"productType": "PCH"})
            item = {"id": "offer-01", "title": "MINI Cooper PCH", "productType": "PCH"}
            return ToolResult(
                "ignored tool prose",
                "offer_list",
                {"version": 1, "items": [item]},
                {"items": [item]},
            )

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=CompoundProvider())) as browser:
        browser.app.state.orchestrator.tools = OfferTools()
        conversation_id = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()["conversationId"]
        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "What is PCH, and show me current PCH offers",
                "pageContext": CONTEXT,
            },
        ).json()

    assistant = [message for message in response["messages"] if message["role"] == "assistant"]
    assert [message["text"] for message in assistant] == [
        "PCH means Personal Contract Hire.",
        "Here are the current PCH offers.",
        "What else can I help you with?",
    ]
    assert response["cards"][0]["type"] == "offer"


def test_trusted_inline_links_survive_turn_response_and_restoration(tmp_path: Path) -> None:
    class ContactProvider:
        async def generate_turn(self, messages):
            trusted = messages.planning_context.trusted_tool_facts
            if not trusted:
                return ProviderReply("", [ToolCall("locations", "list_dealerships", {})])
            result = trusted["results"][0]
            phone = next(link for link in result["availableLinks"] if link["destinationKind"] == "telephone")
            return ProviderReply(
                "",
                response_draft=GroundedResponseDraft(
                    messages=[
                        GroundedMessageDraft(
                            purpose="answer",
                            segments=[
                                TextSegment(type="text", text="You can reach Manchester on "),
                                LinkSegment(type="link", linkReference=phone["reference"]),
                                TextSegment(type="text", text="."),
                            ],
                        )
                    ],
                    linkReferences=[phone["reference"]],
                ),
            )

    class ContactTools:
        async def execute(self, name, arguments, conversation_id):
            assert (name, arguments) == ("list_dealerships", {})
            assert conversation_id
            location = {
                "id": "northstar-manchester",
                "name": "Northstar Manchester",
                "phone": "0161 555 0100",
            }
            return ToolResult(
                "ignored tool prose",
                "dealership_list",
                {"version": 1, "items": [location]},
                {"items": [location]},
            )

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=ContactProvider())) as browser:
        browser.app.state.orchestrator.tools = ContactTools()
        conversation_id = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()["conversationId"]
        path = f"/api/chat/v1/conversations/{conversation_id}"
        response = browser.post(
            f"{path}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "How can I call Manchester?",
                "pageContext": CONTEXT,
            },
        ).json()
        restored = browser.get(path).json()

    expected = [
        {"type": "text", "text": "You can reach Manchester on "},
        {
            "type": "link",
            "label": "Call 0161 555 0100",
            "href": "tel:01615550100",
            "destinationKind": "telephone",
        },
        {"type": "text", "text": "."},
    ]
    response_assistant = next(
        message for message in response["messages"] if message["role"] == "assistant"
    )
    assert response_assistant["segments"] == expected
    restored_answer = next(
        message
        for message in restored["messages"]
        if message["role"] == "assistant" and message.get("purpose") == "answer"
    )
    assert restored_answer["segments"] == expected
    assert restored["messages"][-1]["purpose"] == "follow_up"
