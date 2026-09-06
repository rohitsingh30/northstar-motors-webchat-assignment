import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from webchat.api.models import TurnAction
from webchat.domain.interactions import ActionHandoff, choice_interaction
from webchat.integrations.contracts import (
    PlanningContext,
    ProviderReply,
    SemanticMessages,
    ToolCall,
)
from webchat.integrations.fake_llm import FakeLlmProvider
from webchat.integrations.fake_llm.routing import DeterministicApplicationRouter
from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.contracts.semantics import TurnUnderstanding
from webchat.orchestration.presentation.registry import RendererRegistry
from webchat.orchestration.provider_loop import ProviderToolLoop, TurnReferences
from webchat.orchestration.state import WorkflowStateReducer
from webchat.orchestration.tools.actions import (
    StructuredActionExecution,
    StructuredActionHandler,
    bind_pending_action_handoff,
)
from webchat.orchestration.tools.executor import ApplicationToolExecutor
from webchat.orchestration.tools.result import ToolResult
from webchat.orchestration.tools.workflows import WorkflowToolHandler


class StubDealership:
    pass


class CapturingExecutor:
    def __init__(self) -> None:
        self.calls = []

    async def execute(self, name, arguments, conversation_id=None):
        self.calls.append((name, arguments, conversation_id))
        return ToolResult("ok", "vehicle_list", {"version": 1, "items": []}, {})


class SequenceProvider:
    def __init__(self, replies):
        self.replies = list(replies)

    async def generate_turn(self, messages):
        del messages
        return self.replies.pop(0)


def contact_choice_messages(handoff: ActionHandoff):
    interaction = choice_interaction(
        "Choose a contact method.",
        [
            {"label": "Callback", "action": {"type": "start_callback"}},
            {"label": "Message", "action": {"type": "start_dealership_message"}},
        ],
        handoff=handoff,
    )
    assert interaction is not None
    return [
        SimpleNamespace(
            role="assistant",
            text=interaction.prompt,
            interaction_json=interaction.as_json(),
        ),
        SimpleNamespace(role="user", text="Request a callback", interaction_json=None),
    ]


def test_unified_catalogue_covers_every_application_executor_route() -> None:
    executor = ApplicationToolExecutor(StubDealership())
    catalogue = UnifiedToolCatalog(executor)
    expected = (
        set(executor.routes)
        | set(WorkflowToolHandler.TOOL_TO_KIND)
        | set(WorkflowToolHandler.FORM_TO_KIND)
        | set(WorkflowToolHandler.CONTROL_TOOLS)
    )

    assert expected == {definition.id for definition in catalogue.definitions()}
    assert all(definition.executor_kind == "application" for definition in catalogue.definitions())


@pytest.mark.parametrize(
    "tool_name",
    ["request_offer_enquiry_form", "estimate_part_exchange"],
)
def test_internal_tools_are_catalogued_but_not_model_visible(tool_name: str) -> None:
    catalogue = UnifiedToolCatalog(ApplicationToolExecutor(StubDealership()))

    definition = catalogue.get(tool_name)
    assert definition.invocation == "internal"
    assert definition.id not in {tool.id for tool in catalogue.planner_tools()}


def test_literal_api_and_structured_action_tool_calls_are_catalogued() -> None:
    source_root = Path(__file__).parents[2] / "webchat"
    source_files = [
        *sorted((source_root / "api").glob("*.py")),
        source_root / "orchestration" / "tools" / "actions.py",
    ]
    invoked: set[str] = set()
    for source_file in source_files:
        tree = ast.parse(source_file.read_text())
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            function_name = (
                call.func.attr
                if isinstance(call.func, ast.Attribute)
                else call.func.id
                if isinstance(call.func, ast.Name)
                else ""
            )
            argument_index = 2 if function_name == "_tool_view" else 0
            if function_name not in {"execute", "_execute", "_tool_view"}:
                continue
            if len(call.args) <= argument_index:
                continue
            argument = call.args[argument_index]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                invoked.add(argument.value)

    catalogue_ids = {
        definition.id
        for definition in UnifiedToolCatalog(
            ApplicationToolExecutor(StubDealership())
        ).definitions()
    }
    assert invoked <= catalogue_ids


