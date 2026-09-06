from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from webchat.config import Settings
from webchat.domain.interactions import (
    input_interaction,
    reference_choice_interaction,
    single_action_interaction,
)
from webchat.integrations.contracts import (
    PlanningValidationError,
    ProviderReply,
    ProviderUnavailableError,
    ToolCall,
)
from webchat.integrations.fake_llm import FakeLlmProvider
from webchat.main import create_app
from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.contracts.response import (
    BulletSegment,
    FactSegment,
    GroundedMessageDraft,
    GroundedResponseDraft,
    ParagraphBlock,
    TextSegment,
)
from webchat.orchestration.contracts.semantics import RequestedInputChange, TurnUnderstanding
from webchat.orchestration.state import workflow_state
from webchat.orchestration.tools.executor import ApplicationToolExecutor
from webchat.orchestration.tools.result import ToolResult

CONTEXT = {"path": "/", "section": "vehicles", "vehicleId": "veh-001", "title": "Used Cars"}


def direct_tool_calls(
    name: str,
    arguments: dict | None = None,
) -> list[ToolCall]:
    return [ToolCall(f"test-{name}", name, dict(arguments or {}))]


def grounded_test_reply(messages, text: str = "Here are the current Northstar details."):
    """Model the required post-tool composition call in semantic-provider tests."""
    trusted = messages.planning_context.trusted_tool_facts
    if not trusted:
        return None
    cards = [
        card["reference"]
        for result in trusted["results"]
        for card in result.get("availableCards", [])
    ]
    suggestions = [
        item["reference"]
        for result in trusted["results"]
        for item in result.get("availableSuggestions", [])
    ]
    collections = [
        item["reference"]
        for result in trusted["results"]
        for item in result.get("availableCollections", [])
    ]
    collection_purposes = [
        str(item.get("presentation", {}).get("purpose") or "")
        for result in trusted["results"]
        for item in result.get("availableCollections", [])
    ]
    obligation_kinds = {
        str(result.get("responseObligation", {}).get("kind") or "")
        for result in trusted["results"]
        if isinstance(result.get("responseObligation"), dict)
    }
    required_fact_ids = list(
        dict.fromkeys(
            str(fact_id)
            for result in trusted["results"]
            for fact_id in (result.get("responseObligation") or {}).get("requiredFactIds", [])
        )
    )
    workflow_state = messages.planning_context.workflow_state
    constraints = dict(workflow_state.get("constraints") or {})
    requested = list(constraints.get("missingPublicFields") or [])
    if not requested and constraints.get("secureInputReady") is True:
        requested = list(constraints.get("secureFields") or [])
    segments = [TextSegment(type="text", text=text)]
    for fact_id in required_fact_ids:
        segments.extend(
            [
                TextSegment(type="text", text=" "),
                FactSegment(type="fact", factId=fact_id),
                TextSegment(type="text", text="."),
            ]
        )
    for field in requested if not collections else []:
        segments.extend(
            [
                BulletSegment(type="bullet"),
                TextSegment(
                    type="text",
                    text={
                        "preferredDayOrDate": "A preferred day or date",
                        "approximateTime": "An approximate time",
                        "slotId": "The appointment time that suits you",
                    }.get(field, field),
                ),
            ]
        )
    grounded_messages = [
        GroundedMessageDraft(
            purpose=(
                "workflow_prompt"
                if requested or (collection_purposes and collection_purposes[0] == "choice")
                else "clarification"
                if collection_purposes and collection_purposes[0] == "clarification"
                else "answer"
            ),
            segments=segments,
            collectionReference=collections[0] if collections else None,
        )
    ]
    grounded_messages.extend(
        GroundedMessageDraft(
            purpose="answer",
            segments=[TextSegment(type="text", text="Here are the other available options.")],
            collectionReference=reference,
        )
        for reference in collections[1:]
    )
    if (
        not requested
        and grounded_messages[-1].purpose == "answer"
        and obligation_kinds.intersection({"catalogue_results", "informational_next_steps"})
    ):
        grounded_messages.append(
            GroundedMessageDraft(
                purpose="follow_up",
                segments=[TextSegment(type="text", text="What would you like help with next?")],
            )
        )
    return ProviderReply(
        "",
        response_draft=GroundedResponseDraft(
            messages=grounded_messages,
            cardReferences=cards,
            suggestionReferences=suggestions,
        ),
    )


def card_data(payload: dict, card_type: str) -> list[dict]:
    return [card["data"] for card in payload.get("cards", []) if card["type"] == card_type]


def client(tmp_path: Path) -> TestClient:
    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    # Integration tests are offline and must not inherit a developer's hosted-provider secrets.
    return TestClient(create_app(settings, provider=FakeLlmProvider()))


def test_grounded_comparison_is_recomposed_when_the_first_answer_only_lists_inventory(
    tmp_path: Path,
) -> None:
    class ComparisonProvider:
        def __init__(self) -> None:
            self.compositions = 0

        async def generate_turn(self, messages):
            trusted = messages.planning_context.trusted_tool_facts
            if not trusted:
                return ProviderReply(
                    "",
                    tool_calls=[
                        ToolCall(
                            "compare-models",
                            "compare_vehicle_models",
                            {"queries": ["BMW 3 Series", "MINI Cooper"]},
                            intent_kind="vehicle_comparison",
                        )
                    ],
                )
            self.compositions += 1
            result = trusted["results"][0]
            facts = {
                (fact.get("entityReference"), fact["field"].rsplit(".", 1)[-1]): fact["factId"]
                for fact in result["facts"]
            }
            segments = [
                TextSegment(type="text", text="Price is "),
                FactSegment(type="fact", factId=facts[("vehicle:veh-014", "pricePence")]),
                TextSegment(type="text", text=" compared with "),
                FactSegment(type="fact", factId=facts[("vehicle:veh-041", "pricePence")]),
                TextSegment(type="text", text=". Mileage is "),
                FactSegment(type="fact", factId=facts[("vehicle:veh-014", "mileage")]),
                TextSegment(type="text", text=" compared with "),
                FactSegment(type="fact", factId=facts[("vehicle:veh-041", "mileage")]),
                TextSegment(type="text", text="."),
            ]
            return ProviderReply(
                "",
                response_draft=GroundedResponseDraft(
                    messages=(
                        [
                            GroundedMessageDraft(
                                purpose="answer",
                                segments=segments,
                            )
                        ]
                        if self.compositions == 1
                        else [
                            GroundedMessageDraft(
                                purpose="comparison",
                                segments=segments,
                            ),
                            GroundedMessageDraft(
                                purpose="follow_up",
                                segments=[
                                    TextSegment(
                                        type="text",
                                        text="Which option would you like to explore next?",
                                    )
                                ],
                            ),
                        ]
                    ),
                    cardReferences=[result["availableCards"][0]["reference"]],
                ),
            )

    class ComparisonTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "compare_vehicle_models"
            assert arguments == {"queries": ["BMW 3 Series", "MINI Cooper"]}
            items = [
                {
                    "id": "veh-014",
                    "make": "BMW",
                    "model": "3 Series",
                    "pricePence": 2100000,
                    "mileage": 30000,
                },
                {
                    "id": "veh-041",
                    "make": "MINI",
                    "model": "Cooper",
                    "pricePence": 2900000,
                    "mileage": 5000,
                },
            ]
            payload = {"version": 1, "items": items, "suggestions": []}
            return ToolResult(
                "Compared current vehicles.",
                "vehicle_comparison",
                payload,
                {"items": items, "comparison": {"vehicleIds": ["veh-014", "veh-041"]}},
            )

    provider = ComparisonProvider()
    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=provider)) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.orchestrator.tools = ComparisonTools()
        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Compare a BMW 3 Series with a MINI Cooper",
                "pageContext": CONTEXT,
            },
        )

    assert response.json()["status"] == "completed"
    assert provider.compositions == 2
    assert response.json()["messages"][1]["purpose"] == "comparison"
    assert response.json()["messages"][1]["view"]["cards"][0]["type"] == "vehicle_comparison"
    assert response.json()["messages"][2]["purpose"] == "follow_up"
    assert response.json()["messages"][2]["view"] is None


def test_timeout_failure_returns_a_specific_retryable_customer_message(tmp_path: Path) -> None:
    class TimeoutProvider:

        async def generate_turn(self, messages):
            del messages
            raise TimeoutError("provider exceeded its budget")

    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
        llm_turn_timeout_seconds=45,
    )
    with TestClient(create_app(settings, provider=TimeoutProvider())) as browser:
        assert browser.app.state.orchestrator.timeout_seconds == 45
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        response = browser.post(
            f"/api/chat/v1/conversations/{created['conversationId']}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Show me current offers",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error"] == {
        "code": "LLM_TIMEOUT",
        "message": "The AI service is taking longer than expected. Please retry.",
        "retryable": True,
    }


def test_planning_failure_completes_with_customer_safe_clarification(
    tmp_path: Path,
) -> None:
    class InvalidPlanningProvider:

        async def generate_turn(self, messages):
            del messages
            raise PlanningValidationError("return_a_valid_typed_customer_turn_proposal")

    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=InvalidPlanningProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        response = browser.post(
            f"/api/chat/v1/conversations/{created['conversationId']}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Book an appointment for the 3 Series",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["error"] is None
    assert payload["messages"][-1]["text"] == (
        "I’m not completely sure how to handle that request. Could you rephrase it or tell me "
        "the outcome you want?"
    )


def test_executed_information_read_preserves_open_workflow_without_appending_its_question(
    tmp_path: Path,
) -> None:
    schedule_prompt = (
        "The selected vehicle is at Northstar Liverpool. What day or date would suit you, "
        "and roughly what time works best?"
    )

    class InterruptionProvider:

        async def generate_turn(self, messages):
            context = messages.planning_context
            trusted = context.trusted_tool_facts
            if trusted:
                result = trusted["results"][-1]
                cards = [card["reference"] for card in result.get("availableCards", [])]
                if result["tool"] == "list_dealerships":
                    return ProviderReply(
                        "",
                        response_draft=GroundedResponseDraft(
                            messages=[
                                GroundedMessageDraft(
                                    purpose="answer",
                                    segments=[
                                        TextSegment(
                                            type="text",
                                            text="Here are the requested dealership details.",
                                        )
                                    ],
                                ),
                                GroundedMessageDraft(
                                    purpose="follow_up",
                                    segments=[
                                        TextSegment(
                                            type="text",
                                            text=(
                                                "Would you like opening hours or to continue "
                                                "arranging your test drive?"
                                            ),
                                        )
                                    ],
                                ),
                            ],
                            cardReferences=cards,
                        ),
                    )
                location_fact = result["responseObligation"]["requiredFactIds"][0]
                return ProviderReply(
                    "",
                    response_draft=GroundedResponseDraft(
                        messages=[
                            GroundedMessageDraft(
                                purpose="workflow_prompt",
                                segments=[
                                    TextSegment(type="text", text="The selected vehicle is at "),
                                    FactSegment(type="fact", factId=location_fact),
                                    TextSegment(
                                        type="text",
                                        text=(
                                            ". What day or date would suit you, and roughly what "
                                            "time works best?"
                                        ),
                                    ),
                                ],
                            )
                        ]
                    ),
                )
            if "where" in context.latest_customer_message.casefold():
                return ProviderReply(
                    "",
                    tool_calls=[
                        ToolCall(
                            "dealership-location",
                            "list_dealerships",
                            {"town": "Liverpool"},
                            intent_kind="dealership_detail",
                        )
                    ],
                    turn_understanding=TurnUnderstanding(
                        dialogueAct="switch_goal",
                        goalRelation="active",
                        intentKinds=["dealership_detail"],
                        confidence="high",
                    ),
                )
            return ProviderReply(
                "",
                tool_calls=[
                    ToolCall(
                        "test-drive-schedule",
                        "list_test_drive_slots",
                        {"vehicleId": "veh-001"},
                        intent_kind="test_drive",
                    )
                ],
                turn_understanding=TurnUnderstanding(
                    dialogueAct=(
                        "continue_goal"
                        if "book" in context.latest_customer_message.casefold()
                        and context.workflow_state
                        else "start_goal"
                    ),
                    goalRelation="active" if context.workflow_state else "new",
                    intentKinds=["test_drive"],
                    resolvedReferences=([] if context.workflow_state else ["vehicle:veh-001"]),
                    confidence="high",
                ),
            )

    class InterruptionTools:
        async def execute(self, name, arguments, conversation_id):
            assert conversation_id
            if name == "list_dealerships":
                assert arguments == {"town": "Liverpool"}
                dealership = {
                    "id": "northstar-liverpool",
                    "name": "Northstar Liverpool",
                    "town": "Liverpool",
                    "address": "8 Regent Road",
                    "postcode": "L5 9YN",
                }
                return ToolResult(
                    "Northstar Liverpool is in Liverpool.",
                    "dealership_list",
                    {"version": 1, "items": [dealership]},
                    {"items": [dealership]},
                )
            assert name == "list_test_drive_slots"
            assert arguments == {"vehicleId": "veh-001"}
            vehicle = {
                "id": "veh-001",
                "year": 2025,
                "make": "BMW",
                "model": "X3",
                "variant": "xDrive20d M Sport",
                "dealershipName": "Northstar Liverpool",
                "dealershipTown": "Liverpool",
            }
            return ToolResult(
                "The selected vehicle at Northstar Liverpool is ready for scheduling.",
                None,
                None,
                {
                    "vehicle": vehicle,
                    "resolution": {
                        "kind": "test_drive_slots",
                        "status": "ready",
                        "scope": "broad",
                        "continuation": "request_schedule_preferences",
                        "vehicleId": "veh-001",
                        "count": 0,
                        "scheduleAlternativeCount": 0,
                        "locationAlternativeCount": 0,
                        "equivalentVehicleSlotCount": 0,
                    },
                },
            )

    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=InterruptionProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        conversation_id = created["conversationId"]
        browser.app.state.orchestrator.tools = InterruptionTools()
        path = f"/api/chat/v1/conversations/{conversation_id}/turns"

        started = browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "Book a test drive for the X3",
                "pageContext": CONTEXT,
            },
        )
        interrupted = browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "Where is this car located?",
                "pageContext": CONTEXT,
            },
        )
        continued = browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "Book the test drive",
                "pageContext": CONTEXT,
            },
        )
        state = browser.app.state.conversations.get_state(conversation_id)

    assert started.json()["status"] == "completed"
    assert started.json()["messages"][-1]["text"] == schedule_prompt
    assert interrupted.json()["status"] == "completed"
    assert interrupted.json()["messages"][-1]["text"] == (
        "Would you like opening hours or to continue arranging your test drive?"
    )
    assert interrupted.json()["messages"][-1]["purpose"] == "follow_up"
    assert card_data(interrupted.json(), "dealership")
    assert continued.json()["status"] == "completed"
    assert continued.json()["messages"][-1]["text"] == schedule_prompt
    assert "not completely sure" not in continued.json()["messages"][-1]["text"]
    assert state.agentWorkflow["activeWorkflow"] == "test_drive"
    assert state.dialogue.activeQuestion is not None
    assert state.dialogue.activeQuestion.goalIntent == "test_drive"


def test_provider_outage_is_reported_truthfully_and_remains_retryable(
    tmp_path: Path,
) -> None:
    class UnavailableProvider:

        async def generate_turn(self, messages):
            del messages
            raise ProviderUnavailableError("hosted planner transport unavailable")

    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=UnavailableProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        response = browser.post(
            f"/api/chat/v1/conversations/{created['conversationId']}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Book a test drive",
                "pageContext": CONTEXT,
            },
        )

    assert response.json()["status"] == "failed"
    assert response.json()["error"] == {
        "code": "LLM_PROVIDER_UNAVAILABLE",
        "message": "The AI service is temporarily unavailable. Please retry.",
        "retryable": True,
    }


def test_identical_failed_turn_retry_reuses_the_turn_without_duplicate_user_message(
    tmp_path: Path,
) -> None:
    class FailOnceProvider:

        def __init__(self):
            self.calls = 0

        async def generate_turn(self, messages):
            del messages
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("temporary provider timeout")
            return ProviderReply("Thanks — the retry worked.", response_mode="conversation")

    provider = FailOnceProvider()
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    message_id = str(uuid4())
    body = {
        "clientMessageId": message_id,
        "text": "Show me current offers",
        "pageContext": CONTEXT,
    }
    with TestClient(create_app(settings, provider=provider)) as browser:
        conversation = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()
        path = f"/api/chat/v1/conversations/{conversation['conversationId']}/turns"

        failed = browser.post(path, json=body)
        retried = browser.post(path, json=body)
        replayed = browser.post(path, json=body)
        restored = browser.get(
            f"/api/chat/v1/conversations/{conversation['conversationId']}"
        ).json()

    assert failed.json()["status"] == "failed"
    assert retried.json()["status"] == "completed"
    assert replayed.json() == retried.json()
    assert provider.calls == 2
    assert [message["role"] for message in restored["messages"]] == [
        "user",
        "assistant",
        "assistant",
    ]
    assert restored["messages"][-1]["text"] == "What else can I help you with?"


