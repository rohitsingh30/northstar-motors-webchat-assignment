from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from webchat.config import Settings
from webchat.domain.interactions import input_interaction, single_action_interaction
from webchat.integrations.contracts import (
    ProposalReview,
    ProviderReply,
    ReviewUnavailableError,
    ToolCall,
)
from webchat.main import create_app
from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.tools.executor import ApplicationToolExecutor
from webchat.orchestration.tools.result import ToolResult

CONTEXT = {"path": "/", "section": "vehicles", "vehicleId": "veh-001", "title": "Used Cars"}


def direct_tool_calls(
    name: str,
    arguments: dict | None = None,
) -> list[ToolCall]:
    return [ToolCall(f"test-{name}", name, dict(arguments or {}))]


def client(tmp_path: Path) -> TestClient:
    settings = Settings(environment="test", webchat_database_path=tmp_path / "webchat.sqlite3")
    return TestClient(create_app(settings))


def test_timeout_failure_returns_a_specific_retryable_customer_message(tmp_path: Path) -> None:
    class TimeoutProvider:
        requires_review = False

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
        created = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()
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


def test_reviewer_failure_returns_a_specific_retryable_customer_message(tmp_path: Path) -> None:
    class InvalidReviewProvider:
        requires_review = True

        async def generate_turn(self, messages):
            del messages
            raise ReviewUnavailableError("reviewer returned no valid decision")

    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=InvalidReviewProvider())) as browser:
        created = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()
        response = browser.post(
            f"/api/chat/v1/conversations/{created['conversationId']}/turns",
            json={
                "clientMessageId": str(uuid4()),
                "text": "I want to book a workshop appointment.",
                "pageContext": CONTEXT,
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error"] == {
        "code": "LLM_REVIEW_FAILED",
        "message": "The AI response could not be validated safely. Please retry.",
        "retryable": True,
    }


def test_daily_turn_limit_rejects_only_new_turns(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
        webchat_daily_turn_limit=1,
    )
    first_message_id = str(uuid4())
    with TestClient(create_app(settings)) as browser:
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
    with TestClient(create_app(settings)) as browser:
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
    assert pickup.json()["messages"][1]["text"].startswith(
        "I don't have confirmed Northstar information"
    )
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


def test_pending_single_action_and_choice_are_provider_independent(tmp_path: Path) -> None:
    class PromptProvider:
        requires_review = False

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            del messages
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
        "Please choose one of the available options."
    )
    assert provider.calls == 3


def test_pending_interaction_does_not_capture_a_new_explicit_request(tmp_path: Path) -> None:
    class PromptProvider:
        requires_review = False

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            del messages
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


def test_chat_yes_cannot_bypass_protected_confirmation_controls(tmp_path: Path) -> None:
    class ConfirmationProvider:
        requires_review = False

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            del messages
            self.calls += 1
            if self.calls > 1:
                return ProviderReply("", interaction_decision="accept")
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

    provider = ConfirmationProvider()
    executor = ConfirmationExecutor()
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=provider)) as browser:
        browser.app.state.orchestrator.tools = executor
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
    assistant = accepted_in_chat.json()["messages"][1]
    assert assistant["viewType"] == "confirmation"
    assert assistant["text"].startswith("Please use the confirmation controls")
    assert provider.calls == 2
    assert executor.calls == 1


def test_named_dealership_question_stays_a_card_with_semantic_provider(
    tmp_path: Path,
) -> None:
    class MisroutingProvider:
        requires_review = True

        def __init__(self) -> None:
            self.called = False

        async def generate_turn(self, messages):
            del messages
            self.called = True
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls("list_dealerships", {"town": "Stockport"}),
                review=ProposalReview("correct", ("WRONG_TOOL", "MISSING_OPERATION")),
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
            payload = {"version": 1, "items": [item], "suggestions": []}
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
    assistant = response.json()["messages"][1]
    assert assistant["viewType"] == "dealership_list"
    assert assistant["view"]["items"][0]["name"] == "Northstar Stockport"
    assert provider.called is True


