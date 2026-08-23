"""Shared result returned by every business-tool handler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from webchat.domain.interactions import PendingInteraction


@dataclass(frozen=True)
class ToolResult:
    text: str
    view_type: str | None
    view_payload: dict[str, Any] | None
    facts: dict[str, Any]
    interaction: PendingInteraction | None = None