def test_confirmed_writes_are_not_model_visible() -> None:
    catalogue = UnifiedToolCatalog(CapturingExecutor())

    assert all(definition.risk != "confirmed_write" for definition in catalogue.planner_tools())
    assert all(not definition.id.startswith("confirm_") for definition in catalogue.planner_tools())


def test_model_visible_capability_schemas_exclude_every_secure_field() -> None:
    from webchat.domain.capabilities import CapabilityRegistry

    catalogue = UnifiedToolCatalog(ApplicationToolExecutor(StubDealership()))
    for spec in CapabilityRegistry.SPECS.values():
        definition = catalogue.get(spec.agent_tool)
        provider_fields = set(definition.provider_input_schema.get("properties", {}))
        assert provider_fields.isdisjoint(spec.secure_fields), spec.kind


def test_every_planner_schema_excludes_protected_identity_and_vehicle_fields() -> None:
    catalogue = UnifiedToolCatalog(ApplicationToolExecutor(StubDealership()))
    protected_fields = {
        "firstName",
        "lastName",
        "email",
        "phone",
        "registration",
        "condition",
    }

    for definition in catalogue.planner_tools():
        provider_fields = set(definition.provider_input_schema.get("properties", {}))
        assert provider_fields.isdisjoint(protected_fields), definition.id


def test_slot_tools_expose_only_domain_relevant_provider_fields() -> None:
    catalogue = UnifiedToolCatalog(ApplicationToolExecutor(StubDealership()))

    assert set(catalogue.get("list_test_drive_slots").provider_input_schema["properties"]) == {
        "vehicleId",
        "dealershipId",
        "dealershipTown",
        "dateFrom",
        "dateTo",
        "timeOfDay",
        "schedulePreferenceMode",
    }
    workshop_fields = {
        "dealershipId",
        "serviceTypeId",
        "serviceTypeName",
        "dealershipTown",
        "dateFrom",
        "dateTo",
        "timeOfDay",
        "schedulePreferenceMode",
        "workflowMode",
    }
    assert set(catalogue.get("list_workshop_slots").provider_input_schema["properties"]) == (
        workshop_fields
    )
    assert set(catalogue.get("refine_workshop_slots").provider_input_schema["properties"]) == (
        workshop_fields
    )


def test_verified_booking_cancellation_exposes_no_model_authored_fields() -> None:
    definition = UnifiedToolCatalog(ApplicationToolExecutor(StubDealership())).get(
        "prepare_workshop_cancellation"
    )

    assert definition.provider_input_schema.get("properties", {}) == {}


@pytest.mark.asyncio
async def test_catalogue_validates_before_dispatch() -> None:
    executor = CapturingExecutor()
    catalogue = UnifiedToolCatalog(executor)

    with pytest.raises(ValueError):
        await catalogue.execute("search_vehicles", {"page": 0}, "conversation")
    assert executor.calls == []


@pytest.mark.asyncio
async def test_catalogue_keeps_live_workshop_slot_context_out_of_model_arguments() -> None:
    executor = CapturingExecutor()
    catalogue = UnifiedToolCatalog(executor)
    definition = catalogue.get("prepare_workshop_amendment")
    trusted_slot = {
        "slotId": "ws-slot-0002",
        "selectedStartsAt": "2026-08-29T09:00:00Z",
        "selectedDealershipId": "northstar-stockport",
        "selectedDealershipName": "Northstar Stockport",
        "selectedServiceTypeId": "diagnostic-inspection",
        "selectedServiceName": "Diagnostic inspection",
    }

    assert "slotId" in definition.provider_input_schema["properties"]
    assert "selectedStartsAt" not in definition.provider_input_schema["properties"]
    assert "selectedStartsAt" in definition.input_schema["properties"]

    await catalogue.execute(
        "prepare_workshop_amendment",
        {},
        "conversation",
        trusted_arguments=trusted_slot,
    )

    assert executor.calls == [
        ("prepare_workshop_amendment", trusted_slot, "conversation")
    ]


@pytest.mark.asyncio
async def test_catalogue_rejects_untrusted_or_incomplete_workshop_slot_context() -> None:
    catalogue = UnifiedToolCatalog(CapturingExecutor())

    with pytest.raises(ValueError):
        await catalogue.execute(
            "prepare_workshop_amendment",
            {"slotId": "ws-slot-0002"},
            "conversation",
        )

    with pytest.raises(ValueError):
        await catalogue.execute(
            "prepare_workshop_amendment",
            {},
            "conversation",
            trusted_arguments={"slotId": "ws-slot-0002"},
        )


