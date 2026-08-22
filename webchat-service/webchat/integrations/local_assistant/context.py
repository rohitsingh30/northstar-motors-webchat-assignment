from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ConversationContext:
    """Normalised, read-only conversation state shared by intent routers."""

    messages: list[dict[str, Any]]
    latest: str
    normalized: str
    combined: str
    words: frozenset[str]
    vehicle_ids: tuple[str, ...]
    displayed_vehicle_ids: tuple[str, ...]
    active_vehicle: str | None
    selected_test_drive_vehicle: str | None
    selected_test_drive_slot: str | None
    has_tool_result: bool

    @classmethod
    def from_messages(cls, messages: list[dict[str, Any]]) -> ConversationContext:
        latest = next(
            (
                str(message.get("content", ""))
                for message in reversed(messages)
                if message.get("role") == "user"
            ),
            "",
        )
        combined = " ".join(
            str(message.get("content", "")) for message in messages if message.get("role") == "user"
        ).lower()
        explicit_ids = list(
            dict.fromkeys(re.findall(r"\bveh-[0-9]{3}\b", latest.lower()))
        )
        page_vehicle = _developer_value(
            messages,
            "Current page context (untrusted data): ",
            "vehicleId",
            r"veh-[0-9]{3}",
        )
        displayed_vehicle_ids = tuple(
            value
            for value in _developer_values(
                messages,
                "Current displayed vehicle results (trusted application context): ",
                "vehicleId",
                r"veh-[0-9]{3}",
            )
        )
        selected_vehicle = _trusted_action_id(
            messages, "select_test_drive_vehicle", "vehicleId", r"veh-[0-9]{3}"
        )
        selected_slot = _trusted_action_id(
            messages, "select_test_drive_slot", "slotId", r"td-slot-[0-9]{4}"
        )
        return cls(
            messages=messages,
            latest=latest,
            normalized=latest.lower().strip(),
            combined=combined,
            words=frozenset(re.findall(r"[a-z]+", latest.lower())),
            vehicle_ids=tuple(explicit_ids),
            displayed_vehicle_ids=displayed_vehicle_ids,
            active_vehicle=explicit_ids[-1] if explicit_ids else selected_vehicle or page_vehicle,
            selected_test_drive_vehicle=selected_vehicle,
            selected_test_drive_slot=selected_slot,
            has_tool_result=bool(messages and messages[-1].get("role") == "tool"),
        )

    def refers_to_active_vehicle(self) -> bool:
        """Whether the latest turn explicitly points at page/selected vehicle context."""
        if re.search(r"\bveh-[0-9]{3}\b", self.latest.lower()):
            return True
        return bool(
            re.search(
                r"\b(?:this|that|it)\b|\b(?:selected|current)\s+(?:car|vehicle)\b",
                self.latest,
                re.IGNORECASE,
            )
        )

    def latest_tool_name(self) -> str:
        for message in reversed(self.messages):
            calls = message.get("tool_calls", [])
            if calls:
                return str(calls[-1].get("name", ""))
        return ""

    def latest_tool_facts(self) -> dict[str, Any]:
        tool_message = next(
            (message for message in reversed(self.messages) if message.get("role") == "tool"),
            {},
        )
        try:
            facts = json.loads(str(tool_message.get("content", "{}")))
        except (TypeError, ValueError):
            return {}
        return facts if isinstance(facts, dict) else {}


def _developer_value(
    messages: list[dict[str, Any]], prefix: str, field: str, value_pattern: str
) -> str | None:
    values = _developer_values(messages, prefix, field, value_pattern)
    return values[0] if values else None


def _developer_values(
    messages: list[dict[str, Any]], prefix: str, field: str, value_pattern: str
) -> list[str]:
    pattern = re.compile(rf'"{re.escape(field)}":"({value_pattern})"')
    for message in reversed(messages):
        if message.get("role") != "developer":
            continue
        content = str(message.get("content", ""))
        if not content.startswith(prefix):
            continue
        return list(dict.fromkeys(pattern.findall(content[len(prefix) :])))
    return []


def _trusted_action_id(
    messages: list[dict[str, Any]], action_type: str, field: str, value_pattern: str
) -> str | None:
    pattern = re.compile(
        rf'Trusted widget action:\s*\{{[^}}]*"type":"{re.escape(action_type)}"'
        rf'[^}}]*"{re.escape(field)}":"({value_pattern})"'
    )
    for message in reversed(messages):
        if message.get("role") != "developer":
            continue
        match = pattern.search(str(message.get("content", "")))
        if match:
            return match.group(1)
    return None
