# Persistence package

This package owns webchat SQLite schema evolution and repository access. SQL does not belong in API,
orchestration, widget, or integration modules.

## Files and folders

| Path | Responsibility |
| --- | --- |
| `__init__.py` | Package marker |
| [`database.py`](./database.py) | SQLite connections, foreign keys, busy timeout, WAL migrations, `BEGIN IMMEDIATE` transactions |
| [`repositories.py`](./repositories.py) | Conversation/session, message, turn, draft, attempt, receipt, and verified-grant repositories |
| [`migrations/`](./migrations/README.md) | Ordered immutable SQL schema migrations |

## Repository ownership

| Repository | Owns |
| --- | --- |
| `ConversationRepository` | Session hashes, conversation lifecycle/listing, initial/current context, workflow state, expiry |
| `MessageRepository` | Ordered messages, per-turn reads, draft-card replacement with receipt |
| `TurnRepository` | Client-message deduplication and running/completed/failed state |
| `WorkflowRepository` | Draft replacement/status, operation attempts, idempotency keys, receipts, verified grants |

## Rules

- Use parameterized SQL for values.
- Use repository transactions for state transitions that must be atomic.
- Persist idempotency material before external creation calls.
- Never expose raw repository rows directly to the browser.
- Add a new migration instead of rewriting an applied migration.
- Keep migration files included in `pyproject.toml` package data.