def test_daily_turn_limit_rejects_only_new_turns(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
        webchat_daily_turn_limit=1,
    )
    first_message_id = str(uuid4())
    with TestClient(create_app(settings, provider=FakeLlmProvider())) as browser:
        conversation = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()
        path = f"/api/chat/v1/conversations/{conversation['conversationId']}/turns"
        first = browser.post(
            path,
            json={
                "clientMessageId": first_message_id,
                "text": "Show me current offers",
                "pageContext": CONTEXT,
            },
        )
        repeated = browser.post(
            path,
            json={
                "clientMessageId": first_message_id,
                "text": "Show me current offers",
                "pageContext": CONTEXT,
            },
        )
        rejected = browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "Show me the vehicles",
                "pageContext": CONTEXT,
            },
        )

    assert first.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json()["turnId"] == first.json()["turnId"]
    assert rejected.status_code == 429
    assert rejected.json()["error"]["code"] == "DAILY_REVIEW_LIMIT_REACHED"


def test_accepting_pickup_contact_offer_shows_executable_contact_choices(
    tmp_path: Path,
) -> None:
    class BusinessGateway:
        async def get_business_information(self):
            return {
                "organisation": "Northstar Motors",
                "finance": {"notice": "Finance notice."},
                "partExchange": {"estimateNotice": "Estimate notice."},
                "privacyContact": "privacy@example.test",
            }

    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=FakeLlmProvider())) as browser:
        browser.app.state.orchestrator.tools = ApplicationToolExecutor(BusinessGateway())
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        conversation_id = created["conversationId"]

        pickup = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Will you pick up my car?",
                "pageContext": CONTEXT,
            },
        )
        accepted = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "yes",
                "pageContext": CONTEXT,
            },
        )
        repeated_acceptance = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "yes",
                "pageContext": CONTEXT,
            },
        )

    assert pickup.status_code == 200
    assert "confirmed Northstar information" in pickup.json()["messages"][1]["text"]
    for response in (accepted, repeated_acceptance):
        assert response.status_code == 200
        assistant = response.json()["messages"][1]
        assert assistant["viewType"] == "suggestion_list"
        assert [item["action"]["type"] for item in assistant["view"]["suggestions"]] == [
            "start_callback",
            "start_dealership_message",
            "show_dealerships",
            "show_opening_hours",
        ]


def test_dealership_message_chip_bypasses_planning_and_reuses_read_context(
    tmp_path: Path,
) -> None:
    class CompositionOnlyProvider:
        async def generate_turn(self, messages):
            reply = grounded_test_reply(messages, "Tell me what you would like to send.")
            assert reply is not None, "a structured action must not re-enter planning"
            return reply

    class CapturingTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            self.calls.append((name, arguments, conversation_id))
            payload = {
                "version": 1,
                "draftId": "draft-dealership-message",
                "kind": "dealership_message",
                "status": "collecting",
                "summary": dict(arguments),
                "missingFields": ["subject", "message", "preferredContactMethod"],
                "missingPublicFields": ["subject", "message", "preferredContactMethod"],
                "secureFields": ["firstName", "lastName", "email", "phone"],
                "secureInputReady": False,
            }
            return ToolResult(
                "I need a few more details before asking for confirmation.",
                "draft",
                payload,
                payload,
            )

    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=CompositionOnlyProvider())) as browser:
        conversation_id = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()["conversationId"]
        browser.app.state.conversations.update_workflow_state(
            conversation_id,
            workflow_state(
                "dealership_information",
                "viewing",
                entities={"dealershipId": "northstar-manchester"},
                constraints={"town": "Manchester", "department": "sales"},
            ),
        )
        tools = CapturingTools()
        browser.app.state.orchestrator.tools = tools

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "send a message to the dealership",
                "pageContext": CONTEXT,
                "action": {"type": "start_dealership_message"},
            },
        )
        state = browser.app.state.conversations.get_workflow_state(conversation_id)

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert tools.calls == [
        (
            "prepare_dealership_message",
            {
                "dealershipId": "northstar-manchester",
                "dealershipTown": "Manchester",
                "department": "sales",
            },
            conversation_id,
        )
    ]
    assert state["activeWorkflow"] == "dealership_message"
    assert state["entities"] == {"dealershipId": "northstar-manchester"}
    assert state["constraints"]["collectedPublicValues"] == {
        "dealershipId": "northstar-manchester",
        "dealershipTown": "Manchester",
        "department": "sales",
    }


def test_department_information_has_no_duplicate_dealership_card_and_has_next_steps(
    tmp_path: Path,
) -> None:
    class DepartmentProvider:
        async def generate_turn(self, messages):
            trusted = messages.planning_context.trusted_tool_facts
            if not trusted:
                return ProviderReply(
                    "",
                    tool_calls=direct_tool_calls(
                        "find_dealership_departments",
                        {"town": "Manchester", "department": "sales"},
                    ),
                )
            result = trusted["results"][0]
            department_facts = [
                fact for fact in result["facts"] if ".departments." in fact["field"]
            ]
            contact_references = [
                suggestion["reference"]
                for suggestion in result["availableSuggestions"]
                if dict(suggestion.get("action") or {}).get("type")
                in {"start_callback", "start_dealership_message"}
            ]
            segments = [TextSegment(type="text", text="The available departments are:")]
            for fact in department_facts:
                segments.extend(
                    [
                        BulletSegment(type="bullet"),
                        FactSegment(type="fact", factId=fact["factId"]),
                    ]
                )
            return ProviderReply(
                "",
                response_draft=GroundedResponseDraft(
                    messages=[
                        GroundedMessageDraft(purpose="answer", segments=segments),
                        GroundedMessageDraft(
                            purpose="follow_up",
                            segments=[
                                TextSegment(
                                    type="text",
                                    text="Would you like a callback or to leave a message?",
                                )
                            ],
                        ),
                    ],
                    suggestionReferences=contact_references,
                ),
            )

    class DepartmentGateway:
        async def list_dealerships(self):
            return {
                "items": [
                    {
                        "id": "northstar-manchester",
                        "name": "Northstar Manchester",
                        "town": "Manchester",
                    }
                ]
            }

        async def get_opening_hours(self, dealership_id):
            assert dealership_id == "northstar-manchester"
            return {
                "weekly": [
                    {"department": "parts"},
                    {"department": "sales"},
                    {"department": "service"},
                ]
            }

    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=DepartmentProvider())) as browser:
        browser.app.state.orchestrator.tools = ApplicationToolExecutor(DepartmentGateway())
        conversation_id = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()["conversationId"]
        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "What departments does the Manchester dealership have?",
                "pageContext": CONTEXT,
            },
        )
        state = browser.app.state.conversations.get_workflow_state(conversation_id)

    assert response.status_code == 200
    assistant = [
        message for message in response.json()["messages"] if message["role"] == "assistant"
    ]
    assert card_data(response.json(), "dealership") == []
    assert assistant[-1]["text"] == "Would you like a callback or to leave a message?"
    assert [item["action"]["type"] for item in assistant[-1]["view"]["quickReplies"]] == [
        "start_callback",
        "start_dealership_message",
    ]
    assert state["entities"] == {"dealershipId": "northstar-manchester"}
    assert state["constraints"] == {"town": "Manchester", "department": "sales"}


def test_part_exchange_pickup_callback_handoff_does_not_ask_for_a_department(
    tmp_path: Path,
) -> None:
    class HandoffProvider:
        async def generate_turn(self, messages):
            if messages.planning_context.trusted_tool_facts:
                grounded = grounded_test_reply(messages, "I can continue with that request.")
                assert grounded is not None
                return grounded
            latest = next(
                (
                    str(item.get("content") or "")
                    for item in reversed(messages)
                    if item.get("role") == "user"
                ),
                "",
            )
            if latest.strip().casefold() == "yes":
                return ProviderReply("", interaction_decision="accept")
            if "bolton" in latest.casefold():
                return ProviderReply(
                    "",
                    direct_tool_calls("prepare_callback", {"dealershipTown": "Bolton"}),
                )
            return ProviderReply(
                "",
                direct_tool_calls(
                    "get_business_information",
                    {
                        "topic": "part_exchange",
                        "question": "Will you pick up my car?",
                    },
                ),
            )

    class Gateway:
        async def get_business_information(self):
            return {
                "organisation": "Northstar Motors",
                "partExchange": {"estimateNotice": "Estimate notice."},
                "privacyContact": "privacy@example.test",
            }

        async def list_dealerships(self):
            return {
                "items": [
                    {
                        "id": "northstar-bolton",
                        "name": "Northstar Bolton",
                        "town": "Bolton",
                    },
                    {
                        "id": "northstar-stockport",
                        "name": "Northstar Stockport",
                        "town": "Stockport",
                    },
                ]
            }

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=HandoffProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        conversation_id = created["conversationId"]
        browser.app.state.conversations.update_workflow_state(
            conversation_id,
            workflow_state("part_exchange", "estimate_ready"),
        )
        browser.app.state.orchestrator.tools = ApplicationToolExecutor(
            Gateway(), browser.app.state.workflows
        )
        path = f"/api/chat/v1/conversations/{conversation_id}/turns"

        pickup = browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "Will you pick up my car?",
                "pageContext": CONTEXT,
            },
        )
        accepted = browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "yes",
                "pageContext": CONTEXT,
            },
        )
        callback = browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "please have a dealership call me",
                "pageContext": CONTEXT,
                "action": {"type": "start_callback"},
            },
        )
        callback_state = browser.app.state.conversations.get_workflow_state(conversation_id)
        bolton = browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "bolton",
                "pageContext": CONTEXT,
            },
        )

        state = browser.app.state.conversations.get_workflow_state(conversation_id)
        draft = browser.app.state.workflow_repository.latest_active(conversation_id, "callback")

    assert pickup.status_code == accepted.status_code == callback.status_code == 200
    callback_view = callback.json()["messages"][-1]["view"]
    assert callback_view["missingPublicFields"] == ["dealershipId"]
    assert callback_view["summary"]["department"] == "sales"
    assert callback_view["summary"]["reason"] == "Will you pick up my car?"
    assert callback_state.get("pausedWorkflow") is None, callback_state.get("pausedWorkflow")
    assert bolton.status_code == 200
    assert all(
        "which team" not in item["text"].casefold()
        for item in bolton.json()["messages"]
        if item["role"] == "assistant"
    )
    assert state["activeWorkflow"] == "callback"
    assert "pausedWorkflow" not in state, state
    assert state["constraints"]["missingPublicFields"] == []
    assert draft is not None


def test_pending_single_action_and_choice_are_provider_independent(tmp_path: Path) -> None:
    class PromptProvider:

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            if grounded := grounded_test_reply(messages, "I found current MOT appointments."):
                return grounded
            self.calls += 1
            if self.calls > 1:
                return ProviderReply("", interaction_decision="accept")
            return ProviderReply(
                "Would you like to see the supported workshop services?",
                interaction=single_action_interaction(
                    "Would you like to see the supported workshop services?",
                    {"type": "show_workshop_services"},
                ),
            )

    class WorkshopGateway:
        async def list_service_types(self):
            return {
                "items": [
                    {"id": "mot", "name": "MOT"},
                    {"id": "tyre-fitting", "name": "Tyre fitting"},
                ]
            }

    provider = PromptProvider()
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=provider)) as browser:
        browser.app.state.orchestrator.tools = ApplicationToolExecutor(WorkshopGateway())
        conversation = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()
        conversation_id = conversation["conversationId"]
        offered = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Can you help with servicing?",
                "pageContext": CONTEXT,
            },
        )
        accepted = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "yes please",
                "pageContext": CONTEXT,
            },
        )
        choice_not_selected = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "yes",
                "pageContext": CONTEXT,
            },
        )

    assert offered.status_code == 200
    assert accepted.json()["messages"][1]["viewType"] == "service_list"
    assert choice_not_selected.json()["messages"][1]["viewType"] == "service_list"
    assert choice_not_selected.json()["messages"][1]["text"] == (
        "Which available option would you like?"
    )
    assert provider.calls == 3


def test_exact_visible_workshop_choice_bypasses_planning_but_is_ai_composed(
    tmp_path: Path,
) -> None:
    class WorkshopProvider:

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            if grounded := grounded_test_reply(
                messages,
                "Which workshop service would you like to book?",
            ):
                return grounded
            self.calls += 1
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls(
                    "list_workshop_slots",
                    {"workflowMode": "booking"},
                ),
            )

    class WorkshopGateway:
        async def list_service_types(self):
            return {
                "items": [
                    {
                        "id": "full-service",
                        "name": "Full service",
                        "description": "A comprehensive annual service.",
                        "durationMinutes": 180,
                        "priceFromPence": 29900,
                    },
                    {"id": "mot", "name": "MOT"},
                ]
            }

        async def list_workshop_locations(self):
            return {
                "items": [
                    {
                        "id": "dealer-stockport",
                        "name": "Northstar Stockport",
                        "town": "Stockport",
                    }
                ]
            }

    provider = WorkshopProvider()
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=provider)) as browser:
        browser.app.state.orchestrator.tools = ApplicationToolExecutor(WorkshopGateway())
        conversation_id = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()["conversationId"]
        first = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "I want to book a workshop appointment",
                "pageContext": CONTEXT,
            },
        )
        selected = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "full service",
                "pageContext": CONTEXT,
                "action": {
                    "type": "select_workshop_service",
                    "serviceTypeId": "full-service",
                },
            },
        )
        state = browser.app.state.conversations.get_state(conversation_id)

    assert card_data(first.json(), "service") == []
    first_prompt = next(
        message for message in first.json()["messages"] if message.get("viewType") == "choice_list"
    )
    assert first_prompt["view"]["selectionOnly"] is True
    assert "Which workshop service" in first_prompt["text"]
    assert selected.json()["status"] == "completed"
    selected_assistant = [
        message for message in selected.json()["messages"] if message["role"] == "assistant"
    ]
    assert selected_assistant[-1]["viewType"] == "grounded_presentation"
    assert card_data(selected.json(), "dealership")
    assert state.agentWorkflow["stage"] == "choosing_dealership"
    assert state.agentWorkflow["entities"]["serviceTypeId"] == "full-service"
    assert provider.calls == 1


def test_pending_interaction_does_not_capture_a_new_explicit_request(tmp_path: Path) -> None:
    class PromptProvider:

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            if grounded := grounded_test_reply(messages, "Please review the callback request."):
                return grounded
            self.calls += 1
            if self.calls == 1:
                return ProviderReply(
                    "Would you like to see the supported workshop services?",
                    interaction=single_action_interaction(
                        "Would you like to see the supported workshop services?",
                        {"type": "show_workshop_services"},
                    ),
                )
            return ProviderReply("I heard your new request.")

    provider = PromptProvider()
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=provider)) as browser:
        conversation = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()
        conversation_id = conversation["conversationId"]
        for text in ("Can you help with servicing?", "show me current offers"):
            response = browser.post(
                f"/api/chat/v1/conversations/{conversation_id}/turns",
                json={
                    "clientMessageId": str(uuid4()),
                    "text": text,
                    "pageContext": CONTEXT,
                },
            )

    assert response.status_code == 200
    assert response.json()["messages"][1]["text"] == "I heard your new request."
    assert provider.calls == 2


def test_chat_yes_confirms_only_the_immediately_active_protected_draft(tmp_path: Path) -> None:
    class ConfirmationProvider:

        def __init__(self) -> None:
            self.calls = 0
            self.receipt_composition_calls = 0

        async def generate_turn(self, messages):
            trusted = (
                getattr(getattr(messages, "planning_context", None), "trusted_tool_facts", None)
                or {}
            )
            if any(
                card.get("type") == "receipt"
                for result in trusted.get("results", [])
                for card in result.get("availableCards", [])
            ):
                self.receipt_composition_calls += 1
                return grounded_test_reply(
                    messages,
                    "Please confirm the completed request again.",
                )
            if grounded := grounded_test_reply(messages, "Please review the callback request."):
                return grounded
            self.calls += 1
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls("prepare_callback"),
            )

    class ConfirmationExecutor:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, name, arguments, conversation_id):
            assert name == "prepare_callback"
            assert arguments == {}
            assert conversation_id
            self.calls += 1
            return ToolResult(
                "Review the callback request before submitting it.",
                "confirmation",
                {"version": 1, "kind": "callback", "draftId": "draft-1"},
                {"kind": "callback", "draftId": "draft-1"},
            )

    class ConfirmationWorkflows:
        def __init__(self) -> None:
            self.calls = []

        async def confirm(self, conversation_id, draft_id, client_action_id, expected_kind):
            self.calls.append((conversation_id, draft_id, client_action_id, expected_kind))
            return {"kind": "callback", "status": "confirmed", "reference": "CALL-001"}

    provider = ConfirmationProvider()
    executor = ConfirmationExecutor()
    workflows = ConfirmationWorkflows()
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=provider)) as browser:
        browser.app.state.orchestrator.tools = executor
        browser.app.state.orchestrator.workflows = workflows
        conversation = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()
        conversation_id = conversation["conversationId"]
        first = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Request a callback",
                "pageContext": CONTEXT,
            },
        )
        accepted_in_chat = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "yes",
                "pageContext": CONTEXT,
            },
        )

    assert first.status_code == 200
    response = accepted_in_chat.json()
    assert response["cards"][0]["type"] == "receipt"
    assert response["cards"][0]["data"]["reference"] == "CALL-001"
    assert response["pendingInteraction"] is None
    assert response["messages"][-1]["purpose"] == "follow_up"
    assert response["messages"][-1]["text"] == "What else can I help you with?"
    assert provider.receipt_composition_calls == 0
    assert provider.calls == 1
    assert executor.calls == 1
    assert workflows.calls[0][1] == "draft-1"
    assert UUID(workflows.calls[0][2])
    assert workflows.calls[0][3] == "callback"


