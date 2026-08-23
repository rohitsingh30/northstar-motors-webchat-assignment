from types import SimpleNamespace

import pytest

from webchat.api.models import (
    CallbackDraftRequest,
    DealershipMessageDraftRequest,
    PartExchangeDraftRequest,
    SalesEnquiryDraftRequest,
    VehicleInterestDraftRequest,
    WorkshopDraftRequest,
)
from webchat.api.models import TestDriveDraftRequest as TestDriveRequestModel
from webchat.config import Settings
from webchat.orchestration.catalogue import UnifiedToolCatalog
from webchat.orchestration.catalogue.mcp import McpServerConfig, McpToolSource
from webchat.orchestration.tools.inputs import WorkflowDraftInput


class NeverExecute:
    async def execute(self, name, arguments, conversation_id=None):
        raise AssertionError


@pytest.mark.parametrize(
    "request_model",
    [
        CallbackDraftRequest,
        DealershipMessageDraftRequest,
        PartExchangeDraftRequest,
        SalesEnquiryDraftRequest,
        TestDriveRequestModel,
        VehicleInterestDraftRequest,
        WorkshopDraftRequest,
    ],
)
def test_http_workflow_fields_are_accepted_by_the_catalogue(request_model) -> None:
    """The HTTP form contract must never outgrow the internal draft contract."""
    assert set(request_model.model_fields) <= set(WorkflowDraftInput.model_fields)


def test_callback_preferred_time_is_a_workflow_field_not_workshop_runtime_context() -> None:
    definition = UnifiedToolCatalog(NeverExecute()).get("prepare_callback")

    assert definition.validate_arguments({"preferredTime": "Weekday afternoon"}) == {
        "preferredTime": "Weekday afternoon"
    }


@pytest.mark.asyncio
async def test_mcp_discovery_joins_unified_catalogue_with_risk_metadata(monkeypatch) -> None:
    source = McpToolSource((McpServerConfig("customer-data", "https://mcp.example/tools", {}),))
    read_tool = SimpleNamespace(
        name="find_customer",
        description="Find a customer record",
        inputSchema={
            "type": "object",
            "properties": {"email": {"type": "string"}},
            "required": ["email"],
            "additionalProperties": False,
        },
        outputSchema={"type": "object"},
        annotations=SimpleNamespace(readOnlyHint=True),
    )
    write_tool = SimpleNamespace(
        name="delete_customer",
        description="Delete a customer record",
        inputSchema={"type": "object"},
        outputSchema=None,
        annotations=SimpleNamespace(readOnlyHint=False),
    )

    async def tools(server):
        assert server.name == "customer-data"
        return read_tool, write_tool

    monkeypatch.setattr(source, "_list_tools", tools)
    catalogue = UnifiedToolCatalog(NeverExecute())
    definitions = await source.discover()
    for definition in definitions:
        catalogue.register(definition, source)

    readable = catalogue.get("mcp__customer_data__find_customer")
    mutation = catalogue.get("mcp__customer_data__delete_customer")
    assert readable.executor_kind == "mcp"
    assert readable.output_schema == {"type": "object"}
    assert readable.validate_arguments({"email": "a@example.com"}) == {"email": "a@example.com"}
    assert mutation.risk == "confirmed_write"
    assert readable in catalogue.planner_tools()
    assert mutation not in catalogue.planner_tools()


def test_mcp_configuration_requires_safe_unique_http_servers() -> None:
    settings = Settings(
        _env_file=None,
        mcp_servers_json=(
            '[{"name":"crm","url":"https://mcp.example/tools",'
            '"headers":{"Authorization":"Bearer token"}}]'
        ),
    )
    assert settings.mcp_servers() == (
        {
            "name": "crm",
            "url": "https://mcp.example/tools",
            "headers": {"Authorization": "Bearer token"},
        },
    )

    with pytest.raises(ValueError, match="absolute HTTP"):
        Settings(
            _env_file=None, mcp_servers_json='[{"name":"crm","url":"file:///tmp/x"}]'
        ).mcp_servers()
    with pytest.raises(ValueError, match="duplicate MCP"):
        Settings(
            _env_file=None,
            mcp_servers_json=(
                '[{"name":"crm","url":"https://one.example"},'
                '{"name":"crm","url":"https://two.example"}]'
            ),
        ).mcp_servers()
