from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from webchat.orchestration.tools.inputs import EmptyInput
from webchat.orchestration.tools.result import ToolResult

from .contracts import ToolDefinition


@dataclass(frozen=True)
class McpServerConfig:
    name: str
    url: str
    headers: dict[str, str]


class McpToolSource:
    """Discover and execute Streamable HTTP MCP tools through the same catalogue."""

    def __init__(self, servers: tuple[McpServerConfig, ...]):
        self.servers = servers
        self._routes: dict[str, tuple[McpServerConfig, str]] = {}

    async def discover(self) -> tuple[ToolDefinition, ...]:
        definitions: list[ToolDefinition] = []
        for server in self.servers:
            tools = await self._list_tools(server)
            for remote in tools:
                remote_name = str(remote.name)
                tool_id = f"mcp__{_safe_name(server.name)}__{_safe_name(remote_name)}"
                self._routes[tool_id] = (server, remote_name)
                annotations = getattr(remote, "annotations", None)
                read_only = bool(getattr(annotations, "readOnlyHint", False))
                definitions.append(
                    ToolDefinition(
                        id=tool_id,
                        title=remote_name.replace("_", " ").title(),
                        description=str(remote.description or f"MCP tool {remote_name}"),
                        input_model=EmptyInput,
                        executor_kind="mcp",
                        executor_reference=f"{server.name}:{remote_name}",
                        invocation="planner" if read_only else "confirmation",
                        risk="read" if read_only else "confirmed_write",
                        result_mode="evidence",
                        input_schema_override=dict(remote.inputSchema or {"type": "object"}),
                        output_schema=(
                            dict(remote.outputSchema)
                            if getattr(remote, "outputSchema", None)
                            else None
                        ),
                    )
                )
        return tuple(definitions)

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        conversation_id: str | None = None,
    ) -> ToolResult:
        del conversation_id
        try:
            server, remote_name = self._routes[name]
        except KeyError as error:
            raise ValueError(f"Unknown MCP tool: {name}") from error
        result = await self._call_tool(server, remote_name, arguments)
        if getattr(result, "isError", False):
            raise ValueError(f"MCP tool failed: {name}")
        structured = getattr(result, "structuredContent", None)
        facts = (
            structured
            if isinstance(structured, dict)
            else {"content": [_content_value(item) for item in getattr(result, "content", [])]}
        )
        text_parts = [
            str(getattr(item, "text", ""))
            for item in getattr(result, "content", [])
            if getattr(item, "text", "")
        ]
        return ToolResult("\n".join(text_parts) or "MCP tool completed.", None, None, facts)

    @staticmethod
    async def _list_tools(server: McpServerConfig):
        async with _session(server) as session:
            result = await session.list_tools()
            return tuple(result.tools)

    @staticmethod
    async def _call_tool(server: McpServerConfig, name: str, arguments: dict[str, Any]):
        async with _session(server) as session:
            return await session.call_tool(name, arguments)


def _session(server: McpServerConfig):
    from contextlib import asynccontextmanager

    from mcp import ClientSession
    from mcp.client.streamable_http import (
        create_mcp_http_client,
        streamable_http_client,
    )

    @asynccontextmanager
    async def connect():
        async with (
            create_mcp_http_client(server.headers) as http_client,
            streamable_http_client(server.url, http_client=http_client) as streams,
        ):
            read_stream, write_stream = streams[:2]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session

    return connect()


def _safe_name(value: str) -> str:
    normalized = "".join(character if character.isalnum() else "_" for character in value)
    return normalized.strip("_").lower()


def _content_value(item: Any) -> dict[str, Any]:
    dump = getattr(item, "model_dump", None)
    return dump(mode="json") if dump else {"type": type(item).__name__}