def test_generic_follow_up_opens_unfilled_message_form_at_trusted_location(
    tmp_path: Path,
) -> None:
    class DealershipMessageProvider:
        requires_review = True

        async def generate_turn(self, messages):
            text = messages.reviewer_context.latest_customer_message.casefold()
            call = (
                ToolCall("location", "list_dealerships", {"town": "Stockport"})
                if "where" in text
                else ToolCall("message", "prepare_dealership_message", {})
            )
            return ProviderReply(
                "",
                [call],
                review=ProposalReview("accept", ("CANDIDATE_VALID",)),
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

    assistant = response.json()["messages"][1]
    assert assistant["viewType"] == "draft"
    assert assistant["view"]["summary"] == {
        "dealershipId": "northstar-stockport",
        "contactProvided": False,
        "kind": "dealership_message",
    }


def test_pch_definition_reaches_online_semantic_provider_before_safety_gate(
    tmp_path: Path,
) -> None:
    class DefinitionProvider:
        requires_review = True

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
                review=ProposalReview("accept", ("CANDIDATE_VALID",)),
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
    assert "viewType" not in assistant


@pytest.mark.parametrize(
    "wording",
    [
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
        requires_review = True

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            del messages
            self.calls += 1
            if self.calls == 1:
                return ProviderReply(
                    "",
                    tool_calls=direct_tool_calls("request_part_exchange_estimate_form"),
                    review=ProposalReview("accept", ("CANDIDATE_VALID",)),
                )
            return ProviderReply(
                (
                    "I don't have confirmed Northstar information that answers that "
                    "question. Would you like me to help you contact a dealership?"
                ),
                review=ProposalReview("clarify", ("UNSUPPORTED_REQUEST",)),
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
    assert estimate.json()["messages"][1]["viewType"] == "part_exchange_estimate_form"
    assert pickup.status_code == 200
    assistant = pickup.json()["messages"][1]
    assert assistant["text"].startswith("I don't have confirmed Northstar information")
    assert "viewType" not in assistant
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
        requires_review = True

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            del messages
            self.calls += 1
            if self.calls == 1:
                return ProviderReply(
                    "",
                    tool_calls=direct_tool_calls("request_part_exchange_estimate_form"),
                    review=ProposalReview("accept", ("CANDIDATE_VALID",)),
                )
            return ProviderReply(
                (
                    "I don't have confirmed Northstar information that answers that "
                    "question. Would you like me to help you contact a dealership?"
                ),
                review=ProposalReview("clarify", ("CONFIRMATION_OR_OUTCOME_CLAIM",)),
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
    assert "viewType" not in assistant
    assert "collect your car" not in assistant["text"]
    assert provider.calls == 2


def test_explicit_pch_offer_request_stays_on_live_offer_cards(tmp_path: Path) -> None:
    class MisroutingProvider:
        requires_review = True

        def __init__(self) -> None:
            self.called = False

        async def generate_turn(self, messages):
            del messages
            self.called = True
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls("list_offers", {"productType": "PCH"}),
                review=ProposalReview("correct", ("WRONG_TOOL", "MISSING_OPERATION")),
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
    assert response.json()["messages"][1]["viewType"] == "offer_list"


def test_conversation_response_vehicle_search_is_replaced_by_application_route(
    tmp_path: Path,
) -> None:
    class ProseProvider:
        requires_review = True

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            del messages
            self.calls += 1
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls(
                    "search_vehicles",
                    {"sort": "priceAsc", "maxPricePence": 3_000_000},
                ),
                review=ProposalReview("correct", ("WRONG_TOOL", "MISSING_OPERATION")),
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
    assert response.json()["messages"][1]["viewType"] == "vehicle_list"


def test_conversation_response_after_facets_still_reaches_vehicle_cards(
    tmp_path: Path,
) -> None:
    class ProseProvider:
        requires_review = True

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            del messages
            self.calls += 1
            if self.calls == 1:
                return ProviderReply(
                    "",
                    tool_calls=direct_tool_calls(
                        "get_vehicle_facets",
                    ),
                    review=ProposalReview("correct", ("WRONG_TOOL", "MISSING_OPERATION")),
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
                review=ProposalReview("accept", ("CANDIDATE_VALID",)),
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
    assert response.json()["messages"][1]["viewType"] == "vehicle_list"


def test_independent_review_correction_reaches_workshop_cards(tmp_path: Path) -> None:
    class ReplanningProvider:
        requires_review = True

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            del messages
            self.calls += 1
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls("list_workshop_slots", {"serviceTypeName": "MOT"}),
                review=ProposalReview("correct", ("WRONG_TOOL", "MISSING_OPERATION")),
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
    assert response.json()["messages"][1]["viewType"] == "slot_list"


def test_independent_review_can_stop_execution_with_a_safe_clarification(
    tmp_path: Path,
) -> None:
    class RepeatingProvider:
        requires_review = True

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            del messages
            self.calls += 1
            return ProviderReply(
                "Which workshop service would you like to book?",
                review=ProposalReview("clarify", ("CLARIFICATION_REQUIRED",)),
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
        requires_review = False

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
            assert messages.reviewer_context.latest_customer_message == options[0]
            assert messages.reviewer_context.pending_interaction is not None
            assert messages.reviewer_context.pending_interaction["kind"] == "input"
            return ProviderReply("I’ll compare those vehicles now.", response_mode="conversation")

    provider = ClarificationProvider()
    settings = Settings(
        environment="test",
        webchat_database_path=tmp_path / "webchat.sqlite3",
    )
    with TestClient(create_app(settings, provider=provider)) as browser:
        created = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()
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
        requires_review = True

        async def generate_turn(self, messages):
            del messages
            return ProviderReply(
                "You're welcome.",
                review=ProposalReview("accept", ("CANDIDATE_VALID",)),
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
        created = browser.post(
            "/api/chat/v1/conversations", json={"pageContext": CONTEXT}
        ).json()
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
    assert any("Page-context rule" in value for value in developer_content)
    assert all("£31,995" not in value for value in developer_content)
    assert all("pricePence" not in value for value in developer_content)
    assert provider.messages.reviewer_context.page_vehicles == [
        {
            "position": 1,
            "vehicleId": "veh-019",
            "label": "Used Cars",
        }
    ]
    assert provider.messages.reviewer_context.page["entities"] == [
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
    assistant = response.json()["messages"][1]
    assert assistant["viewType"] == "dealership_list"
    assert assistant["view"]["items"][0]["name"] == "Northstar Stockport"


def test_existing_workshop_booking_request_always_returns_private_lookup_form(
    tmp_path: Path,
) -> None:
    class FakeTools:
        async def execute(self, name, arguments, conversation_id):
            assert name == "request_workshop_booking_lookup_form"
            assert arguments == {"mode": "amend"}
            assert conversation_id
            payload = {"version": 1, "mode": "amend"}
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
                    {"label": value, "text": value, "action": {"type": "noop"}}
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
    assistant = response.json()["messages"][1]
    assert assistant["text"] == "Choose a fuel type below."
    assert assistant["viewType"] == "suggestion_list"
    assert [item["label"] for item in assistant["view"]["suggestions"]] == [
        "Diesel",
        "Electric",
    ]


def test_service_text_reaches_provider_and_returns_the_appointment_picker(
    tmp_path: Path,
) -> None:
    class BookingProvider:
        def __init__(self) -> None:
            self.called = False

        async def generate_turn(self, messages):
            del messages
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

    assert response.status_code == 200
    assert provider.called is True
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

    assert response.status_code == 200
    assert tools.calls == [("list_workshop_slots", {"serviceTypeName": "tyres"})]
    assert response.json()["messages"][1]["viewType"] == "slot_list"


def test_contextual_workshop_location_cannot_fall_through_to_dealership_lookup(
    tmp_path: Path,
) -> None:
    class MisroutingProvider:
        requires_review = True

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            del messages
            self.calls += 1
            if self.calls == 1:
                return ProviderReply(
                    "",
                    tool_calls=direct_tool_calls(
                        "list_workshop_slots", {"serviceTypeName": "tyres"}
                    ),
                    review=ProposalReview("accept", ("CANDIDATE_VALID",)),
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
                review=ProposalReview("correct", ("WRONG_TOOL", "MISSING_OPERATION")),
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
    assistant = second.json()["messages"][1]
    assert assistant["viewType"] == "slot_list"
    assert assistant["view"]["items"][0]["dealershipTown"] == "Bolton"


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


@pytest.mark.parametrize(
    "wording",
    ["what is tyre cost", "what is cost of tyre fitting"],
)
def test_review_corrects_named_service_before_unrelated_business_tool_executes(
    tmp_path: Path, wording: str
) -> None:
    class RecoveringProvider:
        requires_review = True

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            del messages
            self.calls += 1
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls(
                    "get_service_information",
                    {"q": wording},
                ),
                review=ProposalReview("correct", ("WRONG_TOOL", "MISSING_OPERATION")),
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
    assert response.json()["messages"][1]["text"].startswith("Tyre fitting is priced on request")


def test_unavailable_business_information_retry_is_bounded(tmp_path: Path) -> None:
    class UnsupportedProvider:
        requires_review = True

        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, messages):
            self.calls += 1
            return ProviderReply(
                "",
                tool_calls=direct_tool_calls(
                    "get_business_information",
                    {"topic": "general", "question": "Will you collect my car?"},
                ),
                review=ProposalReview("accept", ("CANDIDATE_VALID",)),
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
    assert response.json()["messages"][1]["text"].startswith(
        "I don't have confirmed Northstar information"
    )


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

    assert response.json()["messages"][1]["viewType"] == "slot_list"
    assert state["activeWorkflow"] == "workshop_booking"
    assert state["stage"] == "choosing_time"
    assert state["entities"] == {"serviceTypeId": "tyre-fitting"}
    assert state["constraints"] == {"serviceTypeId": "tyre-fitting"}
    assert state["lastTool"] == "list_workshop_slots"


def test_typed_service_choice_can_be_refined_by_the_next_customer_message(
    tmp_path: Path,
) -> None:
    class RefinementProvider:
        requires_review = True

        async def generate_turn(self, messages):
            del messages
            return ProviderReply(
                "",
                [
                    ToolCall(
                        "change-service",
                        "refine_workshop_slots",
                        {"serviceTypeName": "tyre"},
                    )
                ],
                review=ProposalReview("accept", ("CANDIDATE_VALID",)),
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
    assert second.json()["messages"][1]["viewType"] == "slot_list"
    assert state["activeWorkflow"] == "workshop_booking"
    assert state["constraints"] == {"serviceTypeName": "tyre"}


def test_plural_dealership_follow_ups_do_not_revert_to_an_older_single_location(
    tmp_path: Path,
) -> None:
    class DealershipProvider:
        requires_review = True

        async def generate_turn(self, messages):
            text = messages.reviewer_context.latest_customer_message.casefold()
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
                review=ProposalReview("accept", ("CANDIDATE_VALID",)),
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
    hours = response.json()["messages"][1]["view"]["items"]
    assert [item["town"] for item in hours] == [
        "Bolton",
        "Liverpool",
        "Manchester",
        "Stockport",
    ]


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
    assert tools.calls == [("search_vehicles", {"bodyStyle": "SUV", "fuelType": "Hybrid"})]


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
    with TestClient(create_app(settings)) as browser:
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
                "vehicleId": "veh-001",
                "firstName": "Jo",
                "lastName": "Smith",
                "email": "jo@example.com",
                "phone": "+44 7123 456789",
            },
        )

    assert response.status_code == 200
    assert response.json()["view"]["draftId"] == "draft-001"


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


def test_dealership_workshop_action_returns_services_in_the_next_message(
    tmp_path: Path,
) -> None:
    class UnexpectedProvider:
        async def generate_turn(self, messages):
            raise AssertionError("A typed dealership workshop action must not call the model")

    class FakeTools:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, name, arguments, conversation_id):
            self.calls.append((name, arguments, conversation_id))
            payload = {
                "version": 1,
                "dealershipId": "northstar-bolton",
                "items": [{"id": "brake-inspection", "name": "Brake inspection"}],
            }
            return ToolResult("Choose a service.", "service_list", payload, payload)

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
                "text": "Book a service at Northstar Bolton",
                "pageContext": CONTEXT,
                "action": {
                    "type": "start_dealership_workshop",
                    "dealershipId": "northstar-bolton",
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["messages"][1]["viewType"] == "service_list"
    assert tools.calls == [
        (
            "list_workshop_slots",
            {"dealershipId": "northstar-bolton"},
            conversation_id,
        )
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