def test_renderer_registry_accepts_only_known_application_views() -> None:
    registry = RendererRegistry()

    assert registry.can_render(
        ToolResult("vehicles", "vehicle_list", {"version": 1, "items": []}, {})
    )
    assert not registry.can_render(
        ToolResult("unknown", "arbitrary_view", {"version": 1}, {})
    )


@pytest.mark.asyncio
async def test_fake_provider_accepts_injected_direct_tool_planner() -> None:
    class Planner:
        async def generate_turn(self, messages):
            return ProviderReply("", [ToolCall("one", "list_offers", {"productType": "PCH"})])

    reply = await FakeLlmProvider(Planner()).generate_turn(
        [{"role": "user", "content": "Show PCH offers"}]
    )

    assert reply.tool_calls[0].name == "list_offers"


def test_application_router_has_no_generic_conversation_fallback() -> None:
    assert (
        DeterministicApplicationRouter().route(
            [{"role": "user", "content": "tell me a completely unrelated joke"}]
        )
        is None
    )


def test_fake_language_rules_are_physically_isolated_from_hosted_execution() -> None:
    root = Path(__file__).resolve().parents[2] / "webchat"
    production_files = [
        root / "integrations/hosted_llm/provider.py",
        root / "orchestration/provider_loop.py",
        root / "orchestration/policy.py",
    ]

    assert all("fake_llm" not in path.read_text() for path in production_files)
    assert (root / "integrations/fake_llm/routing").is_dir()


def test_public_typed_choices_are_interpreted_by_ai_not_a_runtime_phrase_parser() -> None:
    root = Path(__file__).resolve().parents[2] / "webchat"

    assert "selected_choice_action" not in (
        root / "orchestration/orchestrator.py"
    ).read_text()
    assert "_natural_choice_action" not in (
        root / "domain/interactions.py"
    ).read_text()


def test_policy_never_authors_customer_facing_clarification_copy() -> None:
    root = Path(__file__).resolve().parents[2] / "webchat/orchestration"

    policy_source = (root / "policy.py").read_text()
    loop_source = (root / "provider_loop.py").read_text()
    assert "clarification: str" not in policy_source
    assert "decision.clarification" not in loop_source


def test_removed_ontology_and_transition_files_do_not_linger() -> None:
    planning = Path(__file__).resolve().parents[2] / "webchat/orchestration/planning"

    assert not (planning / "ontology.py").exists()
    assert not (planning / "transitions.py").exists()
    assert not (planning / "tool_routes.py").exists()
    assert not (planning / "turn_plan.py").exists()


def test_retired_ai_reviewer_contract_does_not_reappear() -> None:
    root = Path(__file__).resolve().parents[2] / "webchat"

    assert not (root / "orchestration/planning/review.py").exists()
    assert not (root / "integrations/hosted_llm/review.py").exists()
    sources = [
        root / "integrations/contracts.py",
        root / "integrations/hosted_llm/provider.py",
        root / "orchestration/provider_loop.py",
        root / "orchestration/orchestrator.py",
    ]
    retired_contracts = {
        "ProposalReview",
        "ReviewUnavailableError",
        "requires_review",
        "reviewer_context",
    }

    for source in sources:
        contents = source.read_text()
        assert all(contract not in contents for contract in retired_contracts), source


def test_every_widget_action_has_exactly_one_handler() -> None:
    handler = StructuredActionHandler(CapturingExecutor())
    expected = {
        "next_vehicle_page",
        "search_vehicle_inventory",
        "compare_displayed_vehicles",
        "select_test_drive_vehicle",
        "start_vehicle_interest",
        "start_sales_enquiry",
        "start_offer_enquiry",
        "view_offer",
        "apply_vehicle_preference",
        "choose_vehicle_filter",
        "clear_vehicle_filter",
        "reset_vehicle_search",
        "select_workshop_service",
        "start_dealership_workshop",
        "try_workshop_location",
        "show_workshop_services",
        "start_callback",
        "start_dealership_message",
        "show_dealerships",
        "show_opening_hours",
        "show_dealership_contact_options",
    }

    assert set(handler._handlers) == expected


