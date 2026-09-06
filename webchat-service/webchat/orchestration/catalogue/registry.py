"""Runtime registration, validation, and dispatch for the unified tool catalogue."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from .contracts import ToolDefinition
from .definitions import CORE_TOOLS


class UnifiedToolCatalog:
    """Single runtime authority for discovery, validation, policy metadata, and execution."""

    def __init__(self, executor: Any, definitions: Iterable[ToolDefinition] = CORE_TOOLS):
        definition_list = tuple(definitions)
        self._definitions = {definition.id: definition for definition in definition_list}
        if len(self._definitions) != len(definition_list):
            raise ValueError("tool IDs must be unique")
        self._executors: dict[str, Any] = {
            definition.id: executor for definition in definition_list
        }

    def register(self, definition: ToolDefinition, executor: Any) -> None:
        if definition.id in self._definitions:
            raise ValueError(f"duplicate tool ID: {definition.id}")
        self._definitions[definition.id] = definition
        self._executors[definition.id] = executor

    def get(self, name: str) -> ToolDefinition:
        try:
            definition = self._definitions[name]
        except KeyError as error:
            raise ValueError(f"Unknown or disallowed tool: {name}") from error
        if not definition.available:
            raise ValueError(f"Tool is unavailable: {name}")
        return definition

    def planner_tools(self) -> list[ToolDefinition]:
        return [
            definition
            for definition in self._definitions.values()
            if definition.available
            and definition.invocation == "planner"
            and definition.risk != "confirmed_write"
        ]

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._definitions.values())

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        conversation_id: str | None = None,
        *,
        trusted_arguments: dict[str, Any] | None = None,
        replacement_draft_id: str | None = None,
    ):
        definition = self.get(name)
        validated = definition.validate_arguments(arguments)
        trusted = definition.validate_trusted_arguments(trusted_arguments)
        execution_metadata = (
            {"replacement_draft_id": replacement_draft_id}
            if replacement_draft_id is not None
            else {}
        )
        return await asyncio.wait_for(
            self._executors[name].execute(
                name,
                {**validated, **trusted},
                conversation_id,
                **execution_metadata,
            ),
            timeout=definition.timeout_seconds,
        )
