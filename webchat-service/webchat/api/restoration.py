from __future__ import annotations

import json
from typing import Protocol


class MessageStore(Protocol):
    def list(self, conversation_id: str) -> list: ...


class ReceiptStore(Protocol):
    def statuses(self, conversation_id: str, draft_ids: list[str]) -> dict[str, str]: ...

    def succeeded_receipts(self, conversation_id: str) -> list[dict]: ...


def message_view(message) -> dict:
    view = {
        "id": message.id,
        "turnId": message.turn_id,
        "role": message.role,
        "text": message.text,
        "createdAt": message.created_at,
    }
    if message.view_type:
        view["viewType"] = message.view_type
        view["view"] = json.loads(message.view_payload_json)
    if message.purpose:
        view["purpose"] = message.purpose
    if message.segments_json:
        segments = json.loads(message.segments_json)
        if isinstance(segments, list):
            view["segments"] = segments
    if message.blocks_json:
        blocks = json.loads(message.blocks_json)
        if isinstance(blocks, list):
            view["blocks"] = blocks
    return view


def receipt_text(kind: str, request_kind: str | None = None) -> str:
    if kind == "request_cancelled" and request_kind == "sales_enquiry":
        return "Your sales enquiry has been cancelled."
    return {
        "sales_enquiry": "Your sales enquiry has been submitted.",
        "test_drive": "Your test drive is confirmed.",
        "vehicle_interest": "Your interest has been registered.",
        "callback": "Your callback request has been submitted.",
        "workshop_booking": "Your workshop appointment is confirmed.",
        "workshop_amend": "Your workshop booking has been updated.",
        "workshop_cancel": "Your workshop booking has been cancelled.",
        "workshop_change_abandoned": "Your current workshop booking has been kept.",
        "request_cancelled": "Your request has been cancelled.",
        "dealership_message": "Your dealership message has been submitted.",
        "part_exchange": "Your part-exchange request has been submitted.",
    }.get(kind, "Your request is complete.")


class ConversationRestorer:
    """Reconcile message history with authoritative workflow completion state."""

    def __init__(self, messages: MessageStore, workflows: ReceiptStore):
        self.messages = messages
        self.workflows = workflows

    def restore(self, conversation_id: str) -> list[dict]:
        messages = self.messages.list(conversation_id)
        draft_ids = self._draft_ids(messages)
        statuses = self.workflows.statuses(conversation_id, draft_ids)
        active_messages = self._active_messages(messages, statuses)
        restored = [message_view(message) for message in _restorable_messages(active_messages)]
        self._insert_missing_receipts(restored, self.workflows.succeeded_receipts(conversation_id))
        return restored

    @staticmethod
    def _draft_ids(messages) -> list[str]:
        draft_ids = []
        for message in messages:
            if message.view_type not in {"draft", "secure_input", "confirmation"}:
                continue
            payload = _payload(message)
            if isinstance(payload.get("draftId"), str):
                draft_ids.append(payload["draftId"])
        return draft_ids

    @staticmethod
    def _active_messages(messages, statuses: dict[str, str]) -> list:
        active_messages = []
        for message in messages:
            if message.view_type not in {"draft", "secure_input", "confirmation"}:
                active_messages.append(message)
                continue
            draft_id = _payload(message).get("draftId")
            if (
                not isinstance(draft_id, str)
                or draft_id not in statuses
                or statuses[draft_id] in {"collecting", "awaiting_confirmation"}
            ):
                active_messages.append(message)
        return active_messages

    @staticmethod
    def _insert_missing_receipts(restored: list[dict], completed_workflows: list[dict]) -> None:
        identities = {
            _receipt_identity(item.get("view", {}))
            for item in restored
            if item.get("viewType") == "receipt"
        }
        for completed in completed_workflows:
            receipt = completed["receipt"]
            identity = _receipt_identity(receipt)
            if identity in identities:
                continue
            synthetic = {
                "id": f"workflow-{completed['draftId']}",
                "role": "assistant",
                "text": receipt_text(
                    str(receipt.get("kind") or ""),
                    str(receipt.get("requestKind") or ""),
                ),
                "createdAt": completed["createdAt"],
                "viewType": "receipt",
                "view": receipt,
            }
            insert_at = len(restored)
            for index, item in enumerate(restored):
                if item.get("viewType") in {"draft", "confirmation"} and item.get("view", {}).get(
                    "kind"
                ) == receipt.get("kind"):
                    insert_at = (
                        index - 1 if index and restored[index - 1].get("role") == "user" else index
                    )
                    break
            restored.insert(insert_at, synthetic)
            identities.add(identity)


def _restorable_messages(messages) -> list:
    """Hide pending cards completed by a receipt while preserving repeat requests."""
    keep = [True] * len(messages)
    pending_by_kind: dict[str, list[int]] = {}
    for index, message in enumerate(messages):
        if message.role != "assistant" or message.view_type not in {
            "draft",
            "secure_input",
            "confirmation",
            "receipt",
        }:
            continue
        payload = _payload(message)
        kind = payload.get("kind")
        if not isinstance(kind, str) or not kind:
            continue
        if message.view_type in {"draft", "secure_input", "confirmation"}:
            pending_by_kind.setdefault(kind, []).append(index)
            continue
        for pending_index in pending_by_kind.pop(kind, []):
            keep[pending_index] = False
    return [message for index, message in enumerate(messages) if keep[index]]


def _payload(message) -> dict:
    try:
        payload = json.loads(message.view_payload_json or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _receipt_identity(receipt: dict) -> tuple:
    return receipt.get("kind"), receipt.get("reference"), receipt.get("status")