@pytest.mark.parametrize(
    "action",
    [
        {"type": "choose_vehicle_filter", "vehicleFilter": "fuelType"},
        {"type": "clear_vehicle_filter", "vehicleFilter": "fuelType"},
        {"type": "reset_vehicle_search"},
        {"type": "start_callback"},
        {"type": "start_dealership_message"},
        {"type": "start_dealership_workshop", "dealershipId": "dealer-stockport"},
        {"type": "show_dealerships"},
        {"type": "show_opening_hours"},
        {"type": "show_dealership_contact_options"},
    ],
)
def test_vehicle_filter_controls_are_valid_api_actions(action: dict) -> None:
    assert TurnAction.model_validate(action).model_dump(exclude_none=True) == action


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_type", "tool_name"),
    [
        ("start_callback", "prepare_callback"),
        ("start_dealership_message", "prepare_dealership_message"),
        ("show_dealerships", "list_dealerships"),
        ("show_opening_hours", "list_opening_hours"),
        ("show_dealership_contact_options", "show_dealership_contact_options"),
    ],
)
async def test_dealership_contact_actions_execute_one_catalogue_operation(
    action_type: str, tool_name: str
) -> None:
    executor = CapturingExecutor()
    execution = await StructuredActionHandler(executor).execute(
        {"type": action_type}, "conversation", [], {}
    )

    assert execution is not None
    assert execution.tool_name == tool_name
    assert executor.calls == [(tool_name, {}, "conversation")]


@pytest.mark.asyncio
async def test_part_exchange_contact_handoff_prefills_callback_without_client_authored_fields() -> None:
    executor = CapturingExecutor()
    handoff = ActionHandoff(
        sourceWorkflow="part_exchange",
        topic="part_exchange",
        customerReason="Will you pick up my car?",
        transition="handoff",
    )
    state = {
        "activeWorkflow": "part_exchange",
        "entities": {},
        "constraints": {},
    }

    execution = await StructuredActionHandler(executor).execute(
        {"type": "start_callback"},
        "conversation",
        contact_choice_messages(handoff),
        state,
    )

    assert execution is not None
    assert execution.arguments == {
        "department": "sales",
        "reason": "Will you pick up my car?",
    }
    assert execution.transition == "handoff"
    assert executor.calls == [
        (
            "prepare_callback",
            {"department": "sales", "reason": "Will you pick up my car?"},
            "conversation",
        )
    ]


def test_typed_callback_selection_recovers_the_same_server_owned_handoff() -> None:
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

    binding = bind_pending_action_handoff(
        "prepare_callback",
        {"department": "service"},
        interaction.model_dump(exclude_none=True),
        {"activeWorkflow": "part_exchange", "entities": {}, "constraints": {}},
    )

    assert binding is not None
    assert binding.arguments == {
        "department": "service",
        "reason": "Will you pick up my car?",
    }
    assert binding.transition == "handoff"


def test_typed_action_cannot_consume_a_handoff_for_an_unoffered_operation() -> None:
    interaction = choice_interaction(
        "Choose a contact method.",
        [{"label": "Message", "action": {"type": "start_dealership_message"}}],
        handoff=ActionHandoff(
            topic="part_exchange",
            customerReason="Will you pick up my car?",
        ),
    )
    assert interaction is not None

    assert (
        bind_pending_action_handoff(
            "prepare_callback",
            {},
            interaction.model_dump(exclude_none=True),
            {},
        )
        is None
    )


