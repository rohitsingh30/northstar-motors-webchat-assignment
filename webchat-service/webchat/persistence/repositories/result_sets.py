"""Persistence for sensitivity-labelled trusted tool results."""

from __future__ import annotations

from webchat.orchestration.contracts.facts import ToolResultEnvelope

from ..database import Database
from .common import utc_now


class ResultSetRepository:
    def __init__(self, database: Database):
        self.database = database

    def add(
        self,
        conversation_id: str,
        turn_id: str,
        kind: str,
        envelope: ToolResultEnvelope,
        *,
        connection=None,
    ) -> None:
        values = (
            envelope.resultId,
            conversation_id,
            turn_id,
            kind,
            envelope.model_dump_json(),
            envelope.observedAt.isoformat(),
            envelope.expiresAt.isoformat() if envelope.expiresAt else None,
            utc_now(),
        )
        statement = (
            "INSERT INTO trusted_result_sets "
            "(id, conversation_id, turn_id, kind, envelope_json, observed_at, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )
        if connection is not None:
            connection.execute(statement, values)
            return
        with self.database.transaction() as owned:
            owned.execute(statement, values)
