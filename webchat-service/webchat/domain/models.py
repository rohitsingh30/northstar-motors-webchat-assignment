from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Message:
    id: str
    conversation_id: str
    turn_id: str | None
    sequence: int
    role: str
    text: str
    view_type: str | None
    view_payload_json: str | None
    created_at: str
    interaction_json: str | None = None


@dataclass(frozen=True)
class Turn:
    id: str
    conversation_id: str
    client_message_id: str
    status: str
    correlation_id: str
    error_category: str | None
    started_at: str
    completed_at: str | None