@pytest.mark.asyncio
async def test_compound_typed_plan_recovers_handoff_without_changing_sibling_call() -> None:
    interaction = choice_interaction(
        "Choose a contact method.",
        [
            {"label": "Callback", "action": {"type": "start_callback"}},
            {"label": "Opening hours", "action": {"type": "show_opening_hours"}},
        ],
        handoff=ActionHandoff(
            sourceWorkflow="part_exchange",
            topic="part_exchange",
            customerReason="Will you pick up my car?",
            transition="handoff",
        ),
    )
    assert interaction is not None
    understanding = TurnUnderstanding(
        dialogueAct="start_goal",
        goalRelation="new",
        intentStructure="compound_outcomes",
        intentKinds=["callback", "opening_hours"],
        confidence="high",
    )
    provider = SequenceProvider(
        [
            ProviderReply(
                "",
                [
                    ToolCall("callback", "prepare_callback", {}, intent_kind="callback"),
                    ToolCall("hours", "list_opening_hours", {}, intent_kind="opening_hours"),
                ],
                turn_understanding=understanding,
            ),
            ProviderReply("Done."),
        ]
    )
    executor = CapturingExecutor()
    catalogue = UnifiedToolCatalog(executor)
    loop = ProviderToolLoop(provider, catalogue, WorkflowStateReducer(), 5)
    history = SemanticMessages(
        [],
        PlanningContext(
            latest_customer_message="Please call me and show the opening hours",
            pending_interaction=interaction.model_dump(exclude_none=True),
        ),
    )

    result = await loop.run(
        history,
        {},
        "conversation",
        TurnReferences([], None, [], []),
    )

    assert result.reply is not None and result.reply.text == "Done."
    assert executor.calls == [
        (
            "prepare_callback",
            {"department": "sales", "reason": "Will you pick up my car?"},
            "conversation",
        ),
        ("list_opening_hours", {}, "conversation"),
    ]


@pytest.mark.asyncio
async def test_part_exchange_contact_handoff_prefills_dealership_message() -> None:
    executor = CapturingExecutor()
    handoff = ActionHandoff(
        sourceWorkflow="part_exchange",
        topic="part_exchange",
        customerReason="Will you pick up my car?",
        transition="handoff",
    )

    execution = await StructuredActionHandler(executor).execute(
        {"type": "start_dealership_message"},
        "conversation",
        contact_choice_messages(handoff),
        {"activeWorkflow": "part_exchange", "entities": {}, "constraints": {}},
    )

    assert execution is not None
    assert execution.arguments == {
        "department": "sales",
        "subject": "Part-exchange enquiry",
        "message": "Will you pick up my car?",
    }
    assert execution.transition == "handoff"


@pytest.mark.asyncio
async def test_contextual_contact_reads_reuse_known_dealership_and_department() -> None:
    executor = CapturingExecutor()
    handler = StructuredActionHandler(executor)
    state = {
        "activeWorkflow": "callback",
        "entities": {"dealershipId": "northstar-bolton"},
        "constraints": {
            "collectedPublicValues": {
                "dealershipId": "northstar-bolton",
                "department": "sales",
            }
        },
    }

    dealership = await handler.execute(
        {"type": "show_dealerships"}, "conversation", [], state
    )
    hours = await handler.execute(
        {"type": "show_opening_hours"}, "conversation", [], state
    )

    assert dealership.tool_name == "get_dealership"
    assert dealership.arguments == {"id": "northstar-bolton"}
    assert hours.arguments == {
        "dealershipId": "northstar-bolton",
        "department": "sales",
    }


@pytest.mark.asyncio
async def test_dealership_message_action_reuses_informational_location_and_department() -> None:
    executor = CapturingExecutor()
    execution = await StructuredActionHandler(executor).execute(
        {"type": "start_dealership_message"},
        "conversation",
        [],
        {
            "activeWorkflow": "dealership_information",
            "entities": {"dealershipId": "northstar-manchester"},
            "constraints": {"town": "Manchester", "department": "sales"},
        },
    )

    assert execution.tool_name == "prepare_dealership_message"
    assert execution.arguments == {
        "dealershipId": "northstar-manchester",
        "dealershipTown": "Manchester",
        "department": "sales",
    }


@pytest.mark.asyncio
async def test_vehicle_sales_enquiry_reuses_authoritative_vehicle_dealership_and_meaning() -> None:
    class VehicleExecutor(CapturingExecutor):
        async def execute(self, name, arguments, conversation_id=None):
            self.calls.append((name, arguments, conversation_id))
            if name == "get_vehicle":
                return ToolResult(
                    "vehicle",
                    "vehicle_detail",
                    {"version": 1},
                    {
                        "id": "veh-019",
                        "make": "BMW",
                        "model": "i4",
                        "dealershipId": "northstar-bolton",
                    },
                )
            return ToolResult("draft", "draft", {"version": 1}, {})

    executor = VehicleExecutor()
    execution = await StructuredActionHandler(executor).execute(
        {"type": "start_sales_enquiry", "vehicleId": "veh-019"},
        "conversation",
        [],
        {},
    )

    assert execution.arguments == {
        "vehicleId": "veh-019",
        "dealershipId": "northstar-bolton",
        "enquiryType": "availability",
        "message": "Please contact me about the availability of the BMW i4.",
    }