def test_named_dealership_question_stays_a_card_with_semantic_provider(
    tmp_path: Path,
) -> None:
    class MisroutingProvider:

        def __init__(self) -> None:
            self.called = False

        async def generate_turn(self, messages):
            trusted = messages.planning_context.trusted_tool_facts
            if trusted:
                result = trusted["results"][0]
                return ProviderReply(
                    "",
                    response_draft=GroundedResponseDraft(
                        messages=[
                            GroundedMessageDraft(
                                purpose="answer",
                                segments=[
                                    TextSegment(
                                        type="text",
                                        text="Here is the Stockport dealership.",
                                    )
                                ],
                            )
                        ],
                        cardReferences=[result["availableCards"][0]["reference"]],
                    ),
                )
            self.called = True
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls("list_dealerships", {"town": "Stockport"}),
            )

    class DealershipTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "list_dealerships"
            assert arguments == {"town": "Stockport"}
            assert conversation_id
            item = {
                "id": "dealer-stockport",
                "name": "Northstar Stockport",
                "town": "Stockport",
                "addressLine": "24 Wellington Road",
                "postcode": "SK4 2BE",
                "phone": "0161 555 0124",
            }
            payload = {
                "version": 1,
                "items": [item],
                "suggestions": [
                    {
                        "label": "Opening hours",
                        "text": "show me the opening hours",
                        "action": {"type": "show_opening_hours"},
                    },
                    {
                        "label": "Departments",
                        "text": "what departments does the dealership have?",
                    },
                    {
                        "label": "Request a callback",
                        "text": "please have the dealership call me",
                        "action": {"type": "start_callback"},
                    },
                    {
                        "label": "Leave a message",
                        "text": "send a message to the dealership",
                        "action": {"type": "start_dealership_message"},
                    },
                ],
            }
            return ToolResult("Here are our dealerships.", "dealership_list", payload, payload)

    provider = MisroutingProvider()
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=provider)) as browser:
        browser.app.state.orchestrator.tools = DealershipTools()
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        response = browser.post(
            f"/api/chat/v1/conversations/{created['conversationId']}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Where is the Stockport dealership?",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assert card_data(response.json(), "dealership")[0]["items"][0]["name"] == (
        "Northstar Stockport"
    )
    assistant = [
        message for message in response.json()["messages"] if message["role"] == "assistant"
    ]
    assert assistant[-1]["purpose"] == "follow_up"
    assert assistant[-1]["text"] == "Which of these options would you like help with next?"
    assert [reply["label"] for reply in assistant[-1]["view"]["quickReplies"]] == [
        "Opening hours",
        "Departments",
        "Request a callback",
        "Leave a message",
    ]
    assert provider.called is True


def test_dealership_fallback_preserves_the_global_follow_up_contract(
    tmp_path: Path,
) -> None:
    class CompositionFailureProvider:

        async def generate_turn(self, messages):
            if messages.planning_context.trusted_tool_facts:
                raise RuntimeError("composition unavailable")
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls("list_dealerships", {"town": "Stockport"}),
            )

    class DealershipGateway:
        async def list_dealerships(self):
            return {
                "items": [
                    {
                        "id": "northstar-stockport",
                        "name": "Northstar Stockport",
                        "town": "Stockport",
                        "addressLine": "24 Wellington Road",
                        "postcode": "SK4 2BE",
                        "phone": "0161 555 0124",
                    }
                ]
            }

    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=CompositionFailureProvider())) as browser:
        browser.app.state.orchestrator.tools = ApplicationToolExecutor(DealershipGateway())
        conversation_id = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()["conversationId"]
        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Where is the Stockport dealership?",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assistant = [
        message for message in response.json()["messages"] if message["role"] == "assistant"
    ]
    assert card_data(response.json(), "dealership")[0]["items"][0]["name"] == (
        "Northstar Stockport"
    )
    assert assistant[-1]["purpose"] == "follow_up"
    assert assistant[-1]["text"] == "Which of these options would you like help with next?"
    assert [reply["label"] for reply in assistant[-1]["view"]["quickReplies"]] == [
        "Opening hours",
        "Departments",
        "Request a callback",
        "Leave a message",
    ]


def test_generic_follow_up_opens_unfilled_message_form_at_trusted_location(
    tmp_path: Path,
) -> None:
    class DealershipMessageProvider:

        async def generate_turn(self, messages):
            if grounded := grounded_test_reply(messages):
                return grounded
            text = messages.planning_context.latest_customer_message.casefold()
            call = (
                ToolCall("location", "list_dealerships", {"town": "Stockport"})
                if "where" in text
                else ToolCall("message", "prepare_dealership_message", {})
            )
            return ProviderReply(
                "",
                [call],
            )

    class DealershipGateway:
        async def list_dealerships(self):
            return {
                "items": [
                    {
                        "id": "northstar-stockport",
                        "name": "Northstar Stockport",
                        "town": "Stockport",
                        "addressLine": "24 Wellington Road",
                        "postcode": "SK4 2BE",
                        "phone": "0161 555 0124",
                    }
                ]
            }

        async def get_business_information(self):
            return {"privacyContact": "privacy@northstarmotors.example"}

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=DealershipMessageProvider())) as browser:
        gateway = DealershipGateway()
        browser.app.state.orchestrator.tools = ApplicationToolExecutor(
            gateway, browser.app.state.workflows
        )
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]

        for customer_text in (
            "Where is the Stockport dealership?",
            "Send a message to the dealership",
        ):
            response = browser.post(
                f"/api/chat/v1/conversations/{conversation_id}/turns",
                json={
                    "clientMessageId": str(uuid4()),
                    "text": customer_text,
                    "pageContext": CONTEXT,
                },
            )
            assert response.status_code == 200
        state = browser.app.state.conversations.get_state(conversation_id)

    assert response.json()["workflow"] is None
    assert state.agentWorkflow["activeWorkflow"] == "dealership_message"
    assert state.agentWorkflow["stage"] == "collecting"
    assert state.agentWorkflow["constraints"]["collectedPublicValues"] == {
        "dealershipId": "northstar-stockport",
    }
    assistant = [item for item in response.json()["messages"] if item["role"] == "assistant"]
    assert assistant[-1]["viewType"] == "choice_list"
    assert assistant[-1]["view"]["choiceField"] == "department"
    assert [item["id"] for item in assistant[-1]["view"]["items"]] == [
        "sales",
        "service",
        "parts",
        "general",
    ]


def test_pch_definition_reaches_online_semantic_provider_before_safety_gate(
    tmp_path: Path,
) -> None:
    class DefinitionProvider:

        def __init__(self) -> None:
            self.called = False

        async def generate_turn(self, messages):
            del messages
            self.called = True
            return ProviderReply(
                (
                    "PCH means Personal Contract Hire. It is a long-term vehicle "
                    "rental, and you return the vehicle at the end of the agreement."
                ),
                response_mode="answer",
                citation_ids=("customer.pch",),
            )

    class NoTools:
        async def execute(self, name, arguments, conversation_id):
            raise AssertionError(
                f"Definition unexpectedly called {name} with {arguments} for {conversation_id}"
            )

    provider = DefinitionProvider()
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=provider)) as browser:
        browser.app.state.orchestrator.tools = NoTools()
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        response = browser.post(
            f"/api/chat/v1/conversations/{created['conversationId']}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "what is pch",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assert provider.called is True
    assistant = response.json()["messages"][1]
    assert "Personal Contract Hire" in assistant["text"]
    assert assistant.get("viewType") is None


@pytest.mark.parametrize(
    "wording",
    [
        "will you pick my car?",
        "will you pick up the car?",
        "will you pick up my car?",
        "will you pick up car",
        "Can Northstar collect my car?",
        "Can you collect the vehicle from my home?",
        "Do you offer vehicle collection?",
        "Can you deliver or collect my car?",
        "Will someone come and collect it?",
    ],
)
def test_misclassified_collection_question_never_renders_vehicle_or_notice_cards(
    tmp_path: Path,
    wording: str,
) -> None:
    class SemanticProvider:

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            if grounded := grounded_test_reply(messages):
                return grounded
            self.calls += 1
            if self.calls == 1:
                return ProviderReply(
                    "",
                    tool_calls=direct_tool_calls("request_part_exchange_estimate_form"),
                )
            return ProviderReply(
                (
                    "I don't have confirmed Northstar information that answers that "
                    "question. Would you like me to help you contact a dealership?"
                ),
                response_mode="clarify",
            )

    class BusinessGateway:
        async def get_business_information(self):
            return {
                "organisation": "Northstar Motors",
                "finance": {"notice": "Finance notice."},
                "partExchange": {"estimateNotice": "Estimate notice."},
                "privacyContact": "privacy@example.test",
            }

    provider = SemanticProvider()
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=provider)) as browser:
        browser.app.state.orchestrator.tools = ApplicationToolExecutor(BusinessGateway())
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        conversation_id = created["conversationId"]
        estimate = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "I want an indicative part-exchange estimate",
                "pageContext": CONTEXT,
            },
        )
        pickup = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": wording,
                "pageContext": CONTEXT,
            },
        )

    assert estimate.status_code == 200
    assert estimate.json()["workflow"]["kind"] == "part_exchange_estimate"
    assert pickup.status_code == 200
    assistant = pickup.json()["messages"][1]
    assert assistant["text"].startswith("I don't have confirmed Northstar information")
    assert assistant.get("viewType") is None
    assert "Finance notice" not in assistant["text"]
    assert "Estimate notice" not in assistant["text"]
    assert "privacy@example.test" not in assistant["text"]
    assert provider.calls == 2


@pytest.mark.parametrize(
    "wording",
    [
        "will you pick up the car?",
        "will you pick up my car?",
        "will you pick up car",
    ],
)
def test_reviewed_clarification_cannot_bypass_semantic_execution_boundary(
    tmp_path: Path,
    wording: str,
) -> None:
    class ProseProvider:

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            if grounded := grounded_test_reply(messages):
                return grounded
            self.calls += 1
            if self.calls == 1:
                return ProviderReply(
                    "",
                    tool_calls=direct_tool_calls("request_part_exchange_estimate_form"),
                )
            return ProviderReply(
                (
                    "I don't have confirmed Northstar information that answers that "
                    "question. Would you like me to help you contact a dealership?"
                ),
                response_mode="clarify",
            )

    class BusinessGateway:
        async def get_business_information(self):
            return {
                "organisation": "Northstar Motors",
                "finance": {"notice": "Finance notice."},
                "partExchange": {"estimateNotice": "Estimate notice."},
                "privacyContact": "privacy@example.test",
            }

    provider = ProseProvider()
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=provider)) as browser:
        browser.app.state.orchestrator.tools = ApplicationToolExecutor(BusinessGateway())
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        conversation_id = created["conversationId"]
        browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "I want an indicative part-exchange estimate",
                "pageContext": CONTEXT,
            },
        )
        pickup = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": wording,
                "pageContext": CONTEXT,
            },
        )

    assert pickup.status_code == 200
    assistant = pickup.json()["messages"][1]
    assert assistant["text"].startswith("I don't have confirmed Northstar information")
    assert assistant.get("viewType") is None
    assert "collect your car" not in assistant["text"]
    assert provider.calls == 2


def test_explicit_pch_offer_request_stays_on_live_offer_cards(tmp_path: Path) -> None:
    class MisroutingProvider:

        def __init__(self) -> None:
            self.called = False

        async def generate_turn(self, messages):
            if grounded := grounded_test_reply(messages, "Here are the current PCH offers."):
                return grounded
            self.called = True
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls("list_offers", {"productType": "PCH"}),
            )

    class OfferTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            assert conversation_id
            self.calls.append((name, arguments))
            payload = {
                "version": 1,
                "items": [{"id": "offer-001", "productType": "PCH"}],
                "suggestions": [],
            }
            return ToolResult("Found 1 result.", "offer_list", payload, payload)

    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    provider = MisroutingProvider()
    with TestClient(create_app(settings, provider=provider)) as browser:
        tools = OfferTools()
        browser.app.state.orchestrator.tools = tools
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        response = browser.post(
            f"/api/chat/v1/conversations/{created['conversationId']}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "show me current PCH offers",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assert provider.called is True
    assert tools.calls == [("list_offers", {"productType": "PCH"})]
    assert card_data(response.json(), "offer")[0]["items"][0]["productType"] == "PCH"


def test_conversation_response_vehicle_search_is_replaced_by_application_route(
    tmp_path: Path,
) -> None:
    class ProseProvider:

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            trusted = messages.planning_context.trusted_tool_facts
            if trusted:
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
                                segments=[
                                    TextSegment(type="text", text="I found a matching vehicle.")
                                ],
                            ),
                            GroundedMessageDraft(
                                purpose="follow_up",
                                segments=[
                                    TextSegment(
                                        type="text", text="Which type of car interests you?"
                                    )
                                ],
                            ),
                        ],
                        cardReferences=cards,
                    ),
                )
            self.calls += 1
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls(
                    "search_vehicles",
                    {"sort": "priceAsc", "maxPricePence": 3_000_000},
                ),
            )

    class VehicleTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            self.calls.append((name, arguments))
            assert conversation_id
            assert name == "search_vehicles"
            payload = {
                "version": 1,
                "items": [{"id": "veh-001", "make": "Volvo", "model": "XC40"}],
                "suggestions": [],
            }
            return ToolResult("I found 1 vehicle.", "vehicle_list", payload, payload)

    provider = ProseProvider()
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=provider)) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        tools = VehicleTools()
        browser.app.state.orchestrator.tools = tools
        response = browser.post(
            f"/api/chat/v1/conversations/{created['conversationId']}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "show me cars under £30,000",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assert provider.calls == 1
    assert tools.calls == [("search_vehicles", {"sort": "priceAsc", "maxPricePence": 3_000_000})]
    assert card_data(response.json(), "vehicle_preview")[0]["items"][0]["id"] == "veh-001"
    assistant = response.json()["messages"][1:]
    assert assistant[0]["purpose"] == "answer"
    assert assistant[0]["viewType"] == "grounded_presentation"
    assert assistant[0]["view"]["cards"][0]["type"] == "vehicle_preview"
    assert assistant[1]["purpose"] == "follow_up"
    assert assistant[1]["view"] is None


def test_conversation_response_after_facets_still_reaches_vehicle_cards(
    tmp_path: Path,
) -> None:
    class ProseProvider:

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            if grounded := grounded_test_reply(messages, "I found a matching vehicle."):
                return grounded
            self.calls += 1
            if self.calls == 1:
                return ProviderReply(
                    "",
                    tool_calls=direct_tool_calls(
                        "get_vehicle_facets",
                    ),
                )
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls(
                    "search_vehicles",
                    {
                        "sort": "priceAsc",
                        "make": "Volvo",
                        "transmission": "Automatic",
                    },
                ),
            )

    class FacetTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            self.calls.append((name, arguments))
            assert conversation_id
            if name == "get_vehicle_facets":
                return ToolResult(
                    "Loaded options.",
                    None,
                    None,
                    {"makes": ["Volvo"], "transmissions": ["Automatic"]},
                )
            assert name == "search_vehicles"
            payload = {
                "version": 1,
                "items": [{"id": "veh-001", "make": "Volvo", "model": "XC40"}],
                "suggestions": [],
            }
            return ToolResult("I found 1 vehicle.", "vehicle_list", payload, payload)

    provider = ProseProvider()
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=provider)) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        tools = FacetTools()
        browser.app.state.orchestrator.tools = tools
        response = browser.post(
            f"/api/chat/v1/conversations/{created['conversationId']}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "show me automatic Volvos",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assert provider.calls == 2
    assert tools.calls == [
        ("get_vehicle_facets", {}),
        (
            "search_vehicles",
            {"sort": "priceAsc", "make": "Volvo", "transmission": "Automatic"},
        ),
    ]
    assert card_data(response.json(), "vehicle_preview")[0]["items"][0]["id"] == "veh-001"


def test_independent_review_correction_reaches_workshop_cards(tmp_path: Path) -> None:
    class ReplanningProvider:

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            if grounded := grounded_test_reply(messages, "I found current MOT appointments."):
                return grounded
            self.calls += 1
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls("list_workshop_slots", {"serviceTypeName": "MOT"}),
            )

    class WorkshopTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            self.calls.append((name, arguments))
            assert conversation_id
            payload = {"version": 1, "items": [{"id": "ws-slot-0001"}]}
            return ToolResult("I found 1 appointment.", "slot_list", payload, payload)

    provider = ReplanningProvider()
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=provider)) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        tools = WorkshopTools()
        browser.app.state.orchestrator.tools = tools
        response = browser.post(
            f"/api/chat/v1/conversations/{created['conversationId']}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "I want to book an MOT appointment",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assert provider.calls == 1
    assert tools.calls == [("list_workshop_slots", {"serviceTypeName": "MOT"})]
    assert response.json()["workflow"] is None


def test_independent_review_can_stop_execution_with_a_safe_clarification(
    tmp_path: Path,
) -> None:
    class RepeatingProvider:

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            del messages
            self.calls += 1
            return ProviderReply(
                "Which workshop service would you like to book?",
                response_mode="clarify",
            )

    class NoTools:
        async def execute(self, name, arguments, conversation_id):
            raise AssertionError((name, arguments, conversation_id))

    provider = RepeatingProvider()
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=provider)) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        browser.app.state.orchestrator.tools = NoTools()
        response = browser.post(
            f"/api/chat/v1/conversations/{created['conversationId']}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "I want to book an MOT appointment",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assert provider.calls == 1
    assert response.json()["messages"][1]["text"] == (
        "Which workshop service would you like to book?"
    )


def test_finite_llm_clarification_options_render_restore_and_submit_as_plain_replies(
    tmp_path: Path,
) -> None:
    options = (
        "BMW 3 Series 320d M Sport",
        "BMW 1 Series 118i M Sport",
    )

    class ClarificationProvider:

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            self.calls += 1
            if self.calls == 1:
                return ProviderReply(
                    "Which BMW would you like to compare with the MINI Countryman?",
                    response_mode="clarify",
                    interaction=input_interaction(
                        "Which BMW would you like to compare with the MINI Countryman?",
                        "compare_vehicles",
                        ["vehicleIds"],
                        [],
                    ),
                    suggestions=options,
                )
            assert messages.planning_context.latest_customer_message == options[0]
            assert messages.planning_context.pending_interaction is not None
            assert messages.planning_context.pending_interaction["kind"] == "input"
            return ProviderReply("I’ll compare those vehicles now.", response_mode="conversation")

    provider = ClarificationProvider()
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=provider)) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        conversation_id = created["conversationId"]
        clarified = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Compare the MINI with a BMW",
                "pageContext": CONTEXT,
            },
        )
        restored = browser.get(f"/api/chat/v1/conversations/{conversation_id}")
        selected = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": options[0],
                "pageContext": CONTEXT,
            },
        )

    assistant = clarified.json()["messages"][1]
    assert assistant["viewType"] == "suggestion_list"
    assert assistant["view"]["completeChoiceSet"] is True
    assert assistant["view"]["suggestions"] == [
        {"label": option, "text": option} for option in options
    ]
    restored_assistant = restored.json()["messages"][1]
    assert restored_assistant["view"] == assistant["view"]
    assert selected.json()["messages"][1]["text"] == "I’ll compare those vehicles now."
    assert provider.calls == 2


