import pytest

from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.retrieval import FastEmbedCandidateRetriever, KnowledgeIndex


class NeverExecute:
    async def execute(self, name, arguments, conversation_id=None):
        raise AssertionError


@pytest.fixture(scope="module")
def retriever() -> FastEmbedCandidateRetriever:
    return FastEmbedCandidateRetriever(UnifiedToolCatalog(NeverExecute()))


@pytest.mark.parametrize(
    ("question", "expected_tool"),
    [
        ("show me the cheapest available cars", "search_vehicles"),
        ("show me current offers", "list_offers"),
        ("show me all available cars and clear my filters", "reset_vehicle_search"),
        ("help me find another car", "show_vehicle_preferences"),
        ("show me the cheapest matching vehicles first", "refine_vehicle_search"),
        ("show me the vehicle", "get_vehicle"),
        ("Compare the BMW 1 Series and BMW 3 Series.", "compare_vehicle_models"),
        (
            (
                "Customer request: anything other than the displayed make\n"
                "Active operation: vehicle_search\n"
                "Operation stage: viewing\n"
                'Current operation state: {"entities":{},"constraints":'
                '{"bodyStyle":"SUV","fuelType":"Hybrid"},'
                '"lastTool":"search_vehicles","lastRenderer":"vehicle_list"}'
            ),
            "refine_vehicle_search",
        ),
        ("what is the cost of tyre fitting", "get_service_information"),
        ("do you do car cleaning", "get_service_information"),
        ("what service types do you support", "list_service_types"),
        ("I want to book a workshop appointment", "list_workshop_slots"),
        (
            (
                "Customer request: I want tyre\n"
                "Active operation: workshop_booking\n"
                "Operation stage: choosing_time\n"
                'Current operation state: {"entities":{"serviceTypeId":"interim-service"},'
                '"constraints":{"dealershipTown":"Stockport"},'
                '"lastTool":"list_workshop_slots","lastRenderer":"slot_list"}'
            ),
            "refine_workshop_slots",
        ),
        ("where is the nearest dealership", "list_dealerships"),
        ("what departments do all the dealerships have", "list_dealership_departments"),
        ("what departments does this dealership have", "find_dealership_departments"),
        ("is the dealership open tomorrow", "list_opening_hours"),
        ("are you open on the bank holiday", "list_holiday_opening_hours"),
        (
            (
                "Customer request: yes\n"
                "Immediately preceding exchange (context for follow-up references): "
                '[{"role":"user","text":"Will you pick up my car?"},'
                '{"role":"assistant","text":"I do not have confirmed pickup information. '
                'Would you like me to help you contact a dealership?"}]'
            ),
            "show_dealership_contact_options",
        ),
        ("I want an indicative part-exchange estimate", "request_part_exchange_estimate_form"),
        ("I need to cancel an existing workshop booking", "request_workshop_booking_lookup_form"),
    ],
)
def test_semantic_retrieval_keeps_required_tool_in_planner_candidates(
    retriever: FastEmbedCandidateRetriever, question: str, expected_tool: str
) -> None:
    candidates = retriever.retrieve(question)

    assert expected_tool in {tool.id for tool in candidates.tools}