@pytest.mark.asyncio
async def test_test_drive_vehicle_action_preserves_existing_schedule_preferences() -> None:
    executor = CapturingExecutor()
    state = {
        "activeWorkflow": "test_drive",
        "entities": {},
        "constraints": {
            "schedulingPreferences": {
                "dateFrom": "2026-09-08",
                "dateTo": "2026-09-08",
                "timeOfDay": "afternoon",
            }
        },
    }

    await StructuredActionHandler(executor).execute(
        {"type": "select_test_drive_vehicle", "vehicleId": "veh-019"},
        "conversation",
        [],
        state,
    )

    assert executor.calls[0][0] == "prepare_test_drive"
    assert executor.calls[0][1]["dateFrom"] == "2026-09-08"
    assert executor.calls[0][1]["timeOfDay"] == "afternoon"
    assert executor.calls[1] == (
        "list_test_drive_slots",
        {
            "vehicleId": "veh-019",
            "dateFrom": "2026-09-08",
            "dateTo": "2026-09-08",
            "timeOfDay": "afternoon",
        },
        "conversation",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (
            {"type": "select_workshop_service", "serviceTypeId": "tyre-fitting"},
            {
                "dealershipId": "northstar-bolton",
                "dateFrom": "2026-09-07",
                "dateTo": "2026-09-07",
                "timeOfDay": "morning",
                "serviceTypeId": "tyre-fitting",
            },
        ),
        (
            {
                "type": "start_dealership_workshop",
                "dealershipId": "northstar-stockport",
            },
            {
                "serviceTypeId": "tyre-fitting",
                "dateFrom": "2026-09-07",
                "dateTo": "2026-09-07",
                "timeOfDay": "morning",
                "dealershipId": "northstar-stockport",
            },
        ),
        (
            {
                "type": "try_workshop_location",
                "serviceTypeId": "tyre-fitting",
                "dealershipId": "northstar-stockport",
            },
            {
                "dateFrom": "2026-09-07",
                "dateTo": "2026-09-07",
                "timeOfDay": "morning",
                "serviceTypeId": "tyre-fitting",
                "dealershipId": "northstar-stockport",
            },
        ),
    ],
)
async def test_every_workshop_choice_preserves_compatible_active_context(
    action: dict, expected: dict
) -> None:
    executor = CapturingExecutor()
    state = {
        "activeWorkflow": "workshop_booking",
        "entities": {
            "serviceTypeId": "tyre-fitting",
            "dealershipId": "northstar-bolton",
        },
        "constraints": {
            "schedulingPreferences": {
                "dateFrom": "2026-09-07",
                "dateTo": "2026-09-07",
                "timeOfDay": "morning",
            }
        },
    }

    execution = await StructuredActionHandler(executor).execute(
        action, "conversation", [], state
    )

    assert execution is not None
    assert execution.arguments == expected
    assert executor.calls == [("list_workshop_slots", expected, "conversation")]


@pytest.mark.asyncio
async def test_workflow_action_handler_cannot_bypass_compiled_continuation_context() -> None:
    executor = CapturingExecutor()
    handler = StructuredActionHandler(executor)

    async def bypass(action, conversation_id, messages, workflow_state):
        del action, conversation_id, messages, workflow_state
        return StructuredActionExecution(
            "list_workshop_slots",
            {"dealershipId": "northstar-stockport"},
            ToolResult("incorrect", None, None, {}),
        )

    handler._handlers["try_workshop_location"] = bypass
    state = {
        "activeWorkflow": "workshop_booking",
        "entities": {"serviceTypeId": "tyre-fitting"},
        "constraints": {
            "schedulingPreferences": {
                "dateFrom": "2026-09-07",
                "dateTo": "2026-09-07",
                "timeOfDay": "morning",
            }
        },
    }

    with pytest.raises(ValueError, match="bypassed its preserved continuation context"):
        await handler.execute(
            {
                "type": "try_workshop_location",
                "serviceTypeId": "tyre-fitting",
                "dealershipId": "northstar-stockport",
            },
            "conversation",
            [],
            state,
        )


