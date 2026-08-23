from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from webchat.domain.models import Message, Turn

from .database import Database


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def token_hash(token: str) -> bytes:
    return sha256(token.encode("utf-8")).digest()


class ConversationRepository:
    def __init__(self, database: Database):
        self.database = database

    def create(self, token: str, page_context: dict[str, object], retention_days: int) -> dict:
        conversation_id = str(uuid.uuid4())
        created_at = utc_now()
        expires_at = (
            datetime.now(UTC) + timedelta(days=retention_days)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO conversations "
                "(id, token_hash, session_hash, status, initial_page_context_json, "
                "last_page_context_json, created_at, updated_at, expires_at) "
                "VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    uuid.uuid4().bytes,
                    token_hash(token),
                    json.dumps(page_context, separators=(",", ":")),
                    json.dumps(page_context, separators=(",", ":")),
                    created_at,
                    created_at,
                    expires_at,
                ),
            )
        return {"id": conversation_id, "created_at": created_at}

    def authorize(self, conversation_id: str, token: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT token_hash, session_hash, status, expires_at FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        return bool(
            row
            and row["status"] == "active"
            and row["expires_at"] > utc_now()
            and (row["session_hash"] == token_hash(token) or row["token_hash"] == token_hash(token))
        )

    def has_session(self, token: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM conversations WHERE session_hash = ? AND expires_at > ? LIMIT 1",
                (token_hash(token), utc_now()),
            ).fetchone()
        return bool(row)

    def list_for_session(self, token: str) -> list[dict]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT c.id, c.created_at, c.updated_at, COUNT(m.id) AS message_count, "
                "COALESCE((SELECT text FROM messages first_message "
                "WHERE first_message.conversation_id = c.id AND first_message.role = 'user' "
                "ORDER BY first_message.sequence LIMIT 1), 'New conversation') AS title "
                "FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id "
                "WHERE c.session_hash = ? AND c.status = 'active' AND c.expires_at > ? "
                "GROUP BY c.id ORDER BY c.updated_at DESC, c.rowid DESC",
                (token_hash(token), utc_now()),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete(self, conversation_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE conversations SET status = 'deleted', token_hash = ?, updated_at = ? "
                "WHERE id = ?",
                (uuid.uuid4().bytes, utc_now(), conversation_id),
            )

    def update_context(self, conversation_id: str, page_context: dict[str, object]) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE conversations SET last_page_context_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(page_context, separators=(",", ":")), utc_now(), conversation_id),
            )

    def get_context(self, conversation_id: str) -> dict[str, object]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT last_page_context_json FROM conversations WHERE id = ? AND status = 'active'",
                (conversation_id,),
            ).fetchone()
        return json.loads(row["last_page_context_json"]) if row else {}

    def get_contexts(self, conversation_id: str) -> dict[str, dict[str, object]]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT initial_page_context_json, last_page_context_json "
                "FROM conversations WHERE id = ? AND status = 'active'",
                (conversation_id,),
            ).fetchone()
        if not row:
            return {"initial": {}, "current": {}}
        current = json.loads(row["last_page_context_json"])
        initial = json.loads(row["initial_page_context_json"] or row["last_page_context_json"])
        return {"initial": initial, "current": current}

    def get_workflow_state(self, conversation_id: str) -> dict[str, object]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT workflow_state_json FROM conversations "
                "WHERE id = ? AND status = 'active'",
                (conversation_id,),
            ).fetchone()
        if not row or not row["workflow_state_json"]:
            return {}
        value = json.loads(row["workflow_state_json"])
        return value if isinstance(value, dict) else {}

    def update_workflow_state(
        self, conversation_id: str, workflow_state: dict[str, object]
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE conversations SET workflow_state_json = ?, updated_at = ? "
                "WHERE id = ? AND status = 'active'",
                (
                    json.dumps(workflow_state, separators=(",", ":")),
                    utc_now(),
                    conversation_id,
                ),
            )

    def expire_old(self) -> int:
        with self.database.transaction() as connection:
            changed = connection.execute(
                "UPDATE conversations SET status = 'expired', token_hash = randomblob(32), updated_at = ? "
                "WHERE status = 'active' AND expires_at <= ?",
                (utc_now(), utc_now()),
            ).rowcount
        return changed


