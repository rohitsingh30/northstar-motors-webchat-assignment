"""Workflow drafts, operation attempts, receipts, and verified booking grants."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from ..database import Database
from .common import utc_now


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
        expires_at = (
            (datetime.now(UTC) + timedelta(hours=24))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
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
            row = connection.execute(
                "SELECT * FROM workflow_drafts WHERE id = ?", (draft_id,)
            ).fetchone()
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

    def workshop_proof_for_receipt(
        self, conversation_id: str, booking_reference: str
    ) -> dict[str, str] | None:
        """Recover proof only from a workshop booking created in this conversation."""
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT fields_json, result_json FROM workflow_drafts "
                "WHERE conversation_id = ? AND kind = 'workshop_booking' "
                "AND status = 'succeeded' AND result_json IS NOT NULL "
                "ORDER BY updated_at DESC",
                (conversation_id,),
            ).fetchall()
        for row in rows:
            try:
                result = json.loads(row["result_json"] or "{}")
                fields = json.loads(row["fields_json"] or "{}")
            except (TypeError, ValueError):
                continue
            if str(result.get("reference") or "").casefold() != booking_reference.casefold():
                continue
            proof = {
                "reference": booking_reference,
                "lastName": fields.get("lastName"),
                "registration": fields.get("registration"),
                "phone": fields.get("phone"),
            }
            if all(isinstance(value, str) and value for value in proof.values()):
                return proof
        return None

    def begin_confirmation(
        self,
        conversation_id: str,
        draft_id: str,
        client_action_id: str,
        expected_kind: str | None = None,
    ) -> dict:
        with self.database.transaction() as connection:
            replay = connection.execute(
                "SELECT * FROM operation_attempts WHERE conversation_id = ? AND client_action_id = ?",
                (conversation_id, client_action_id),
            ).fetchone()
            if replay:
                result = dict(replay)
                if result["draft_id"] != draft_id:
                    raise ValueError("Confirmation action does not match this draft")
                draft = connection.execute(
                    "SELECT * FROM workflow_drafts WHERE id = ? AND conversation_id = ?",
                    (result["draft_id"], conversation_id),
                ).fetchone()
                if expected_kind and draft["kind"] != expected_kind:
                    raise ValueError("Draft kind does not match this confirmation")
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
            if (
                not draft
                or draft["status"] != "awaiting_confirmation"
                or draft["expires_at"] <= utc_now()
            ):
                raise ValueError("Draft is not available for confirmation")
            if expected_kind and draft["kind"] != expected_kind:
                raise ValueError("Draft kind does not match this confirmation")
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
        expires_at = (
            (datetime.now(UTC) + timedelta(minutes=30))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO verified_booking_grants "
                "(id, conversation_id, booking_record_id, booking_reference, booking_snapshot_json, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    grant_id,
                    conversation_id,
                    booking_record_id,
                    reference,
                    json.dumps(snapshot),
                    expires_at,
                ),
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
