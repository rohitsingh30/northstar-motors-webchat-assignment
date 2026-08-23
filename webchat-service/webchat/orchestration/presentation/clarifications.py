"""Turn reviewed finite clarification options into a closed widget view."""

from __future__ import annotations

from webchat.domain.interactions import PendingInteraction
from webchat.orchestration.tools.result import ToolResult


def clarification_suggestion_result(
    text: str,
    suggestions: tuple[str, ...],
    interaction: PendingInteraction | None,
) -> ToolResult | None:
    """Render model-proposed replies without allowing them to execute an action."""
    if not suggestions or interaction is None or interaction.kind != "input":
        return None
    normalized = tuple(dict.fromkeys(option.strip() for option in suggestions if option.strip()))
    if not 2 <= len(normalized) <= 4:
        return None
    view_suggestions = [{"label": option, "text": option} for option in normalized]
    return ToolResult(
        text,
        "suggestion_list",
        {
            "version": 1,
            "completeChoiceSet": True,
            "suggestions": view_suggestions,
        },
        {"suggestionCount": len(view_suggestions)},
        interaction,
    )