def test_conversation_response_still_handles_genuine_conversation(tmp_path: Path) -> None:
    class GreetingProvider:

        async def generate_turn(self, messages):
            del messages
            return ProviderReply(
                "You're welcome.",
                response_mode="conversation",
            )

    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=GreetingProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        response = browser.post(
            f"/api/chat/v1/conversations/{created['conversationId']}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "thanks",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assert response.json()["messages"][1]["text"] == "You're welcome."
    assert response.json()["messages"][2]["purpose"] == "follow_up"
    assert response.json()["messages"][2]["text"] == "What else can I help you with?"


def test_explicit_farewell_remains_terminal(tmp_path: Path) -> None:
    class FarewellProvider:

        async def generate_turn(self, messages):
            del messages
            return ProviderReply(
                "Goodbye. Thanks for contacting Northstar Motors.",
                response_mode="conversation",
            )

    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=FarewellProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        response = browser.post(
            f"/api/chat/v1/conversations/{created['conversationId']}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "goodbye",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assistant = [
        message for message in response.json()["messages"] if message["role"] == "assistant"
    ]
    assert [message["text"] for message in assistant] == [
        "Goodbye. Thanks for contacting Northstar Motors."
    ]


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


def test_cancelled_workshop_change_replaces_review_with_persisted_receipt(
    tmp_path: Path,
) -> None:
    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        conversation_id = created["conversationId"]
        draft = browser.app.state.workflow_repository.create_or_replace(
            conversation_id,
            "workshop_amend",
            {"verifiedGrantId": "grant-1", "mileage": 12_000},
            "workshop-amendment",
            "awaiting_confirmation",
        )
        browser.app.state.messages.add(
            conversation_id,
            "assistant",
            "Please review the details below.",
            None,
            "confirmation",
            ('{"kind":"workshop_amend","draftId":"' + draft["id"] + '"}'),
        )

        cancelled = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/drafts/{draft['id']}/cancel",
            json={},
        )
        restored = browser.get(f"/api/chat/v1/conversations/{conversation_id}").json()

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "unchanged"
    assert cancelled.json()["result"] == {
        "kind": "workshop_change_abandoned",
    }
    assert len(restored["messages"]) == 1
    assert restored["messages"][0]["viewType"] == "receipt"
    assert restored["messages"][0]["text"] == ("Your current workshop booking has been kept.")


def test_cancelled_workshop_change_restores_verified_booking_receipt(
    tmp_path: Path,
) -> None:
    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        conversation_id = created["conversationId"]
        browser.app.state.workflow_repository.create_grant(
            conversation_id,
            "wsb-001",
            "WORK-RESTORED",
            {
                "reference": "WORK-RESTORED",
                "startsAt": "2026-08-29T09:00:00Z",
                "dealershipName": "Northstar Stockport",
                "serviceTypeName": "Diagnostic inspection",
                "status": "confirmed",
            },
        )
        draft = browser.app.state.workflow_repository.create_or_replace(
            conversation_id,
            "workshop_amend",
            {"mileage": 12_000},
            "workshop-amendment-with-booking",
            "awaiting_confirmation",
        )
        browser.app.state.messages.add(
            conversation_id,
            "assistant",
            "Please review the details below.",
            None,
            "confirmation",
            ('{"kind":"workshop_amend","draftId":"' + draft["id"] + '"}'),
        )

        cancelled = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/drafts/{draft['id']}/cancel",
            json={},
        )
        restored = browser.get(f"/api/chat/v1/conversations/{conversation_id}").json()

    expected = {
        "kind": "workshop_booking",
        "status": "confirmed",
        "reference": "WORK-RESTORED",
        "startsAt": "2026-08-29T09:00:00Z",
        "dealershipName": "Northstar Stockport",
        "serviceTypeName": "Diagnostic inspection",
    }
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "unchanged"
    assert cancelled.json()["result"] == expected
    assert restored["messages"][0]["view"] == expected
    assert restored["messages"][0]["text"] == "Your current workshop booking has been kept."


def test_cancelled_sales_enquiry_form_keeps_specific_receipt_on_restore(
    tmp_path: Path,
) -> None:
    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        conversation_id = created["conversationId"]
        draft = browser.app.state.workflow_repository.create_or_replace(
            conversation_id,
            "sales_enquiry",
            {"dealershipId": "northstar-stockport"},
            "sales-enquiry-form",
            "collecting",
        )
        browser.app.state.messages.add(
            conversation_id,
            "assistant",
            "Please complete the form below.",
            None,
            "draft",
            ('{"kind":"sales_enquiry","draftId":"' + draft["id"] + '"}'),
        )

        cancelled = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/drafts/{draft['id']}/cancel",
            json={},
        )
        restored = browser.get(f"/api/chat/v1/conversations/{conversation_id}").json()

    assert cancelled.status_code == 200
    assert cancelled.json()["result"] == {
        "kind": "request_cancelled",
        "requestKind": "sales_enquiry",
        "status": "cancelled",
    }
    assert len(restored["messages"]) == 1
    assert restored["messages"][0]["viewType"] == "receipt"
    assert restored["messages"][0]["text"] == "Your sales enquiry has been cancelled."
    assert restored["messages"][0]["view"]["requestKind"] == "sales_enquiry"


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
        assert len(first.json()["messages"]) == 3
        assert first.json()["messages"][-1]["purpose"] == "follow_up"

        restored = browser.get(f"/api/chat/v1/conversations/{conversation_id}")
        assert [message["role"] for message in restored.json()["messages"]] == [
            "user",
            "assistant",
            "assistant",
        ]
        assert restored.json()["messages"][-1]["purpose"] == "follow_up"

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
        assert (
            browser.get(f"/api/chat/v1/conversations/{first['conversationId']}").status_code == 200
        )


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
        "pageText": "2026 Volvo XC40 costs £31,995 and is available.",
        "dialogText": "Volvo XC40 vehicle details",
        "entities": [
            {
                "type": "vehicle",
                "id": "veh-019",
                "label": "Volvo XC40",
                "attributes": {"pricePence": 3_199_500, "availability": "Available"},
            }
        ],
    }
    with TestClient(create_app(settings, provider=provider)) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": starting}).json()
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
        message["content"] for message in provider.messages if message.get("role") == "developer"
    ]
    assert any(
        "Current page context" in value and "veh-019" in value for value in developer_content
    )
    assert any(
        "Page where this conversation started" in value and "Current offers" in value
        for value in developer_content
    )
    assert any("Page-context contract" in value for value in developer_content)
    assert all("£31,995" not in value for value in developer_content)
    assert all("pricePence" not in value for value in developer_content)
    assert provider.messages.planning_context.page_vehicles == [
        {
            "position": 1,
            "vehicleId": "veh-019",
            "label": "Used Cars",
        }
    ]
    assert provider.messages.planning_context.page["entities"] == [
        {"type": "vehicle", "id": "veh-019", "label": "Volvo XC40"}
    ]


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
    assert card_data(response.json(), "dealership")[0]["items"][0]["name"] == (
        "Northstar Stockport"
    )


def test_explicit_information_request_redisplays_an_unchanged_trusted_card(
    tmp_path: Path,
) -> None:
    class ExplicitResultProvider:

        async def generate_turn(self, messages):
            trusted = messages.planning_context.trusted_tool_facts
            if trusted:
                result = trusted["results"][0]
                facts = {fact["field"]: fact["factId"] for fact in result["facts"]}
                return ProviderReply(
                    "",
                    response_draft=GroundedResponseDraft(
                        messages=[
                            GroundedMessageDraft(
                                purpose="answer",
                                blocks=[
                                    ParagraphBlock(
                                        type="paragraph",
                                        segments=[
                                            TextSegment(
                                                type="text",
                                                text="Here are the contact details.",
                                            )
                                        ],
                                    ),
                                    ParagraphBlock(
                                        type="paragraph",
                                        segments=[
                                            TextSegment(type="text", text="Address: "),
                                            FactSegment(
                                                type="fact",
                                                factId=facts["items.1.addressLine"],
                                            ),
                                        ],
                                    ),
                                    ParagraphBlock(
                                        type="paragraph",
                                        segments=[
                                            TextSegment(type="text", text="Phone: "),
                                            FactSegment(type="fact", factId=facts["items.1.phone"]),
                                            TextSegment(type="text", text=". Email: "),
                                            FactSegment(type="fact", factId=facts["items.1.email"]),
                                        ],
                                    ),
                                ],
                            )
                        ],
                        cardReferences=[result["availableCards"][0]["reference"]],
                    ),
                )
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls("list_dealerships", {"town": "Stockport"}),
                turn_understanding=TurnUnderstanding(
                    dialogueAct="continue_goal",
                    goalRelation="active",
                    intentKinds=[],
                    resultPresentation="explicit_request",
                    ambiguity="none",
                    confidence="high",
                ),
            )

    class DealershipTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "list_dealerships"
            assert arguments == {"town": "Stockport"}
            assert conversation_id
            item = {
                "id": "dealer-stockport",
                "name": "Northstar Stockport",
                "town": "Stockport",
                "addressLine": "24 Wellington Road",
                "postcode": "SK4 2BE",
                "phone": "0161 555 0124",
                "email": "stockport@northstarmotors.example",
            }
            payload = {"version": 1, "items": [item], "suggestions": []}
            return ToolResult("Here is the dealership.", "dealership_list", payload, payload)

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=ExplicitResultProvider())) as browser:
        browser.app.state.orchestrator.tools = DealershipTools()
        conversation_id = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()["conversationId"]
        responses = []
        for text in (
            "Where is the Stockport dealership?",
            "What are its contact details?",
        ):
            responses.append(
                browser.post(
                    f"/api/chat/v1/conversations/{conversation_id}/turns",
                    json={
                        "clientMessageId": str(uuid4()),
                        "text": text,
                        "pageContext": CONTEXT,
                    },
                ).json()
            )

    assert [len(card_data(response, "dealership")) for response in responses] == [1, 1]
    assert "optionNumber" not in card_data(responses[1], "dealership")[0]["items"][0]
    assert responses[1]["messages"][1]["text"] == "Here are the contact details."
    assert len(responses[1]["messages"][1]["blocks"]) == 1


def test_existing_workshop_booking_request_always_returns_private_lookup_form(
    tmp_path: Path,
) -> None:
    class FakeTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "request_workshop_booking_lookup_form"
            assert arguments == {"mode": "amend"}
            assert conversation_id
            payload = {
                "version": 1,
                "kind": "booking_lookup",
                "mode": "amend",
                "secureFields": ["reference", "lastName", "registration", "phone"],
                "secureInputReady": True,
            }
            return ToolResult(
                "Enter booking details privately.", "private_booking_lookup", payload, payload
            )

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
    assert response.json()["workflow"]["kind"] == "booking_lookup"
    collector = next(
        message
        for message in response.json()["messages"]
        if message.get("viewType") == "secure_input"
    )
    assert collector["view"] == {
        "version": 1,
        "sourceViewType": "private_booking_lookup",
        "kind": "booking_lookup",
        "mode": "amend",
        "secureFields": ["reference", "lastName", "registration", "phone"],
        "secureInputReady": True,
    }


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
    if mode == "amend":
        assert response.json()["viewType"] == "workshop_booking_details"
        assert response.json()["view"]["reference"] == "WORK-10001"
        assert "What would you like to change?" in response.json()["text"]
    else:
        assert response.json()["viewType"] == view_type
        assert response.json()["view"]["kind"] == kind


