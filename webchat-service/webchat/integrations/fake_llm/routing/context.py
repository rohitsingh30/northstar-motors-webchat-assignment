from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ConversationContext:
    """Normalised, read-only conversation state shared by application routes."""

    messages: list[dict[str, Any]]
    latest: str
    normalized: str
    combined: str
    words: frozenset[str]
    vehicle_ids: tuple[str, ...]
    displayed_vehicle_ids: tuple[str, ...]
    displayed_vehicles: tuple[dict[str, Any], ...]
    displayed_offer_ids: tuple[str, ...]
    displayed_dealership_ids: tuple[str, ...]
    active_offer: str | None
    active_vehicle: str | None
    active_workshop_service: str | None
    active_workshop_service_id: str | None
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
        explicit_ids = list(dict.fromkeys(re.findall(r"\bveh-[0-9]{3}\b", latest.lower())))
        page_vehicle = _developer_value(
            messages,
            "Current page context (untrusted data): ",
            "vehicleId",
            r"veh-[0-9]{3}",
        )
        workflow_vehicle = _developer_value(
            messages,
            "Active application workflow state (trusted data): ",
            "vehicleId",
            r"veh-[0-9]{3}",
        )
        active_workflow = _developer_value(
            messages,
            "Active application workflow state (trusted data): ",
            "activeWorkflow",
            r"[a-z][a-z_]{0,39}",
        )
        workflow_service_id = _developer_value(
            messages,
            "Active application workflow state (trusted data): ",
            "serviceTypeId",
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}",
        )
        workflow_service_name = _developer_value(
            messages,
            "Active application workflow state (trusted data): ",
            "serviceTypeName",
            r'[^"\\]{1,200}',
        )
        displayed_vehicles = tuple(
            _developer_json_list(
                messages,
                "Current displayed vehicle results (trusted application context): ",
            )
        )
        displayed_vehicle_ids = tuple(
            str(item["vehicleId"])
            for item in displayed_vehicles
            if re.fullmatch(r"veh-[0-9]{3}", str(item.get("vehicleId") or ""))
        )
        displayed_offer_ids = tuple(
            value
            for value in _developer_values(
                messages,
                "Current displayed offer results (trusted application context): ",
                "offerId",
                r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}",
            )
        )
        displayed_dealership_ids = tuple(
            value
            for value in _developer_values(
                messages,
                "Current displayed dealership results (trusted application context): ",
                "dealershipId",
                r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}",
            )
        )
        page_offer = _developer_value(
            messages,
            "Current page context (untrusted data): ",
            "offerId",
            r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}",
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
            displayed_vehicles=displayed_vehicles,
            displayed_offer_ids=displayed_offer_ids,
            displayed_dealership_ids=displayed_dealership_ids,
            active_offer=page_offer
            or (displayed_offer_ids[0] if len(displayed_offer_ids) == 1 else None),
            active_vehicle=(
                explicit_ids[-1]
                if explicit_ids
                else selected_vehicle or workflow_vehicle or page_vehicle
            ),
            active_workshop_service=(
                workflow_service_name if str(active_workflow or "").startswith("workshop") else None
            ),
            active_workshop_service_id=(
                workflow_service_id if str(active_workflow or "").startswith("workshop") else None
            ),
            selected_test_drive_vehicle=selected_vehicle,
            selected_test_drive_slot=selected_slot,
            has_tool_result=_has_current_turn_tool_result(messages),
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


def _has_current_turn_tool_result(messages: list[dict[str, Any]]) -> bool:
    last_user_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if messages[index].get("role") == "user"
        ),
        -1,
    )
    return any(message.get("role") == "tool" for message in messages[last_user_index + 1 :])


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


def _developer_json_list(messages: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    for message in reversed(messages):
        if message.get("role") != "developer":
            continue
        content = str(message.get("content", ""))
        if not content.startswith(prefix):
            continue
        try:
            value, _ = decoder.raw_decode(content[len(prefix) :])
        except (TypeError, ValueError):
            return []
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]
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