@pytest.mark.parametrize(
    ("question", "expected_tool", "broader_tools"),
    [
        (
            "Leave a message for a dealership department",
            "prepare_dealership_message",
            {"show_dealership_contact_options", "prepare_callback", "prepare_sales_enquiry"},
        ),
        (
            "Show me dealership contact details",
            "list_dealerships",
            {"show_dealership_contact_options", "get_dealership"},
        ),
        (
            "How can I contact a dealership?",
            "show_dealership_contact_options",
            {"prepare_dealership_message", "prepare_callback", "list_dealerships"},
        ),
        (
            "Find a hybrid SUV under forty thousand pounds",
            "search_vehicles",
            {"show_vehicle_preferences", "select_page_vehicles"},
        ),
        (
            "Compare the BMW 1 Series with the Audi A3",
            "compare_vehicle_models",
            {"compare_vehicles"},
        ),
        (
            "Show me current PCP offers",
            "list_offers",
            {"get_offer"},
        ),
        (
            "How much is tyre fitting?",
            "get_service_information",
            {"list_service_types", "list_workshop_slots", "refine_workshop_slots"},
        ),
        (
            "I need to change an existing unverified workshop booking",
            "request_workshop_booking_lookup_form",
            {"prepare_workshop_amendment", "list_workshop_slots", "refine_workshop_slots"},
        ),
        (
            "I need to cancel an existing unverified workshop booking",
            "request_workshop_booking_lookup_form",
            {"prepare_workshop_cancellation", "cancel_active_capability"},
        ),
        (
            "Value my car for part exchange",
            "request_part_exchange_estimate_form",
            {"prepare_part_exchange"},
        ),
        (
            "Please ask the dealership to call me back",
            "prepare_callback",
            {"show_dealership_contact_options", "prepare_dealership_message"},
        ),
        (
            "What time does the Stockport sales department open on Saturday?",
            "list_opening_hours",
            {"get_opening_hours", "show_dealership_contact_options"},
        ),
        (
            "Arrange a test drive for me",
            "prepare_test_drive",
            {"list_test_drive_slots"},
        ),
        (
            "What workshop services do you offer?",
            "list_service_types",
            {"get_service_information", "list_workshop_slots"},
        ),
        (
            "Book an MOT",
            "list_workshop_slots",
            {"get_service_information", "list_service_types"},
        ),
        (
            "I want to proceed with a part-exchange enquiry",
            "prepare_part_exchange",
            {"request_part_exchange_estimate_form"},
        ),
        (
            "I want to make a sales enquiry about buying a vehicle",
            "prepare_sales_enquiry",
            {"prepare_dealership_message", "show_dealership_contact_options"},
        ),
        (
            "Tell me more about this displayed offer",
            "get_offer",
            {"list_offers"},
        ),
        (
            "Show the complete weekly schedule for this one displayed dealership",
            "get_opening_hours",
            {"list_opening_hours"},
        ),
        (
            "Where are your workshop locations?",
            "list_workshop_locations",
            {"list_dealerships", "list_service_types"},
        ),
    ],
)
def test_semantic_retrieval_ranks_specific_intent_above_broader_siblings(
    retriever: FastEmbedCandidateRetriever,
    question: str,
    expected_tool: str,
    broader_tools: set[str],
) -> None:
    ranked = [tool.id for tool in retriever.retrieve(question).tools]

    assert expected_tool in ranked
    expected_rank = ranked.index(expected_tool)
    assert all(
        sibling not in ranked or expected_rank < ranked.index(sibling)
        for sibling in broader_tools
    )


@pytest.mark.parametrize(
    ("question", "expected_evidence"),
    [
        ("what is PCH", "customer.pch"),
        ("what does PCP mean", "customer.pcp"),
        ("will you pick up my car", "customer.vehicle_pickup_and_collection_policy"),
        ("what does reserved mean", "business.vehicle_availability"),
        (
            "How is the part-exchange estimate calculated?",
            "customer.part_exchange_estimates",
        ),
    ],
)
def test_semantic_retrieval_keeps_answer_evidence_in_context(
    retriever: FastEmbedCandidateRetriever, question: str, expected_evidence: str
) -> None:
    candidates = retriever.retrieve(question)

    assert expected_evidence in {entry.id for entry in candidates.knowledge}


def test_generated_knowledge_index_is_traceable_and_substantial() -> None:
    entries = KnowledgeIndex().entries

    assert len(entries) >= 20
    assert all("#" in entry.source for entry in entries)
    assert {entry.audience for entry in entries} == {"customer", "planner"}
    sources = {entry.source.split("#", 1)[0] for entry in entries}
    assert sources == {
        "PRODUCT-BRIEF.md",
        "docs/BUSINESS-SEMANTICS.md",
        "docs/CUSTOMER-KNOWLEDGE.md",
    }
    assert any(entry.id == "product.vehicle_discovery" for entry in entries)
    assert any(entry.id == "business.workshop_operations" for entry in entries)


def test_customer_knowledge_is_ready_to_render_without_internal_guidance() -> None:
    customer_entries = [entry for entry in KnowledgeIndex().entries if entry.audience == "customer"]
    internal_role_words = {"assistant", "model", "planner", "reviewer"}

    assert customer_entries
    assert all(
        not internal_role_words.intersection(entry.text.casefold().split())
        for entry in customer_entries
    )
    pickup = next(
        entry
        for entry in customer_entries
        if entry.id == "customer.vehicle_pickup_and_collection_policy"
    )
    assert pickup.text == (
        "I don't have confirmed Northstar information about vehicle collection or home pickup. "
        "Would you like me to help you contact a dealership?"
    )
    assert pickup.follow_up_action == {"type": "show_dealership_contact_options"}