@pytest.mark.parametrize(
    ("status", "labels", "question"),
    [
        (
            "confirmed",
            ["Edit booking", "Cancel booking"],
            "Would you like to edit or cancel this booking?",
        ),
        (
            "cancelled",
            ["Book an appointment", "Find another booking"],
            "Would you like to book another appointment or find another booking?",
        ),
    ],
)
def test_verified_booking_lookup_returns_status_specific_next_steps(
    tmp_path: Path, status: str, labels: list[str], question: str
) -> None:
    class FakeWorkflows:
        async def lookup_booking(self, conversation_id, proof):
            return {
                "booking": {
                    "reference": "WORK-10001",
                    "startsAt": "2026-08-24T09:00:00Z",
                    "dealershipName": "Northstar Manchester",
                    "serviceTypeName": "MOT",
                    "status": status,
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
        state = browser.app.state.conversations.get_workflow_state(conversation_id)
        restored = browser.get(
            f"/api/chat/v1/conversations/{conversation_id}"
        ).json()["messages"][-1]

    assert response.status_code == 200
    payload = response.json()
    assert payload["viewType"] == "grounded_presentation"
    assert payload["text"].endswith(question)
    assert payload["view"]["cards"] == [
        {
            "reference": "card:verified-workshop-booking:details",
            "type": "booking",
            "data": {
                "version": 1,
                "reference": "WORK-10001",
                "startsAt": "2026-08-24T09:00:00Z",
                "dealershipName": "Northstar Manchester",
                "serviceTypeName": "MOT",
                "status": status,
            },
        }
    ]
    assert [item["label"] for item in payload["view"]["quickReplies"]] == labels
    assert state["constraints"]["bookingStatus"] == status
    assert restored["viewType"] == "grounded_presentation"
    assert [item["label"] for item in restored["view"]["quickReplies"]] == labels


def test_facet_read_still_returns_interactive_choice_chips(tmp_path: Path) -> None:
    class FacetProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            if grounded := grounded_test_reply(messages, "Choose a fuel type."):
                return grounded
            self.calls += 1
            if self.calls == 1:
                return ProviderReply(
                    "",
                    [ToolCall("load-fuel-facets", "get_vehicle_facets", {})],
                )
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls(
                    "show_vehicle_preferences",
                    {"dimension": "fuelTypes"},
                ),
            )

    class FakeTools:
        async def execute(self, name, arguments, conversation_id):
            assert conversation_id
            if name == "show_vehicle_preferences":
                assert arguments == {"dimension": "fuelTypes"}
                suggestions = [
                    {
                        "label": value,
                        "text": value,
                        "action": {
                            "type": "apply_vehicle_preference",
                            "vehicleFilter": "fuelType",
                            "vehicleFilterValue": value,
                        },
                    }
                    for value in ("Diesel", "Electric")
                ]
                return ToolResult(
                    "Choose a fuel type below.",
                    "suggestion_list",
                    {"version": 1, "suggestions": suggestions},
                    {"dimension": "fuelTypes"},
                )
            assert name == "get_vehicle_facets"
            assert arguments == {}
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
    assert [item["label"] for item in response.json()["quickReplies"]] == [
        "Diesel",
        "Electric",
    ]


def test_service_text_reaches_provider_and_asks_for_a_time_preference(
    tmp_path: Path,
) -> None:
    class BookingProvider:
        def __init__(self) -> None:
            self.called = False

        async def generate_turn(self, messages):
            if grounded := grounded_test_reply(
                messages,
                "What day or approximate time would suit you for the brake inspection?",
            ):
                return grounded
            self.called = True
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls(
                    "list_workshop_slots",
                    {"serviceTypeName": "Brake inspection"},
                ),
            )

    class FakeTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "list_workshop_slots"
            assert arguments == {"serviceTypeName": "Brake inspection"}
            assert conversation_id
            payload = {"version": 1, "items": [{"id": "ws-slot-0001"}]}
            return ToolResult("Found 1 result.", "slot_list", payload, payload)

    provider = BookingProvider()
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=provider)) as browser:
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
        state = browser.app.state.conversations.get_state(conversation_id)

    assert response.status_code == 200
    assert provider.called is True
    assert response.json()["workflow"] is None
    assert state.agentWorkflow["activeWorkflow"] == "workshop_booking"
    assert state.agentWorkflow["stage"] == "choosing_time"
    assistant = [item for item in response.json()["messages"] if item["role"] == "assistant"]
    assert [item["text"] for item in assistant] == [
        (
            "What day or approximate time would suit you for the brake inspection?\n"
            "- A preferred day or date\n"
            "- An approximate time"
        )
    ]
    assert all(item.get("viewType") != "slot_list" for item in assistant)


def test_application_collector_suppresses_competing_model_workflow_questions(
    tmp_path: Path,
) -> None:
    class CompetingPromptProvider:
        async def generate_turn(self, messages):
            if messages.planning_context.trusted_tool_facts:
                assert messages.planning_context.trusted_tool_facts["secureInputActivation"] is True
                return ProviderReply(
                    "",
                    response_draft=GroundedResponseDraft(
                        messages=[
                            GroundedMessageDraft(
                                purpose="workflow_prompt",
                                segments=[
                                    TextSegment(
                                        type="text",
                                        text="Please send these vehicle details together:",
                                    ),
                                    BulletSegment(type="bullet"),
                                    TextSegment(type="text", text="Registration"),
                                    BulletSegment(type="bullet"),
                                    TextSegment(type="text", text="Current mileage"),
                                    BulletSegment(type="bullet"),
                                    TextSegment(type="text", text="Condition"),
                                ],
                            ),
                        ]
                    ),
                )
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls("request_part_exchange_estimate_form"),
            )

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=CompetingPromptProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        response = browser.post(
            f"/api/chat/v1/conversations/{created.json()['conversationId']}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "I’d like a part-exchange estimate",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assistant = [item for item in response.json()["messages"] if item["role"] == "assistant"]
    assert len(assistant) == 1
    assert assistant[0]["viewType"] == "secure_input"
    assert assistant[0]["view"]["kind"] == "part_exchange_estimate"
    assert assistant[0]["text"] == (
        "I can help with that estimate. What’s your vehicle registration? For example, AB12 CDE."
    )
    assert assistant[0].get("blocks") is None
    assert "VIN" not in assistant[0]["text"]
    assert "service history" not in assistant[0]["text"]


def test_structured_choice_collection_owns_service_options_and_prompt(tmp_path: Path) -> None:
    class ServiceQuestionProvider:
        async def generate_turn(self, messages):
            if messages.planning_context.trusted_tool_facts:
                trusted = messages.planning_context.trusted_tool_facts
                assert trusted["secureInputActivation"] is False
                assert trusted["selectionPromptActivation"] is True
                assert trusted["collectionPresentationActivation"] is True
                collection = trusted["results"][0]["availableCollections"][0]["reference"]
                return ProviderReply(
                    "",
                    response_draft=GroundedResponseDraft(
                        messages=[
                            GroundedMessageDraft(
                                purpose="answer",
                                blocks=[
                                    {
                                        "type": "list",
                                        "items": [
                                            {
                                                "segments": [
                                                    {
                                                        "type": "text",
                                                        "text": "MOT — Annual MOT inspection.",
                                                    }
                                                ]
                                            },
                                            {
                                                "segments": [
                                                    {
                                                        "type": "text",
                                                        "text": (
                                                            "Full service — Comprehensive annual "
                                                            "vehicle service."
                                                        ),
                                                    }
                                                ]
                                            },
                                        ],
                                    }
                                ],
                            ),
                            GroundedMessageDraft(
                                purpose="workflow_prompt",
                                collectionReference=collection,
                                segments=[
                                    TextSegment(
                                        type="text",
                                        text="Which workshop service would you like to book?",
                                    )
                                ],
                            ),
                        ]
                    ),
                )
            return ProviderReply("", tool_calls=direct_tool_calls("list_workshop_slots"))

    class ServiceQuestionTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "list_workshop_slots"
            assert arguments == {}
            assert conversation_id
            payload = {
                "version": 1,
                "selectionOnly": True,
                "choiceEntityType": "service",
                "choiceField": "serviceTypeId",
                "collectionViewType": "choice_list",
                "items": [
                    {"id": "mot", "name": "MOT"},
                    {"id": "full-service", "name": "Full service"},
                ],
                "collectionPresentation": {
                    "schemaVersion": 1,
                    "layout": "bullet_list",
                    "purpose": "choice",
                    "items": [
                        {"label": "MOT", "description": "Annual MOT inspection."},
                        {
                            "label": "Full service",
                            "description": "Comprehensive annual vehicle service.",
                        },
                    ],
                },
                "suggestions": [
                    {
                        "label": "MOT",
                        "text": "Book service: MOT",
                        "action": {"type": "select_workshop_service", "serviceTypeId": "mot"},
                    },
                    {
                        "label": "Full service",
                        "text": "Book service: Full service",
                        "action": {
                            "type": "select_workshop_service",
                            "serviceTypeId": "full-service",
                        },
                    },
                ],
            }
            return ToolResult(
                (
                    "Which workshop service would you like to book? You can also tell me "
                    "what your car needs if you’re unsure."
                ),
                "service_list",
                payload,
                payload,
            )

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=ServiceQuestionProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        browser.app.state.orchestrator.tools = ServiceQuestionTools()
        response = browser.post(
            f"/api/chat/v1/conversations/{created.json()['conversationId']}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Book a workshop appointment",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assistant = [item for item in response.json()["messages"] if item["role"] == "assistant"]
    assert [item["text"] for item in assistant] == [
        "Which workshop service would you like to book?"
    ]
    assert assistant[0]["viewType"] == "choice_list"
    assert assistant[0]["view"]["selectionOnly"] is True
    assert assistant[0]["view"]["collectionPresentation"]["items"] == [
        {"label": "MOT", "description": "Annual MOT inspection."},
        {
            "label": "Full service",
            "description": "Comprehensive annual vehicle service.",
        },
    ]
    assert assistant[0]["view"]["suggestions"][0]["label"] == "MOT"
    assert all(
        block["type"] != "list" for message in assistant for block in message.get("blocks") or []
    )


@pytest.mark.parametrize("service_reply", ["interin", "interim"])
def test_typed_workshop_service_can_return_location_choices(
    tmp_path: Path,
    service_reply: str,
) -> None:
    """A valid tool result must not depend on a duplicated per-tool renderer checklist."""

    class WorkshopProvider:
        async def generate_turn(self, messages):
            if messages.planning_context.trusted_tool_facts:
                grounded = grounded_test_reply(
                    messages,
                    "Which available option would you like?",
                )
                assert grounded is not None
                return grounded
            if "book a workshop" in messages.planning_context.latest_customer_message.casefold():
                return ProviderReply(
                    "",
                    tool_calls=direct_tool_calls("list_workshop_slots"),
                )
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls(
                    "refine_workshop_slots",
                    {"serviceTypeId": "interim-service"},
                ),
            )

    class WorkshopTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            self.calls.append((name, arguments))
            assert conversation_id
            if name == "list_workshop_slots":
                payload = {
                    "version": 1,
                    "selectionOnly": True,
                    "choiceEntityType": "service",
                    "choiceField": "serviceTypeId",
                    "collectionViewType": "choice_list",
                    "items": [{"id": "interim-service", "name": "Interim service"}],
                    "suggestions": [
                        {
                            "label": "Interim service",
                            "text": "Book service: Interim service",
                            "action": {
                                "type": "select_workshop_service",
                                "serviceTypeId": "interim-service",
                            },
                        }
                    ],
                    "collectionPresentation": {
                        "schemaVersion": 1,
                        "layout": "chip_grid",
                        "purpose": "choice",
                        "items": [{"label": "Interim service"}],
                    },
                }
                return ToolResult("Choose a service.", "service_list", payload, payload)
            assert name == "refine_workshop_slots"
            assert arguments == {"serviceTypeId": "interim-service"}
            payload = {
                "version": 1,
                "items": [
                    {
                        "id": "northstar-stockport",
                        "name": "Northstar Stockport",
                        "town": "Stockport",
                    }
                ],
                "suggestions": [],
            }
            return ToolResult(
                "Choose a workshop location.",
                "workshop_location_list",
                payload,
                payload,
            )

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=WorkshopProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        tools = WorkshopTools()
        browser.app.state.orchestrator.tools = tools

        first = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Book a workshop appointment",
                "pageContext": CONTEXT,
            },
        )
        second = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": service_reply,
                "pageContext": CONTEXT,
            },
        )

    assert first.status_code == 200
    assert any(message.get("viewType") == "choice_list" for message in first.json()["messages"])
    assert second.status_code == 200
    location_cards = card_data(second.json(), "dealership")
    assert location_cards[0]["items"][0]["id"] == "northstar-stockport"
    assert tools.calls == [
        ("list_workshop_slots", {}),
        ("refine_workshop_slots", {"serviceTypeId": "interim-service"}),
    ]


def test_failed_composition_uses_trusted_fallback_and_advances_workflow_state(
    tmp_path: Path,
) -> None:
    class InvalidComposer:
        async def generate_turn(self, messages):
            if messages.planning_context.trusted_tool_facts:
                return ProviderReply("")
            return ProviderReply("", tool_calls=direct_tool_calls("list_workshop_slots"))

    class WorkshopTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "list_workshop_slots"
            assert arguments == {}
            assert conversation_id
            payload = {
                "version": 1,
                "selectionOnly": True,
                "choiceEntityType": "service",
                "choiceField": "serviceTypeId",
                "collectionViewType": "choice_list",
                "items": [{"id": "interim-service", "name": "Interim service"}],
                "suggestions": [],
                "collectionPresentation": {
                    "schemaVersion": 1,
                    "layout": "chip_grid",
                    "purpose": "choice",
                    "items": [{"label": "Interim service"}],
                },
            }
            return ToolResult("Choose a service.", "service_list", payload, payload)

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=InvalidComposer())) as browser:
        conversation_id = browser.post(
            "/api/chat/v1/conversations",
            json={"pageContext": CONTEXT},
        ).json()["conversationId"]
        browser.app.state.orchestrator.tools = WorkshopTools()
        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Book a workshop appointment",
                "pageContext": CONTEXT,
            },
        )
        workflow_state = browser.app.state.conversations.get_workflow_state(conversation_id)

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["error"] is None
    assert any(
        message.get("viewType") == "choice_list" for message in response.json()["messages"]
    )
    assert workflow_state["activeWorkflow"] == "workshop_booking"
    assert workflow_state["stage"] == "choosing_service"


@pytest.mark.parametrize("composition_failure", ["invalid", "timeout_during_repair"])
def test_failed_ai_slot_composition_falls_back_without_losing_liverpool_selection(
    tmp_path: Path,
    composition_failure: str,
) -> None:
    class InvalidSlotComposer:
        def __init__(self) -> None:
            self.compositions = 0

        async def generate_turn(self, messages):
            assert messages.planning_context.trusted_tool_facts
            self.compositions += 1
            if composition_failure == "timeout_during_repair" and self.compositions == 2:
                raise TimeoutError("composer repair timed out")
            return ProviderReply(
                "",
                response_draft=GroundedResponseDraft(
                    messages=[
                        GroundedMessageDraft(
                            purpose="workflow_prompt",
                            segments=[
                                TextSegment(
                                    type="text",
                                    text=(
                                        "Liverpool has an appointment at 14:00. "
                                        "What day would suit you?"
                                    ),
                                )
                            ],
                        )
                    ]
                ),
            )

    class LiverpoolSlotTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "list_workshop_slots"
            assert arguments == {
                "serviceTypeId": "brake-inspection",
                "dealershipId": "northstar-liverpool",
            }
            assert conversation_id
            items = [
                {
                    "id": "ws-slot-0203",
                    "dealershipId": "northstar-liverpool",
                    "serviceTypeId": "brake-inspection",
                    "startsAt": "2026-09-04T14:00:00Z",
                    "status": "available",
                    "dealershipName": "Northstar Liverpool",
                    "dealershipTown": "Liverpool",
                    "serviceName": "Brake inspection",
                    "durationMinutes": 60,
                    "priceFromPence": 7900,
                }
            ]
            return ToolResult(
                "Found 1 result.",
                "slot_list",
                {"version": 1, "items": items, "mode": "booking"},
                {"items": items},
            )

    provider = InvalidSlotComposer()
    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=provider)) as browser:
        conversation_id = browser.post(
            "/api/chat/v1/conversations",
            json={"pageContext": CONTEXT},
        ).json()["conversationId"]
        browser.app.state.orchestrator.tools = LiverpoolSlotTools()
        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Use the workshop in Liverpool",
                "pageContext": CONTEXT,
                "action": {
                    "type": "try_workshop_location",
                    "serviceTypeId": "brake-inspection",
                    "dealershipId": "northstar-liverpool",
                },
            },
        ).json()
        workflow_state = browser.app.state.conversations.get_workflow_state(conversation_id)

    assistant = next(message for message in response["messages"] if message["role"] == "assistant")
    assert provider.compositions == 2
    assert response["status"] == "completed"
    assert response["error"] is None
    assert assistant["text"] == (
        "Liverpool has workshop availability. "
        "What day or date would suit you, and approximately what time?"
    )
    assert assistant["viewType"] == "trusted_slot_context"
    assert workflow_state["entities"]["dealershipId"] == "northstar-liverpool"
    assert workflow_state["constraints"]["missingPublicFields"] == [
        "preferredDayOrDate",
        "approximateTime",
    ]


