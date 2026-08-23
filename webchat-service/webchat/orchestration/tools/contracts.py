"""Small interfaces shared by orchestration components that execute tools."""

from __future__ import annotations

from typing import Any, Protocol


class ToolExecutor(Protocol):
    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        conversation_id: str | None = None,
    ) -> Any: ...
