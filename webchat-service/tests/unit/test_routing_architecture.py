import ast
from pathlib import Path

import pytest

from webchat.api.models import TurnAction
from webchat.integrations.contracts import ProviderReply, ToolCall
from webchat.integrations.fake_llm import FakeLlmProvider
from webchat.integrations.fake_llm.routing import DeterministicApplicationRouter
from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.presentation.registry import RendererRegistry
from webchat.orchestration.tools.actions import StructuredActionHandler
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


def test_unified_catalogue_covers_every_application_executor_route() -> None:
    executor = ApplicationToolExecutor(StubDealership())
    catalogue = UnifiedToolCatalog(executor)
    expected = (
        set(executor.routes)
        | set(WorkflowToolHandler.TOOL_TO_KIND)
        | set(WorkflowToolHandler.FORM_TO_KIND)
    )

    assert expected == {definition.id for definition in catalogue.definitions()}
    assert all(definition.executor_kind == "application" for definition in catalogue.definitions())


def test_internal_form_tools_are_catalogued_but_not_model_visible() -> None:
    catalogue = UnifiedToolCatalog(ApplicationToolExecutor(StubDealership()))

    definition = catalogue.get("request_offer_enquiry_form")
    assert definition.invocation == "internal"
    assert definition.result_mode == "workflow"
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

    assert "slotId" not in definition.input_schema["properties"]
    assert "selectedStartsAt" not in definition.input_schema["properties"]

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


def test_tool_cannot_return_a_renderer_it_did_not_declare() -> None:
    catalogue = UnifiedToolCatalog(CapturingExecutor())
    result = ToolResult("wrong", "vehicle_list", {"version": 1, "items": []}, {})

    assert not RendererRegistry(catalogue).can_render("list_offers", result)


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


def test_removed_ontology_and_transition_files_do_not_linger() -> None:
    planning = Path(__file__).resolve().parents[2] / "webchat/orchestration/planning"

    assert not (planning / "ontology.py").exists()
    assert not (planning / "transitions.py").exists()
    assert not (planning / "tool_routes.py").exists()
    assert not (planning / "turn_plan.py").exists()


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