def test_workshop_alternative_action_preserves_schedule_and_returns_live_stockport_slots(
    tmp_path: Path,
) -> None:
    class InvalidComposer:
        def __init__(self) -> None:
            self.compositions = 0

        async def generate_turn(self, messages):
            assert messages.planning_context.trusted_tool_facts
            self.compositions += 1
            return ProviderReply(
                "",
                response_draft=GroundedResponseDraft(
                    messages=[
                        GroundedMessageDraft(
                            purpose="answer",
                            segments=[
                                TextSegment(
                                    type="text",
                                    text=(
                                        "The selected service and workshop are ready for "
                                        "appointment scheduling."
                                    ),
                                )
                            ],
                        )
                    ]
                ),
            )

    class StockportGateway:
        def __init__(self) -> None:
            self.calls = []

        async def list_workshop_slots(self, filters):
            self.calls.append(dict(filters))
            return {
                "items": [
                    {
                        "id": "ws-slot-stockport-morning",
                        "dealershipId": "northstar-stockport",
                        "serviceTypeId": "tyre-fitting",
                        "startsAt": "2099-09-07T09:00:00Z",
                        "status": "available",
                        "dealershipName": "Northstar Stockport",
                        "dealershipTown": "Stockport",
                        "serviceName": "Tyre fitting",
                        "durationMinutes": 90,
                    }
                ]
            }

    provider = InvalidComposer()
    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=provider)) as browser:
        conversation_id = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()["conversationId"]
        browser.app.state.conversations.update_workflow_state(
            conversation_id,
            workflow_state(
                "workshop_booking",
                "choosing_dealership",
                entities={
                    "serviceTypeId": "tyre-fitting",
                    "dealershipId": "northstar-bolton",
                },
                constraints={
                    "schedulingPreferences": {
                        "dateFrom": "2099-09-07",
                        "dateTo": "2099-09-07",
                        "timeOfDay": "morning",
                    },
                    "missingPublicFields": ["dealershipId"],
                    "acceptedInputFields": [],
                    "continuation": "choose_alternative_location",
                    "resolution": {
                        "kind": "workshop_slots",
                        "status": "empty",
                        "scope": "preference_filtered",
                        "continuation": "choose_alternative_location",
                        "count": 0,
                        "alternativeCount": 1,
                        "scheduleAlternativeCount": 0,
                    },
                },
            ),
        )
        gateway = StockportGateway()
        browser.app.state.orchestrator.tools = ApplicationToolExecutor(
            gateway, browser.app.state.workflows
        )

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Find a workshop appointment in Stockport",
                "pageContext": CONTEXT,
                "action": {
                    "type": "try_workshop_location",
                    "serviceTypeId": "tyre-fitting",
                    "dealershipId": "northstar-stockport",
                },
            },
        )
        state = browser.app.state.conversations.get_workflow_state(conversation_id)

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert provider.compositions == 2
    assert gateway.calls == [
        {
            "serviceTypeId": "tyre-fitting",
            "dealershipId": "northstar-stockport",
            "dateFrom": "2099-09-07",
            "dateTo": "2099-09-07",
        }
    ]
    assistant_text = "\n".join(
        message["text"]
        for message in response.json()["messages"]
        if message["role"] == "assistant"
    )
    assert "Which one would you like?" in assistant_text
    assert "What else can I help you with?" not in assistant_text
    assert "ready for appointment scheduling" not in assistant_text
    assert state["entities"] == {
        "serviceTypeId": "tyre-fitting",
        "dealershipId": "northstar-stockport",
    }
    assert state["constraints"]["schedulingPreferences"] == {
        "dateFrom": "2099-09-07",
        "dateTo": "2099-09-07",
        "timeOfDay": "morning",
    }
    assert state["constraints"]["missingPublicFields"] == ["slotId"]
    assert state["constraints"]["continuation"] == "request_slot_selection"


def test_structured_information_collection_cannot_be_flattened_into_model_prose(
    tmp_path: Path,
) -> None:
    class CollectionProvider:
        async def generate_turn(self, messages):
            trusted = messages.planning_context.trusted_tool_facts
            if trusted:
                assert trusted["collectionPresentationActivation"] is True
                collection = trusted["results"][0]["availableCollections"][0]["reference"]
                suggestions = [
                    item["reference"] for item in trusted["results"][0]["availableSuggestions"]
                ]
                return ProviderReply(
                    "",
                    response_draft=GroundedResponseDraft(
                        messages=[
                            GroundedMessageDraft(
                                purpose="answer",
                                collectionReference=collection,
                                segments=[
                                    TextSegment(
                                        type="text",
                                        text="These are the workshop services we currently provide.",
                                    )
                                ],
                            ),
                            GroundedMessageDraft(
                                purpose="follow_up",
                                segments=[
                                    TextSegment(
                                        type="text",
                                        text="Would you like to book one of these services?",
                                    )
                                ],
                            ),
                        ],
                        suggestionReferences=suggestions,
                    ),
                )
            return ProviderReply("", tool_calls=direct_tool_calls("list_service_types"))

    class ServicesGateway:
        async def list_service_types(self):
            return {
                "items": [
                    {
                        "id": "brake-inspection",
                        "name": "Brake inspection",
                        "description": "Brake condition and performance inspection.",
                    },
                    {
                        "id": "mot",
                        "name": "MOT",
                        "description": "Annual MOT inspection.",
                    },
                ]
            }

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=CollectionProvider())) as browser:
        browser.app.state.orchestrator.tools = ApplicationToolExecutor(ServicesGateway())
        conversation_id = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()["conversationId"]
        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "What workshop services do you provide?",
                "pageContext": CONTEXT,
            },
        )

    assistant = [item for item in response.json()["messages"] if item["role"] == "assistant"]
    assert len(assistant) == 2
    assert assistant[0]["text"] == "These are the workshop services we currently provide."
    assert ";" not in assistant[0]["text"]
    assert assistant[0]["viewType"] == "service_list"
    assert assistant[0]["view"]["collectionPresentation"] == {
        "schemaVersion": 1,
        "layout": "bullet_list",
        "purpose": "information",
        "items": [
            {
                "label": "Brake inspection",
                "description": "Brake condition and performance inspection.",
            },
            {"label": "MOT", "description": "Annual MOT inspection."},
        ],
    }
    assert [item["label"] for item in assistant[0]["view"]["suggestions"]] == [
        "Brake inspection",
        "MOT",
    ]
    assert assistant[1]["text"] == "Would you like to book one of these services?"


def test_free_text_live_service_selection_asks_for_a_time_preference(tmp_path: Path) -> None:
    class BookingProvider:
        async def generate_turn(self, messages):
            if grounded := grounded_test_reply(
                messages,
                "What day or approximate time would suit you for the tyre fitting?",
            ):
                return grounded
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
        state = browser.app.state.conversations.get_state(conversation_id)

    assert response.status_code == 200
    assert tools.calls == [
        ("list_workshop_slots", {"serviceTypeName": "Tyre fitting"}),
    ]
    assert response.json()["workflow"] is None
    assert state.agentWorkflow["activeWorkflow"] == "workshop_booking"
    assert state.agentWorkflow["stage"] == "choosing_time"
    assistant = [item for item in response.json()["messages"] if item["role"] == "assistant"]
    assert [item["text"] for item in assistant] == [
        (
            "What day or approximate time would suit you for the tyre fitting?\n"
            "- A preferred day or date\n"
            "- An approximate time"
        )
    ]
    assert all(item.get("viewType") != "slot_list" for item in assistant)


def test_live_service_name_does_not_restart_the_service_catalogue(tmp_path: Path) -> None:
    class BookingProvider:
        async def generate_turn(self, messages):
            if grounded := grounded_test_reply(messages, "I found a current appointment."):
                return grounded
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
        state = browser.app.state.conversations.get_state(conversation_id)

    assert response.status_code == 200
    assert tools.calls == [
        ("list_workshop_slots", {"serviceTypeName": "MOT"}),
    ]
    assert response.json()["workflow"] is None
    assert state.agentWorkflow["stage"] == "choosing_time"


def test_typed_workshop_plan_cannot_turn_into_an_optional_location_question(
    tmp_path: Path,
) -> None:
    class PlannerProvider:
        async def generate_turn(self, messages):
            if grounded := grounded_test_reply(messages, "I found current tyre appointments."):
                return grounded
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls(
                    "list_workshop_slots",
                    {"serviceTypeName": "tyres"},
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
        state = browser.app.state.conversations.get_state(conversation_id)

    assert response.status_code == 200
    assert tools.calls == [("list_workshop_slots", {"serviceTypeName": "tyres"})]
    assert response.json()["workflow"] is None
    assert state.agentWorkflow["stage"] == "choosing_time"


def test_contextual_workshop_location_cannot_fall_through_to_dealership_lookup(
    tmp_path: Path,
) -> None:
    class MisroutingProvider:

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            if grounded := grounded_test_reply(messages, "I found current tyre appointments."):
                return grounded
            self.calls += 1
            if self.calls == 1:
                return ProviderReply(
                    "",
                    tool_calls=direct_tool_calls(
                        "list_workshop_slots", {"serviceTypeName": "tyres"}
                    ),
                )
            return ProviderReply(
                "",
                [
                    ToolCall(
                        "bolton-tyres",
                        "list_workshop_slots",
                        {"dealershipTown": "Bolton", "serviceTypeName": "tyres"},
                    )
                ],
            )

    class WorkshopTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            assert conversation_id
            self.calls.append((name, arguments))
            assert name == "list_workshop_slots"
            payload = {
                "version": 1,
                "items": [
                    {
                        "id": "ws-slot-0005",
                        "dealershipTown": arguments.get("dealershipTown", "Liverpool"),
                        "serviceTypeName": "Tyre fitting",
                    }
                ],
            }
            return ToolResult("Found 1 result.", "slot_list", payload, payload)

    provider = MisroutingProvider()
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=provider)) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT}).json()
        conversation_id = created["conversationId"]
        tools = WorkshopTools()
        browser.app.state.orchestrator.tools = tools

        first = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "I want to fit tyres",
                "pageContext": CONTEXT,
            },
        )
        second = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "is there a location in bolton for it?",
                "pageContext": CONTEXT,
            },
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert provider.calls == 2
    assert tools.calls == [
        ("list_workshop_slots", {"serviceTypeName": "tyres"}),
        (
            "list_workshop_slots",
            {"dealershipTown": "Bolton", "serviceTypeName": "tyres"},
        ),
    ]
    assert second.json()["workflow"] is None
    assistant = [item for item in second.json()["messages"] if item["role"] == "assistant"]
    assert assistant[-1]["viewType"] == "trusted_slot_context"
    assert assistant[-1]["view"]["items"][0]["dealershipTown"] == "Bolton"


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
            if grounded := grounded_test_reply(
                messages, "Here is the matched service information."
            ):
                return grounded
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
    assert assistant["text"] == "Here is the matched service information."
    assert assistant.get("viewType") is None
    assert tools.calls == [("get_service_information", {"q": wording})]


@pytest.mark.parametrize(
    "wording",
    ["what is tyre cost", "what is cost of tyre fitting"],
)
def test_review_corrects_named_service_before_unrelated_business_tool_executes(
    tmp_path: Path, wording: str
) -> None:
    class RecoveringProvider:

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            if grounded := grounded_test_reply(
                messages, "Here is the matched service information."
            ):
                return grounded
            self.calls += 1
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls(
                    "get_service_information",
                    {"q": wording},
                ),
            )

    class FakeTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            self.calls.append((name, arguments))
            assert conversation_id
            assert name == "get_service_information"
            return ToolResult(
                "Tyre fitting is priced on request and takes about 90 minutes.",
                None,
                None,
                {"resolution": {"status": "matched"}},
            )

    provider = RecoveringProvider()
    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=provider)) as browser:
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

    assert provider.calls == 1
    assert tools.calls == [
        ("get_service_information", {"q": wording}),
    ]
    assert response.json()["messages"][1]["text"] == "Here is the matched service information."


def test_unavailable_business_information_retry_is_bounded(tmp_path: Path) -> None:
    class UnsupportedProvider:

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            if grounded := grounded_test_reply(
                messages,
                "I don’t have confirmed Northstar information that answers that question.",
            ):
                return grounded
            self.calls += 1
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls(
                    "get_business_information",
                    {"topic": "general", "question": "Will you collect my car?"},
                ),
            )

    class UnavailableTools:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, name, arguments, conversation_id):
            self.calls += 1
            assert name == "get_business_information"
            assert conversation_id
            return ToolResult(
                "I don't have confirmed Northstar information that answers that question.",
                None,
                None,
                {"outcome": "unavailable", "topic": "general"},
            )

    provider = UnsupportedProvider()
    tools = UnavailableTools()
    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=provider)) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.orchestrator.tools = tools
        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Will you collect my car?",
                "pageContext": CONTEXT,
            },
        )

    assert provider.calls == 1
    assert tools.calls == 1
    assert "confirmed Northstar information" in response.json()["messages"][1]["text"]


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

        state = browser.app.state.conversations.get_workflow_state(conversation_id)

    assistant = [item for item in response.json()["messages"] if item["role"] == "assistant"]
    assert assistant[-1]["viewType"] == "trusted_slot_context"
    assert assistant[-1]["text"] == "What day or date would suit you?"
    assert state["activeWorkflow"] == "workshop_booking"
    assert state["stage"] == "choosing_time"
    assert state["entities"] == {"serviceTypeId": "tyre-fitting"}
    assert state["constraints"] == {
        "serviceTypeId": "tyre-fitting",
        "missingPublicFields": ["preferredDayOrDate", "approximateTime"],
    }
    assert state["lastTool"] == "list_workshop_slots"


def test_typed_service_choice_can_be_refined_by_the_next_customer_message(
    tmp_path: Path,
) -> None:
    class RefinementProvider:

        async def generate_turn(self, messages):
            if grounded := grounded_test_reply(messages, "I found current tyre appointments."):
                return grounded
            return ProviderReply(
                "",
                [
                    ToolCall(
                        "change-service",
                        "refine_workshop_slots",
                        {"serviceTypeName": "tyre"},
                    )
                ],
            )

    class FakeTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            assert conversation_id
            self.calls.append((name, arguments))
            payload = {"version": 1, "items": [{"id": "ws-slot-0004"}]}
            return ToolResult("Found 1 result.", "slot_list", payload, payload)

    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        tools = FakeTools()
        browser.app.state.orchestrator.provider = RefinementProvider()
        browser.app.state.orchestrator.tools = tools

        first = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Book service: Interim service",
                "pageContext": CONTEXT,
                "action": {
                    "type": "select_workshop_service",
                    "serviceTypeId": "interim-service",
                },
            },
        )
        second = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "I want tyre",
                "pageContext": CONTEXT,
            },
        )
        state = browser.app.state.conversations.get_workflow_state(conversation_id)

    assert first.status_code == 200
    assert second.status_code == 200
    assert tools.calls == [
        ("list_workshop_slots", {"serviceTypeId": "interim-service"}),
        ("refine_workshop_slots", {"serviceTypeName": "tyre"}),
    ]
    assert second.json()["workflow"] is None
    assert state["activeWorkflow"] == "workshop_booking"
    assert state["constraints"] == {
        "serviceTypeName": "tyre",
        "missingPublicFields": ["preferredDayOrDate", "approximateTime"],
    }


def test_plural_dealership_follow_ups_do_not_revert_to_an_older_single_location(
    tmp_path: Path,
) -> None:
    class DealershipProvider:

        async def generate_turn(self, messages):
            if grounded := grounded_test_reply(
                messages, "Here are the current dealership details."
            ):
                return grounded
            text = messages.planning_context.latest_customer_message.casefold()
            if "stockport" in text:
                call = ToolCall(
                    "stockport",
                    "list_dealerships",
                    {"town": "Stockport"},
                )
            elif "departments" in text:
                call = ToolCall("departments", "list_dealership_departments", {})
            else:
                call = ToolCall("hours", "list_opening_hours", {})
            return ProviderReply(
                "",
                [call],
            )

    class DealershipTools:
        def __init__(self) -> None:
            self.calls = []
            self.locations = [
                {"id": "dealer-bolton", "town": "Bolton"},
                {"id": "dealer-liverpool", "town": "Liverpool"},
                {"id": "dealer-manchester", "town": "Manchester"},
                {"id": "dealer-stockport", "town": "Stockport"},
            ]

        async def execute(self, name, arguments, conversation_id):
            assert conversation_id
            self.calls.append((name, arguments))
            if name == "list_dealerships":
                items = [self.locations[-1]]
                return ToolResult(
                    "Dealership details are shown below.",
                    "dealership_list",
                    {"version": 1, "items": items},
                    {"items": items},
                )
            if name == "list_dealership_departments":
                items = [
                    {**item, "departments": ["parts", "sales", "service"]}
                    for item in self.locations
                ]
                return ToolResult(
                    "Dealership departments are shown below.",
                    "dealership_list",
                    {"version": 1, "items": items},
                    {"items": items},
                )
            assert name == "list_opening_hours"
            assert arguments == {}
            items = [{**item, "departments": []} for item in self.locations]
            return ToolResult(
                "Weekly opening hours are shown below.",
                "opening_hours",
                {"version": 1, "items": items, "day": "Weekly"},
                {"items": items},
            )

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=DealershipProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        tools = DealershipTools()
        browser.app.state.orchestrator.tools = tools

        for text in (
            "Where is the Stockport dealership?",
            "What departments do the dealerships have?",
            "Show me the opening hours",
        ):
            response = browser.post(
                f"/api/chat/v1/conversations/{conversation_id}/turns",
                json={
                    "clientMessageId": str(uuid4()),
                    "text": text,
                    "pageContext": CONTEXT,
                },
            )
            assert response.status_code == 200

    assert tools.calls == [
        ("list_dealerships", {"town": "Stockport"}),
        ("list_dealership_departments", {}),
        ("list_opening_hours", {}),
    ]
    hours = card_data(response.json(), "opening_hours")[0]["items"]
    assert [item["town"] for item in hours] == [
        "Bolton",
        "Liverpool",
        "Manchester",
        "Stockport",
    ]


