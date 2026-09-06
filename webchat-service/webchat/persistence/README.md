# Persistence package

SQLite is the durable webchat store. Migrations run on startup; SQL remains inside repositories.
`database.py` owns connection setup, transaction scope, migration execution, and SQLite pragmas.

| Repository | Owns |
| --- | --- |
| `ConversationRepository` | sessions, lifecycle, page context, authoritative `state_json`, legacy workflow mirror |
| `MessageRepository` | ordered messages, grounded segments/views, receipt replacement |
| `TurnRepository` | client-message admission and execution status |
| `TurnCommitRepository` | atomic final turns, trusted result envelopes, and protected prompt/interaction/state transitions |
| `ProtectedInteractionRepository` | latest confirmation/navigation interaction, status, supersession |
| `WorkflowRepository` | drafts, idempotent operation attempts, receipts, verified booking grants |

`state_json.agentWorkflow` is the public workflow authority. Sibling state members own the latest
result-set references, agenda, and protected pending interaction. `workflow_state_json` remains a
compatibility mirror/fallback for existing databases. Unsubmitted protected browser answers never
enter SQLite.

Use parameterized SQL, repository transactions for atomic state changes, and new immutable
migrations rather than editing an applied migration.