@pytest.mark.asyncio
async def test_choose_another_workshop_service_retains_selected_dealership() -> None:
    executor = CapturingExecutor()
    execution = await StructuredActionHandler(executor).execute(
        {"type": "show_workshop_services"},
        "conversation",
        [],
        {
            "activeWorkflow": "workshop_booking",
            "entities": {"dealershipId": "northstar-stockport"},
            "constraints": {},
        },
    )

    assert execution.tool_name == "list_workshop_slots"
    assert execution.arguments == {"dealershipId": "northstar-stockport"}


@pytest.mark.asyncio
async def test_workshop_service_selection_retains_selected_dealership() -> None:
    executor = CapturingExecutor()
    execution = await StructuredActionHandler(executor).execute(
        {"type": "select_workshop_service", "serviceTypeId": "mot"},
        "conversation",
        [],
        {
            "activeWorkflow": "workshop_booking",
            "entities": {"dealershipId": "northstar-stockport"},
            "constraints": {},
        },
    )

    assert execution.tool_name == "list_workshop_slots"
    assert execution.arguments == {
        "serviceTypeId": "mot",
        "dealershipId": "northstar-stockport",
    }


@pytest.mark.asyncio
async def test_workshop_dealership_selection_retains_selected_service() -> None:
    executor = CapturingExecutor()
    execution = await StructuredActionHandler(executor).execute(
        {"type": "start_dealership_workshop", "dealershipId": "northstar-stockport"},
        "conversation",
        [],
        {
            "activeWorkflow": "workshop_booking",
            "entities": {"serviceTypeId": "mot"},
            "constraints": {},
        },
    )

    assert execution.tool_name == "list_workshop_slots"
    assert execution.arguments == {
        "dealershipId": "northstar-stockport",
        "serviceTypeId": "mot",
    }


@pytest.mark.asyncio
async def test_dealership_workshop_action_starts_a_scoped_service_selection() -> None:
    executor = CapturingExecutor()
    execution = await StructuredActionHandler(executor).execute(
        {"type": "start_dealership_workshop", "dealershipId": "dealer-stockport"},
        "conversation",
        [],
        {},
    )

    assert execution is not None
    assert execution.tool_name == "list_workshop_slots"
    assert executor.calls == [
        ("list_workshop_slots", {"dealershipId": "dealer-stockport"}, "conversation")
    ]


@pytest.mark.asyncio
async def test_view_offer_action_uses_the_existing_offer_lookup() -> None:
    executor = CapturingExecutor()
    execution = await StructuredActionHandler(executor).execute(
        {"type": "view_offer", "offerId": "offer-02"},
        "conversation",
        [],
        {},
    )

    assert execution is not None
    assert execution.tool_name == "get_offer"
    assert executor.calls == [("get_offer", {"id": "offer-02"}, "conversation")]


@pytest.mark.asyncio
async def test_vehicle_filter_actions_change_clear_and_reset_server_owned_search() -> None:
    executor = CapturingExecutor()
    handler = StructuredActionHandler(executor)
    state = {
        "activeWorkflow": "vehicle_search",
        "constraints": {"fuelType": "Hybrid", "bodyStyle": "SUV", "page": 2},
    }

    chooser = await handler.execute(
        {"type": "choose_vehicle_filter", "vehicleFilter": "fuelType"},
        "conversation",
        [],
        state,
    )
    cleared = await handler.execute(
        {"type": "clear_vehicle_filter", "vehicleFilter": "fuelType"},
        "conversation",
        [],
        state,
    )
    reset = await handler.execute(
        {"type": "reset_vehicle_search"},
        "conversation",
        [],
        state,
    )

    assert chooser.arguments == {"dimension": "fuelTypes", "reuseCurrentSearch": True}
    assert cleared.arguments == {"bodyStyle": "SUV"}
    assert reset.tool_name == "reset_vehicle_search"
    assert reset.arguments == {}


@pytest.mark.asyncio
async def test_inventory_chip_never_converts_an_unrelated_workflow_state_into_search_filters() -> None:
    executor = CapturingExecutor()
    execution = await StructuredActionHandler(executor).execute(
        {"type": "search_vehicle_inventory"},
        "conversation",
        [],
        {
            "activeWorkflow": "vehicle_comparison",
            "constraints": {
                "comparisonQueries": ["BMW", "MINI Cooper"],
                "resolvedVehicleIds": ["veh-014", "veh-041"],
            },
        },
    )

    assert execution.tool_name == "search_vehicles"
    assert execution.arguments == {}