def test_typed_fuel_choice_applies_the_filter_instead_of_searching_the_chip_text(
    tmp_path: Path,
) -> None:
    class CompositionProvider:
        async def generate_turn(self, messages):
            grounded = grounded_test_reply(messages, "Here are the matching vehicles.")
            assert grounded is not None
            return grounded

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
    with TestClient(create_app(settings, provider=CompositionProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.conversations.update_workflow_state(
            conversation_id,
            {
                "version": 3,
                "activeWorkflow": "vehicle_search",
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
    assert tools.calls == [("search_vehicles", {"bodyStyle": "SUV", "fuelType": "Hybrid"})]


def test_offer_enquiry_action_uses_live_offer_context_and_conversational_collection(
    tmp_path: Path,
) -> None:
    class CompositionProvider:
        async def generate_turn(self, messages):
            grounded = grounded_test_reply(
                messages,
                "Which dealership should receive your offer enquiry?",
            )
            assert grounded is not None
            return grounded

    class FakeTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            self.calls.append((name, arguments))
            if name == "get_offer":
                offer = {
                    "id": "offer-07",
                    "vehicleId": "veh-007",
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
                "missingPublicFields": ["dealershipId"],
                "secureFields": ["firstName", "lastName", "email", "phone"],
                "secureInputReady": False,
            }
            return ToolResult("Continue the enquiry.", "draft", payload, payload)

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=CompositionProvider())) as browser:
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

    assistant = [item for item in response.json()["messages"] if item["role"] == "assistant"]
    assert assistant[-1].get("viewType") is None
    assert response.json()["workflow"] is None
    assert tools.calls == [
        ("get_offer", {"id": "offer-07"}),
        (
            "prepare_sales_enquiry",
            {
                "enquiryType": "finance",
                "message": "I am interested in the currently published Jaguar F-PACE PCP offer.",
                "vehicleId": "veh-007",
            },
        ),
    ]


def test_inline_offer_enquiry_endpoint_reuses_the_sales_workflow(tmp_path: Path) -> None:
    class FakeTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            self.calls.append((name, arguments, conversation_id))
            if name == "get_offer":
                offer = {
                    "id": "offer-02",
                    "vehicleId": "veh-002",
                    "make": "BMW",
                    "model": "3 Series",
                    "productType": "PCH",
                }
                return ToolResult("Offer", "offer_list", {"items": [offer]}, offer)
            payload = {
                "version": 1,
                "draftId": "draft-offer",
                "kind": "sales_enquiry",
                "status": "collecting",
                "summary": arguments,
                "dealerships": [],
            }
            return ToolResult("Complete the form.", "draft", payload, payload)

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=FakeLlmProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        tools = FakeTools()
        browser.app.state.tools = UnifiedToolCatalog(tools)

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/offer-enquiry-options",
            json={"offerId": "offer-02"},
        )

    assert response.status_code == 200
    assert response.json()["view"]["kind"] == "sales_enquiry"
    assert tools.calls == [
        ("get_offer", {"id": "offer-02"}, conversation_id),
        (
            "request_offer_enquiry_form",
            {
                "enquiryType": "finance",
                "message": "I am interested in the currently published BMW 3 Series PCH offer.",
                "vehicleId": "veh-002",
            },
            conversation_id,
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
            items = [
                {
                    "id": vehicle_id,
                    "model": f"Car {vehicle_id[-3:]}",
                    "pricePence": 2_000_000 + index * 100_000,
                    "mileage": 10_000 + index * 5_000,
                }
                for index, vehicle_id in enumerate(arguments["vehicleIds"])
            ]
            payload = {"version": 1, "items": items, "suggestions": []}
            return ToolResult(
                "Comparison",
                "vehicle_comparison",
                payload,
                {
                    "items": items,
                    "comparison": {"vehicleIds": arguments["vehicleIds"]},
                },
            )

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=FakeLlmProvider())) as browser:
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
        recommendation = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Extra space matters most.",
                "pageContext": CONTEXT,
                "action": {"type": "compare_displayed_vehicles"},
            },
        )

    assert card_data(first.json(), "vehicle_preview")[0]["items"][0]["id"] == "veh-001"
    assert card_data(more.json(), "vehicle_preview")[0]["page"] == 2
    assert tools.calls[1] == (
        "search_vehicles",
        {"fuelType": "Hybrid", "sort": "priceAsc", "page": 2},
    )
    assert tools.calls[-2] == (
        "compare_vehicles",
        {"vehicleIds": ["veh-004", "veh-005", "veh-006"]},
    )
    assert tools.calls[-1] == tools.calls[-2]
    assert card_data(compared.json(), "vehicle_comparison")
    assert card_data(recommendation.json(), "vehicle_comparison") == []
    assert recommendation.json()["messages"][-1]["purpose"] == "follow_up"


def test_paginating_during_vehicle_selection_refreshes_the_authoritative_test_drive_candidates(
    tmp_path: Path,
) -> None:
    class CandidateRefreshProvider:
        async def generate_turn(self, messages):
            if grounded := grounded_test_reply(
                messages,
                "Here are the current matching vehicles. Which would you like?",
            ):
                return grounded
            latest = messages.planning_context.latest_customer_message.casefold()
            pending = messages.planning_context.pending_interaction or {}
            if "book a test drive for one" in latest:
                return ProviderReply(
                    "Which vehicle would you like to book a test drive for?",
                    interaction=reference_choice_interaction(
                        "Which vehicle would you like to book a test drive for?",
                        ("vehicle:veh-001", "vehicle:veh-002", "vehicle:veh-003"),
                        "test_drive",
                    ),
                    turn_understanding=TurnUnderstanding(
                        dialogueAct="switch_goal",
                        goalRelation="active",
                        intentKinds=["test_drive"],
                        referenceCandidates=[
                            "vehicle:veh-001",
                            "vehicle:veh-002",
                            "vehicle:veh-003",
                        ],
                        ambiguity="reference",
                        confidence="medium",
                    ),
                )
            if "mini cooper" in latest:
                assert pending["kind"] == "reference_choice"
                assert pending["candidate_references"] == [
                    "vehicle:veh-004",
                    "vehicle:veh-005",
                    "vehicle:veh-006",
                ]
                return ProviderReply(
                    "",
                    tool_calls=[
                        ToolCall(
                            "prepare-current-mini",
                            "prepare_test_drive",
                            {"vehicleId": "veh-006"},
                            intent_kind="test_drive",
                        )
                    ],
                    turn_understanding=TurnUnderstanding(
                        dialogueAct="answer_open_question",
                        goalRelation="open_question",
                        intentKinds=["test_drive"],
                        answeredQuestionId=pending["question_id"],
                        resolvedReferences=["vehicle:veh-006"],
                        confidence="high",
                    ),
                )
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls(
                    "search_vehicles", {"maxPricePence": 3_500_000}
                ),
                turn_understanding=TurnUnderstanding(
                    dialogueAct="start_goal",
                    goalRelation="new",
                    intentKinds=["vehicle_search"],
                    confidence="high",
                ),
            )

    class CandidateRefreshTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            self.calls.append((name, arguments))
            assert conversation_id
            if name == "search_vehicles":
                page = int(arguments.get("page", 1))
                models = (
                    ["3 Series", "Countryman", "1 Series"]
                    if page == 1
                    else ["Countryman", "X3", "Cooper"]
                )
                makes = (
                    ["BMW", "MINI", "BMW"]
                    if page == 1
                    else ["MINI", "BMW", "MINI"]
                )
                items = [
                    {
                        "id": f"veh-{(page - 1) * 3 + index:03d}",
                        "year": 2026 - page,
                        "make": make,
                        "model": model,
                        "variant": "Current variant",
                        "dealershipTown": "Manchester",
                    }
                    for index, (make, model) in enumerate(
                        zip(makes, models, strict=True), start=1
                    )
                ]
                payload = {
                    "version": 1,
                    "items": items,
                    "page": page,
                    "pageSize": 3,
                    "total": 9,
                    "search": {
                        "filters": {"maxPricePence": 3_500_000},
                        "page": page,
                    },
                    "suggestions": [],
                }
                return ToolResult("Vehicles", "vehicle_list", payload, payload)
            if name == "prepare_test_drive":
                assert arguments["vehicleId"] == "veh-006"
                assert arguments["selectionEvidence"]["vehicleId"] == "veh-006"
                payload = {
                    "version": 1,
                    "draftId": "draft-current-mini",
                    "kind": "test_drive",
                    "status": "collecting",
                    "summary": {"vehicleId": "veh-006"},
                    "missingPublicFields": ["preferredDayOrDate", "approximateTime"],
                    "secureFields": ["firstName", "lastName", "email", "phone"],
                    "secureInputReady": False,
                }
                return ToolResult("Test drive started.", "draft", payload, payload)
            assert name == "list_test_drive_slots"
            assert arguments == {"vehicleId": "veh-006"}
            return ToolResult(
                "The MINI Cooper is ready for scheduling.",
                None,
                None,
                {
                    "vehicle": {
                        "id": "veh-006",
                        "make": "MINI",
                        "model": "Cooper",
                        "dealershipTown": "Manchester",
                    },
                    "resolution": {
                        "kind": "test_drive_slots",
                        "status": "ready",
                        "scope": "broad",
                        "continuation": "request_schedule_preferences",
                        "vehicleId": "veh-006",
                        "count": 0,
                        "scheduleAlternativeCount": 0,
                        "locationAlternativeCount": 0,
                        "equivalentVehicleSlotCount": 0,
                    },
                },
            )

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    provider = CandidateRefreshProvider()
    tools = CandidateRefreshTools()
    with TestClient(create_app(settings, provider=provider)) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        path = f"/api/chat/v1/conversations/{conversation_id}/turns"
        browser.app.state.orchestrator.tools = tools

        browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "Show me cars under £35,000",
                "pageContext": CONTEXT,
            },
        )
        choice = browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "book a test drive for one of these vehicles",
                "pageContext": CONTEXT,
            },
        )
        more = browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "show me more",
                "pageContext": CONTEXT,
                "action": {"type": "next_vehicle_page"},
            },
        )
        refreshed_state = browser.app.state.conversations.get_state(conversation_id)
        selected = browser.post(
            path,
            json={
                "clientMessageId": str(uuid4()),
                "text": "book test drive for MINI Cooper",
                "pageContext": CONTEXT,
            },
        )
        selected_state = browser.app.state.conversations.get_state(conversation_id)

    assert choice.json()["status"] == "completed"
    assert more.json()["status"] == "completed"
    assert [item["id"] for item in card_data(more.json(), "vehicle_preview")[0]["items"]] == [
        "veh-004",
        "veh-005",
        "veh-006",
    ]
    assert refreshed_state.dialogue.activeQuestion is not None
    assert refreshed_state.dialogue.activeQuestion.candidateReferences == [
        "vehicle:veh-004",
        "vehicle:veh-005",
        "vehicle:veh-006",
    ]
    assert selected.json()["status"] == "completed"
    assert selected_state.agentWorkflow["activeWorkflow"] == "test_drive"
    assert selected_state.agentWorkflow["entities"]["vehicleId"] == "veh-006"
    assert selected_state.dialogue.activeQuestion is not None
    assert selected_state.dialogue.activeQuestion.kind == "input"
    assert selected_state.dialogue.activeQuestion.expectedFields == [
        "dateFrom",
        "dateTo",
        "timeOfDay",
        "schedulePreferenceMode",
    ]


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
                "vehicleId": "veh-001",
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
            payload = {"version": 1, "kind": "test_drive", "draftId": "draft-001"}
            return ToolResult("Ready", "confirmation", payload, payload)

    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.tools = FakeTools()

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/test-drive-drafts",
            json={
                "slotId": "td-slot-0001",
                "vehicleId": "veh-001",
                "firstName": "Jo",
                "lastName": "Smith",
                "email": "jo@example.com",
                "phone": "+44 7123 456789",
            },
        )
        pending = browser.app.state.protected_interactions.active(conversation_id)
        state = browser.app.state.conversations.get_state(conversation_id)

    assert response.status_code == 200
    assert response.json()["view"]["draftId"] == "draft-001"
    assert pending.activeDraftId == "draft-001"
    assert pending.workflowKind == "test_drive"
    assert state.pendingInteraction.interactionId == pending.interactionId
    assert state.stateVersion == pending.createdAtStateVersion


def test_private_review_edit_replaces_one_draft_and_confirmation_without_a_paused_gap(
    tmp_path: Path,
) -> None:
    class PreparingTools:
        def __init__(self, workflows) -> None:
            self.workflows = workflows
            self.calls = 0

        async def execute(
            self,
            name,
            arguments,
            conversation_id,
            *,
            replacement_draft_id=None,
        ):
            assert name == "prepare_test_drive"
            self.calls += 1
            draft = self.workflows.prepare(
                conversation_id,
                "test_drive",
                arguments,
                expected_draft_id=replacement_draft_id,
            )
            payload = {
                "version": 1,
                "draftId": draft.id,
                "kind": draft.kind,
                "status": draft.status,
                "summary": draft.summary,
            }
            return ToolResult(
                "Please review and confirm these details.",
                "confirmation",
                payload,
                payload,
            )

    first_payload = {
        "slotId": "td-slot-0001",
        "vehicleId": "veh-001",
        "firstName": "Alex",
        "lastName": "Morgan",
        "email": "alex@example.com",
        "phone": "07700 900123",
    }
    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        tools = PreparingTools(browser.app.state.workflows)
        browser.app.state.tools = tools

        first = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/test-drive-drafts",
            json=first_payload,
        )
        first_draft_id = first.json()["view"]["draftId"]
        second = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/test-drive-drafts"
            f"?replacesDraftId={first_draft_id}",
            json={**first_payload, "lastName": "Taylor"},
        )
        second_draft_id = second.json()["view"]["draftId"]
        restored = browser.get(f"/api/chat/v1/conversations/{conversation_id}").json()
        active = browser.app.state.protected_interactions.active(conversation_id)
        state = browser.app.state.conversations.get_state(conversation_id)
        statuses = browser.app.state.workflow_repository.statuses(
            conversation_id, [first_draft_id, second_draft_id]
        )

        stale = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/test-drive-drafts"
            f"?replacesDraftId={first_draft_id}",
            json=first_payload,
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert statuses == {first_draft_id: "cancelled", second_draft_id: "awaiting_confirmation"}
    assert [message["viewType"] for message in restored["messages"]] == [
        "superseded_confirmation",
        "confirmation",
    ]
    assert active is not None
    assert active.activeDraftId == second_draft_id
    assert state.pendingInteraction.activeDraftId == second_draft_id
    assert state.agentWorkflow["stage"] != "paused"
    assert stale.status_code == 409
    assert tools.calls == 2


def test_natural_typo_review_edit_activates_one_secure_replacement_without_duplicate_drafts(
    tmp_path: Path,
) -> None:
    class CorrectionProvider:
        async def generate_turn(self, messages):
            assert messages.planning_context.pending_interaction["kind"] == "protected_confirmation"
            return ProviderReply(
                "",
                turn_understanding=TurnUnderstanding(
                    dialogueAct="modify_goal",
                    goalRelation="active",
                    intentKinds=["test_drive"],
                    requestedInputChanges=[
                        RequestedInputChange(
                            field="email",
                            sourceContextId="message:correction",
                            sourceText="change me email",
                        )
                    ],
                    confidence="high",
                ),
            )

    class PreparingTools:
        def __init__(self, workflows) -> None:
            self.workflows = workflows
            self.calls = 0

        async def execute(
            self, name, arguments, conversation_id, *, replacement_draft_id=None
        ):
            assert name == "prepare_test_drive"
            self.calls += 1
            draft = self.workflows.prepare(
                conversation_id,
                "test_drive",
                arguments,
                expected_draft_id=replacement_draft_id,
            )
            payload = {
                "version": 1,
                "draftId": draft.id,
                "kind": draft.kind,
                "status": draft.status,
                "summary": draft.summary,
            }
            return ToolResult(
                "Please review these details. Would you like to confirm the request or change anything?",
                "confirmation",
                payload,
                payload,
            )

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=CorrectionProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        tools = PreparingTools(browser.app.state.workflows)
        browser.app.state.tools = tools
        first_payload = {
            "slotId": "td-slot-0001",
            "vehicleId": "veh-001",
            "firstName": "Alex",
            "lastName": "Morgan",
            "email": "old@example.com",
            "phone": "07700 900123",
        }
        first = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/test-drive-drafts",
            json=first_payload,
        )
        first_draft_id = first.json()["view"]["draftId"]
        original_interaction = browser.app.state.protected_interactions.active(conversation_id)

        correction = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "change me email",
                "pageContext": CONTEXT,
            },
        )
        active_during_edit = browser.app.state.protected_interactions.active(conversation_id)
        statuses_during_edit = browser.app.state.workflow_repository.statuses(
            conversation_id, [first_draft_id]
        )

        replacement = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/test-drive-drafts"
            f"?replacesDraftId={first_draft_id}",
            json={**first_payload, "email": "new@example.com"},
        )
        replacement_id = replacement.json()["view"]["draftId"]
        statuses_after = browser.app.state.workflow_repository.statuses(
            conversation_id, [first_draft_id, replacement_id]
        )

    assert correction.status_code == 200
    assistant = [item for item in correction.json()["messages"] if item["role"] == "assistant"]
    assert len(assistant) == 1
    assert assistant[0]["viewType"] == "secure_input"
    assert assistant[0]["view"]["editField"] == "email"
    assert assistant[0]["view"]["draftId"] == first_draft_id
    assert active_during_edit.interactionId == original_interaction.interactionId
    assert statuses_during_edit == {first_draft_id: "awaiting_confirmation"}
    assert tools.calls == 2
    assert statuses_after == {
        first_draft_id: "cancelled",
        replacement_id: "awaiting_confirmation",
    }