class MessageRepository:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _from_row(row) -> Message:
        return Message(**dict(row))

    def list(self, conversation_id: str) -> list[Message]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY sequence",
                (conversation_id,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_for_turn(self, turn_id: str) -> list[Message]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE turn_id = ? ORDER BY sequence", (turn_id,)
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def add(
        self,
        conversation_id: str,
        role: str,
        text: str,
        turn_id: str | None,
        view_type: str | None = None,
        view_payload_json: str | None = None,
    ) -> Message:
        message_id = str(uuid.uuid4())
        created_at = utc_now()
        with self.database.transaction() as connection:
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO messages "
                "(id, conversation_id, turn_id, sequence, role, text, view_type, "
                "view_payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message_id,
                    conversation_id,
                    turn_id,
                    sequence,
                    role,
                    text,
                    view_type,
                    view_payload_json,
                    created_at,
                ),
            )
        return Message(
            message_id,
            conversation_id,
            turn_id,
            sequence,
            role,
            text,
            view_type,
            view_payload_json,
            created_at,
        )

    def replace_draft_with_receipt(
        self,
        conversation_id: str,
        draft_id: str,
        text: str,
        receipt: dict,
    ) -> bool:
        """Replace the persisted draft card so restored chats cannot revive completed forms."""
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT id, view_payload_json FROM messages "
                "WHERE conversation_id = ? AND view_type IN ('draft', 'confirmation') "
                "ORDER BY sequence DESC",
                (conversation_id,),
            ).fetchall()
            fallback_row = None
            for row in rows:
                try:
                    payload = json.loads(row["view_payload_json"] or "{}")
                except (TypeError, ValueError):
                    continue
                if payload.get("draftId") == draft_id:
                    fallback_row = row
                    break
                if fallback_row is None and payload.get("kind") == receipt.get("kind"):
                    fallback_row = row
            if fallback_row is not None:
                connection.execute(
                    "UPDATE messages SET text = ?, view_type = 'receipt', view_payload_json = ? "
                    "WHERE id = ?",
                    (text, json.dumps(receipt, separators=(",", ":")), fallback_row["id"]),
                )
                return True
        return False


class TurnRepository:
    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _from_row(row) -> Turn:
        return Turn(**dict(row))

    def get_by_client_id(self, conversation_id: str, client_message_id: str) -> Turn | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM turns WHERE conversation_id = ? AND client_message_id = ?",
                (conversation_id, client_message_id),
            ).fetchone()
        return self._from_row(row) if row else None

    def create(self, conversation_id: str, client_message_id: str) -> Turn:
        turn = Turn(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            client_message_id=client_message_id,
            status="running",
            correlation_id=str(uuid.uuid4()),
            error_category=None,
            started_at=utc_now(),
            completed_at=None,
        )
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO turns "
                "(id, conversation_id, client_message_id, status, correlation_id, started_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    turn.id,
                    turn.conversation_id,
                    turn.client_message_id,
                    turn.status,
                    turn.correlation_id,
                    turn.started_at,
                ),
            )
        return turn

    def finish(self, turn_id: str, status: str, error_category: str | None = None) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE turns SET status = ?, error_category = ?, completed_at = ? WHERE id = ?",
                (status, error_category, utc_now(), turn_id),
            )


class WorkflowRepository:
    def __init__(self, database: Database):
        self.database = database

    def create_or_replace(
        self,
        conversation_id: str,
        kind: str,
        fields: dict,
        fields_hash: str,
        status: str,
    ) -> dict:
        draft_id = str(uuid.uuid4())
        timestamp = utc_now()
        expires_at = (datetime.now(UTC) + timedelta(hours=24)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE workflow_drafts SET status = 'cancelled', updated_at = ? "
                "WHERE conversation_id = ? AND kind = ? AND status IN ('collecting', 'awaiting_confirmation')",
                (timestamp, conversation_id, kind),
            )
            connection.execute(
                "INSERT INTO workflow_drafts "
                "(id, conversation_id, kind, version, status, fields_json, material_hash, "
                "created_at, updated_at, expires_at) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
                (
                    draft_id,
                    conversation_id,
                    kind,
                    status,
                    json.dumps(fields, sort_keys=True, separators=(",", ":")),
                    fields_hash,
                    timestamp,
                    timestamp,
                    expires_at,
                ),
            )
            row = connection.execute("SELECT * FROM workflow_drafts WHERE id = ?", (draft_id,)).fetchone()
        return dict(row)

    def statuses(self, conversation_id: str, draft_ids: list[str]) -> dict[str, str]:
        if not draft_ids:
            return {}
        placeholders = ",".join("?" for _ in draft_ids)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT id, status FROM workflow_drafts "
                f"WHERE conversation_id = ? AND id IN ({placeholders})",
                (conversation_id, *draft_ids),
            ).fetchall()
        return {str(row["id"]): str(row["status"]) for row in rows}

    def succeeded_receipts(self, conversation_id: str) -> list[dict]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, kind, result_json, updated_at FROM workflow_drafts "
                "WHERE conversation_id = ? AND status = 'succeeded' "
                "AND result_json IS NOT NULL ORDER BY updated_at, created_at",
                (conversation_id,),
            ).fetchall()
        receipts = []
        for row in rows:
            try:
                result = json.loads(row["result_json"] or "{}")
            except (TypeError, ValueError):
                continue
            if not isinstance(result, dict):
                continue
            result.setdefault("kind", row["kind"])
            receipts.append(
                {
                    "draftId": row["id"],
                    "createdAt": row["updated_at"],
                    "receipt": result,
                }
            )
        return receipts

    def begin_confirmation(self, conversation_id: str, draft_id: str, client_action_id: str) -> dict:
        with self.database.transaction() as connection:
            replay = connection.execute(
                "SELECT * FROM operation_attempts WHERE conversation_id = ? AND client_action_id = ?",
                (conversation_id, client_action_id),
            ).fetchone()
            if replay:
                result = dict(replay)
                draft = connection.execute(
                    "SELECT * FROM workflow_drafts WHERE id = ? AND conversation_id = ?",
                    (result["draft_id"], conversation_id),
                ).fetchone()
                result["draft"] = dict(draft)
                if (
                    result["state"] == "failed"
                    and bool(result.get("retryable"))
                    and draft["status"] == "awaiting_confirmation"
                    and draft["expires_at"] > utc_now()
                ):
                    timestamp = utc_now()
                    connection.execute(
                        "UPDATE operation_attempts SET state = 'prepared', error_code = NULL, "
                        "retryable = NULL, updated_at = ? WHERE id = ?",
                        (timestamp, result["id"]),
                    )
                    connection.execute(
                        "UPDATE workflow_drafts SET status = 'executing', updated_at = ? WHERE id = ?",
                        (timestamp, result["draft_id"]),
                    )
                    result.update(state="prepared", error_code=None, retryable=None)
                return result
            draft = connection.execute(
                "SELECT * FROM workflow_drafts WHERE id = ? AND conversation_id = ?",
                (draft_id, conversation_id),
            ).fetchone()
            if not draft or draft["status"] != "awaiting_confirmation" or draft["expires_at"] <= utc_now():
                raise ValueError("Draft is not available for confirmation")
            retry = connection.execute(
                "SELECT * FROM operation_attempts WHERE draft_id = ? AND state = 'failed' "
                "AND retryable = 1 ORDER BY updated_at DESC LIMIT 1",
                (draft_id,),
            ).fetchone()
            if retry:
                timestamp = utc_now()
                connection.execute(
                    "UPDATE operation_attempts SET client_action_id = ?, state = 'prepared', "
                    "error_code = NULL, retryable = NULL, updated_at = ? WHERE id = ?",
                    (client_action_id, timestamp, retry["id"]),
                )
                connection.execute(
                    "UPDATE workflow_drafts SET status = 'executing', updated_at = ? WHERE id = ?",
                    (timestamp, draft_id),
                )
                return {
                    **dict(retry),
                    "client_action_id": client_action_id,
                    "state": "prepared",
                    "error_code": None,
                    "retryable": None,
                    "draft": dict(draft),
                }
            attempt_id = str(uuid.uuid4())
            idempotency_key = str(uuid.uuid4())
            connection.execute(
                "UPDATE workflow_drafts SET status = 'executing', updated_at = ? WHERE id = ?",
                (utc_now(), draft_id),
            )
            connection.execute(
                "INSERT INTO operation_attempts "
                "(id, draft_id, conversation_id, client_action_id, idempotency_key, request_fingerprint, "
                "state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'prepared', ?, ?)",
                (
                    attempt_id,
                    draft_id,
                    conversation_id,
                    client_action_id,
                    idempotency_key,
                    draft["material_hash"],
                    utc_now(),
                    utc_now(),
                ),
            )
        return {
            "id": attempt_id,
            "draft_id": draft_id,
            "state": "prepared",
            "idempotency_key": idempotency_key,
            "draft": dict(draft),
        }

    def succeed_attempt(self, attempt_id: str, draft_id: str, result: dict) -> None:
        result_json = json.dumps(result, separators=(",", ":"))
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE operation_attempts SET state = 'succeeded', result_json = ?, updated_at = ? WHERE id = ?",
                (result_json, utc_now(), attempt_id),
            )
            connection.execute(
                "UPDATE workflow_drafts SET status = 'succeeded', result_json = ?, updated_at = ? WHERE id = ?",
                (result_json, utc_now(), draft_id),
            )

    def fail_attempt(self, attempt_id: str, draft_id: str, code: str, retryable: bool) -> None:
        draft_status = "awaiting_confirmation" if retryable else "failed"
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE operation_attempts SET state = 'failed', error_code = ?, retryable = ?, updated_at = ? WHERE id = ?",
                (code, int(retryable), utc_now(), attempt_id),
            )
            connection.execute(
                "UPDATE workflow_drafts SET status = ?, updated_at = ? WHERE id = ?",
                (draft_status, utc_now(), draft_id),
            )

    def cancel(self, conversation_id: str, draft_id: str) -> dict:
        with self.database.transaction() as connection:
            draft = connection.execute(
                "SELECT id, kind FROM workflow_drafts WHERE id = ? AND conversation_id = ? ",
                (draft_id, conversation_id),
            ).fetchone()
            changed = connection.execute(
                "UPDATE workflow_drafts SET status = 'cancelled', updated_at = ? "
                "WHERE id = ? AND conversation_id = ? AND status IN ('collecting', 'awaiting_confirmation')",
                (utc_now(), draft_id, conversation_id),
            ).rowcount
        if not changed:
            raise ValueError("Draft cannot be cancelled")
        return dict(draft)

    def create_grant(
        self, conversation_id: str, booking_record_id: str, reference: str, snapshot: dict
    ) -> str:
        grant_id = str(uuid.uuid4())
        expires_at = (datetime.now(UTC) + timedelta(minutes=30)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO verified_booking_grants "
                "(id, conversation_id, booking_record_id, booking_reference, booking_snapshot_json, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (grant_id, conversation_id, booking_record_id, reference, json.dumps(snapshot), expires_at),
            )
        return grant_id

    def get_grant(self, conversation_id: str, grant_id: str) -> dict | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM verified_booking_grants WHERE id = ? AND conversation_id = ? "
                "AND revoked_at IS NULL AND expires_at > ?",
                (grant_id, conversation_id, utc_now()),
            ).fetchone()
        return dict(row) if row else None

    def latest_grant(self, conversation_id: str) -> dict | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM verified_booking_grants WHERE conversation_id = ? "
                "AND revoked_at IS NULL AND expires_at > ? ORDER BY expires_at DESC LIMIT 1",
                (conversation_id, utc_now()),
            ).fetchone()
        return dict(row) if row else None

    def revoke_grant(self, grant_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE verified_booking_grants SET revoked_at = ? WHERE id = ?",
                (utc_now(), grant_id),
            )