def test_callback_number_typo_uses_the_same_server_owned_review_edit_path(
    tmp_path: Path,
) -> None:
    class CorrectionProvider:
        async def generate_turn(self, messages):
            assert messages.planning_context.pending_interaction["kind"] == "protected_confirmation"
            return ProviderReply(
                "",
                turn_understanding=TurnUnderstanding(
                    dialogueAct="modify_goal",
                    goalRelation="active",
                    intentKinds=["callback"],
                    requestedInputChanges=[
                        RequestedInputChange(
                            field="phone",
                            sourceContextId="message:correction",
                            sourceText="change me number",
                        )
                    ],
                    confidence="high",
                ),
            )

    class PreparingTools:
        def __init__(self, workflows) -> None:
            self.workflows = workflows
            self.calls = 0

        async def execute(
            self, name, arguments, conversation_id, *, replacement_draft_id=None
        ):
            assert name == "prepare_callback"
            self.calls += 1
            draft = self.workflows.prepare(
                conversation_id,
                "callback",
                arguments,
                expected_draft_id=replacement_draft_id,
            )
            payload = {
                "version": 1,
                "draftId": draft.id,
                "kind": draft.kind,
                "status": draft.status,
                "summary": draft.summary,
            }
            return ToolResult(
                "Please review these details. Would you like to confirm the request or change anything?",
                "confirmation",
                payload,
                payload,
            )

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=CorrectionProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        tools = PreparingTools(browser.app.state.workflows)
        browser.app.state.tools = tools
        first_payload = {
            "dealershipId": "northstar-manchester",
            "department": "sales",
            "firstName": "Alex",
            "lastName": "Morgan",
            "email": "alex@example.com",
            "phone": "07700 900123",
            "preferredTime": "Weekday afternoon",
            "reason": "Discuss a vehicle purchase",
        }
        first = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/callback-drafts",
            json=first_payload,
        )
        first_draft_id = first.json()["view"]["draftId"]
        original_interaction = browser.app.state.protected_interactions.active(conversation_id)

        correction = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "change me number",
                "pageContext": CONTEXT,
            },
        )
        active_during_edit = browser.app.state.protected_interactions.active(conversation_id)
        statuses_during_edit = browser.app.state.workflow_repository.statuses(
            conversation_id, [first_draft_id]
        )

        replacement = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/callback-drafts"
            f"?replacesDraftId={first_draft_id}",
            json={**first_payload, "phone": "07700 900456"},
        )
        replacement_id = replacement.json()["view"]["draftId"]
        statuses_after = browser.app.state.workflow_repository.statuses(
            conversation_id, [first_draft_id, replacement_id]
        )

    assert correction.status_code == 200
    assistant = [item for item in correction.json()["messages"] if item["role"] == "assistant"]
    assert len(assistant) == 1
    assert assistant[0]["viewType"] == "secure_input"
    assert assistant[0]["view"]["editField"] == "phone"
    assert assistant[0]["view"]["draftId"] == first_draft_id
    assert active_during_edit.interactionId == original_interaction.interactionId
    assert statuses_during_edit == {first_draft_id: "awaiting_confirmation"}
    assert replacement.status_code == 200
    assert tools.calls == 2
    assert statuses_after == {
        first_draft_id: "cancelled",
        replacement_id: "awaiting_confirmation",
    }


def test_callback_form_payload_passes_the_unified_catalogue_contract(tmp_path: Path) -> None:
    class CallbackExecutor:
        def __init__(self) -> None:
            self.arguments = None

        async def execute(self, name, arguments, conversation_id):
            assert name == "prepare_callback"
            assert conversation_id
            self.arguments = arguments
            payload = {
                "version": 1,
                "draftId": "draft-callback-001",
                "kind": "callback",
                "status": "awaiting_confirmation",
                "summary": arguments,
            }
            return ToolResult("Ready", "confirmation", payload, payload)

    executor = CallbackExecutor()
    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.tools = UnifiedToolCatalog(executor)

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/callback-drafts",
            json={
                "dealershipId": "northstar-manchester",
                "department": "sales",
                "firstName": "Jo",
                "lastName": "Smith",
                "email": "jo@example.com",
                "phone": "+44 7123 456789",
                "preferredTime": "Weekday afternoon",
                "reason": "Discuss a vehicle purchase",
            },
        )

    assert response.status_code == 200
    assert executor.arguments == {
        "dealershipId": "northstar-manchester",
        "department": "sales",
        "firstName": "Jo",
        "lastName": "Smith",
        "email": "jo@example.com",
        "phone": "07123456789",
        "preferredTime": "Weekday afternoon",
        "reason": "Discuss a vehicle purchase",
    }


@pytest.mark.parametrize(
    ("path", "tool_name", "payload"),
    [
        (
            "test-drive-drafts",
            "prepare_test_drive",
            {"slotId": "td-slot-0001", "vehicleId": "veh-001"},
        ),
        (
            "workshop-drafts",
            "prepare_workshop_booking",
            {
                "slotId": "ws-slot-0001",
                "serviceTypeId": "full-service",
                "dealershipId": "northstar-manchester",
                "registration": "AB19 XYZ",
                "mileage": 42_000,
                "notes": "Check the warning light",
            },
        ),
        (
            "part-exchange-drafts",
            "prepare_part_exchange",
            {
                "dealershipId": "northstar-manchester",
                "registration": "AB19 XYZ",
                "mileage": 45_000,
                "condition": "good",
            },
        ),
        (
            "callback-drafts",
            "prepare_callback",
            {
                "dealershipId": "northstar-manchester",
                "department": "sales",
                "reason": "Discuss a vehicle purchase",
                "preferredTime": "Weekday afternoon",
            },
        ),
        (
            "sales-enquiry-drafts",
            "prepare_sales_enquiry",
            {
                "dealershipId": "northstar-manchester",
                "enquiryType": "finance",
                "message": "Please explain this published offer",
                "vehicleId": "veh-001",
            },
        ),
        (
            "vehicle-interest-drafts",
            "prepare_vehicle_interest",
            {"vehicleId": "veh-001", "notes": "Tell me if it becomes available"},
        ),
        (
            "dealership-message-drafts",
            "prepare_dealership_message",
            {
                "dealershipId": "northstar-manchester",
                "department": "sales",
                "subject": "Vehicle collection",
                "message": "Can someone confirm the available collection options?",
                "preferredContactMethod": "email",
            },
        ),
    ],
)
def test_every_customer_form_payload_passes_its_catalogue_contract(
    tmp_path: Path,
    path: str,
    tool_name: str,
    payload: dict,
) -> None:
    class CapturingExecutor:
        def __init__(self) -> None:
            self.call = None

        async def execute(self, name, arguments, conversation_id):
            self.call = (name, arguments, conversation_id)
            view = {"version": 1, "draftId": "draft-001", "kind": name}
            return ToolResult("Ready", "confirmation", view, view)

    executor = CapturingExecutor()
    complete_payload = {
        **payload,
        "firstName": "Jo",
        "lastName": "Smith",
        "email": "jo@example.com",
        "phone": "+44 7123 456789",
    }
    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.tools = UnifiedToolCatalog(executor)

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/{path}",
            json=complete_payload,
        )

    assert response.status_code == 200, response.text
    assert executor.call is not None
    assert executor.call[0] == tool_name
    assert executor.call[2] == conversation_id


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


def test_workshop_reschedule_creates_draft_from_a_live_trusted_slot(tmp_path: Path) -> None:
    class FakeTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            self.calls.append((name, arguments, conversation_id))
            if name == "list_workshop_slots":
                payload = {
                    "version": 1,
                    "mode": "amendment",
                    "items": [
                        {
                            "id": "ws-slot-0002",
                            "startsAt": "2026-08-29T09:00:00Z",
                            "dealershipId": "northstar-stockport",
                            "dealershipName": "Northstar Stockport",
                            "serviceTypeId": "diagnostic-inspection",
                            "serviceName": "Diagnostic inspection",
                        }
                    ],
                }
                return ToolResult("Choose a time.", "slot_list", payload, payload)
            payload = {
                "version": 1,
                "draftId": "draft-amend-001",
                "kind": "workshop_amend",
                "status": "awaiting_confirmation",
                "summary": {},
            }
            return ToolResult("Review changes.", "confirmation", payload, payload)

    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        browser.app.state.workflow_repository.create_grant(
            conversation_id,
            "workshop-booking-001",
            "WORK-10001",
            {
                "reference": "WORK-10001",
                "status": "confirmed",
                "serviceTypeId": "diagnostic-inspection",
                "serviceTypeName": "Diagnostic inspection",
            },
        )
        executor = FakeTools()
        browser.app.state.tools = UnifiedToolCatalog(executor)

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/workshop-amendment-drafts",
            json={"slotId": "ws-slot-0002"},
        )

    assert response.status_code == 200
    assert response.json()["view"]["summary"] == {
        "newAppointment": "2026-08-29T09:00:00Z",
        "newDealership": "Northstar Stockport",
    }
    assert executor.calls == [
        (
            "list_workshop_slots",
            {
                "serviceTypeId": "diagnostic-inspection",
                "workflowMode": "amendment",
            },
            conversation_id,
        ),
        (
            "prepare_workshop_amendment",
            {
                "slotId": "ws-slot-0002",
                "selectedStartsAt": "2026-08-29T09:00:00Z",
                "selectedDealershipId": "northstar-stockport",
                "selectedDealershipName": "Northstar Stockport",
                "selectedServiceTypeId": "diagnostic-inspection",
                "selectedServiceName": "Diagnostic inspection",
            },
            conversation_id,
        ),
    ]


def test_dealership_workshop_action_is_ai_composed_with_structured_services(
    tmp_path: Path,
) -> None:
    class CompositionProvider:
        async def generate_turn(self, messages):
            grounded = grounded_test_reply(
                messages,
                "Which workshop service would you like to book at this dealership?",
            )
            assert grounded is not None
            return grounded

    class FakeTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            self.calls.append((name, arguments, conversation_id))
            payload = {
                "version": 1,
                "dealershipId": "northstar-bolton",
                "items": [{"id": "brake-inspection", "name": "Brake inspection"}],
                "selectionOnly": True,
                "choiceEntityType": "service",
                "choiceField": "serviceTypeId",
                "collectionViewType": "choice_list",
                "collectionPresentation": {
                    "schemaVersion": 1,
                    "layout": "chip_grid",
                    "purpose": "choice",
                    "items": [{"label": "Brake inspection", "description": None}],
                },
            }
            return ToolResult("Choose a service.", "service_list", payload, payload)

    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=CompositionProvider())) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        tools = FakeTools()
        browser.app.state.orchestrator.tools = tools

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Book a service at Northstar Bolton",
                "pageContext": CONTEXT,
                "action": {
                    "type": "start_dealership_workshop",
                    "dealershipId": "northstar-bolton",
                },
            },
        )

    assert response.status_code == 200
    assert any(
        message.get("viewType") == "choice_list" for message in response.json()["messages"]
    )
    assert tools.calls == [
        (
            "list_workshop_slots",
            {"dealershipId": "northstar-bolton"},
            conversation_id,
        )
    ]


def test_natural_service_choice_keeps_the_selected_dealership_and_opens_slots(
    tmp_path: Path,
) -> None:
    class ConversationalProvider:
        def __init__(self) -> None:
            self.planning_calls = 0

        async def generate_turn(self, messages):
            if messages.planning_context.trusted_tool_facts:
                text = (
                    "Which workshop service would you like to book at this dealership?"
                    if messages.planning_context.trusted_tool_facts["selectionPromptActivation"]
                    else "What workshop appointment would suit you? Please include:"
                )
                grounded = grounded_test_reply(messages, text)
                assert grounded is not None
                return grounded
            self.planning_calls += 1
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls(
                    "refine_workshop_slots",
                    {"serviceTypeId": "tyre-fitting"},
                ),
            )

    class FakeTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            assert name in {"list_workshop_slots", "refine_workshop_slots"}
            self.calls.append((arguments, conversation_id))
            if arguments == {"dealershipId": "northstar-stockport"}:
                services = [
                    {"id": "full-service", "name": "Full service"},
                    {"id": "interim-service", "name": "Interim service"},
                    {"id": "manufacturer-recall", "name": "Manufacturer recall"},
                    {"id": "tyre-fitting", "name": "Tyre fitting"},
                ]
                suggestions = [
                    {
                        "label": service["name"],
                        "text": f"Book service: {service['name']}",
                        "action": {
                            "type": "try_workshop_location",
                            "serviceTypeId": service["id"],
                            "dealershipId": "northstar-stockport",
                        },
                    }
                    for service in services
                ]
                payload = {
                    "version": 1,
                    "dealershipId": "northstar-stockport",
                    "items": services,
                    "suggestions": suggestions,
                    "selectionOnly": True,
                    "choiceEntityType": "service",
                    "choiceField": "serviceTypeId",
                    "collectionViewType": "choice_list",
                    "collectionPresentation": {
                        "schemaVersion": 1,
                        "layout": "chip_grid",
                        "purpose": "choice",
                        "items": [
                            {"label": service["name"], "description": None} for service in services
                        ],
                    },
                }
                return ToolResult("Choose a service.", "service_list", payload, payload)
            assert arguments == {
                "serviceTypeId": "tyre-fitting",
                "dealershipId": "northstar-stockport",
            }
            payload = {
                "version": 1,
                "items": [
                    {
                        "id": "ws-slot-stockport-tyre",
                        "serviceTypeId": "tyre-fitting",
                        "dealershipId": "northstar-stockport",
                    }
                ],
            }
            return ToolResult("Found 1 result.", "slot_list", payload, payload)

    provider = ConversationalProvider()
    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    with TestClient(create_app(settings, provider=provider)) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        tools = FakeTools()
        browser.app.state.orchestrator.tools = tools

        services = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "Book a service at Northstar Stockport",
                "pageContext": CONTEXT,
                "action": {
                    "type": "start_dealership_workshop",
                    "dealershipId": "northstar-stockport",
                },
            },
        )
        slots = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "I want to book tyre service",
                "pageContext": CONTEXT,
            },
        )

    assert services.status_code == 200
    assert any(
        message.get("viewType") == "choice_list" for message in services.json()["messages"]
    )
    assert slots.status_code == 200
    assert any(
        message.get("viewType") in {"choice_list", "trusted_slot_context"}
        for message in slots.json()["messages"]
    )
    assert provider.planning_calls == 1
    assert tools.calls == [
        ({"dealershipId": "northstar-stockport"}, conversation_id),
        (
            {
                "serviceTypeId": "tyre-fitting",
                "dealershipId": "northstar-stockport",
            },
            conversation_id,
        ),
    ]


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
                "serviceTypeId": "full-service",
                "dealershipId": "northstar-manchester",
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


def test_completed_workshop_details_replace_the_collecting_draft_through_catalogue(
    tmp_path: Path,
) -> None:
    class ReplacementExecutor:
        def __init__(self, workflows) -> None:
            self.workflows = workflows

        async def execute(
            self,
            name,
            arguments,
            conversation_id,
            *,
            replacement_draft_id=None,
        ):
            assert name == "prepare_workshop_booking"
            draft = self.workflows.prepare(
                conversation_id,
                "workshop_booking",
                arguments,
                expected_draft_id=replacement_draft_id,
            )
            view = {
                "version": 1,
                "draftId": draft.id,
                "kind": draft.kind,
                "status": draft.status,
                "summary": draft.summary,
            }
            return ToolResult("Ready", "confirmation", view, view)

    public_fields = {
        "slotId": "ws-slot-0001",
        "serviceTypeId": "full-service",
        "dealershipId": "northstar-manchester",
    }
    with client(tmp_path) as browser:
        created = browser.post("/api/chat/v1/conversations", json={"pageContext": CONTEXT})
        conversation_id = created.json()["conversationId"]
        workflows = browser.app.state.workflows
        collecting = workflows.prepare(
            conversation_id,
            "workshop_booking",
            public_fields,
        )
        browser.app.state.tools = UnifiedToolCatalog(ReplacementExecutor(workflows))

        response = browser.post(
            f"/api/chat/v1/conversations/{conversation_id}/workshop-drafts"
            f"?replacesDraftId={collecting.id}",
            json={
                **public_fields,
                "firstName": "Jo",
                "lastName": "Smith",
                "email": "jo@example.com",
                "phone": "+44 7123 456789",
                "registration": "AB19 XYZ",
                "mileage": 42_000,
            },
        )
        replacement_id = response.json().get("view", {}).get("draftId")
        statuses = browser.app.state.workflow_repository.statuses(
            conversation_id,
            [collecting.id, replacement_id],
        )

    assert response.status_code == 200, response.text
    assert statuses == {collecting.id: "cancelled", replacement_id: "awaiting_confirmation"}


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
