# SQLite migrations

Migrations run in filename order during service startup. Applied filenames are recorded in
`schema_migrations`; existing migration files are therefore immutable.

## Files

| Migration | Change |
| --- | --- |
| [`001_initial.sql`](./001_initial.sql) | Create conversations, turns, messages, uniqueness, and message ordering index |
| [`002_conversation_sessions.sql`](./002_conversation_sessions.sql) | Add shared browser-session hash and session conversation index |
| [`002_workflows.sql`](./002_workflows.sql) | Add workflow drafts, operation attempts, idempotency/action uniqueness, verified booking grants |
| [`003_initial_page_context.sql`](./003_initial_page_context.sql) | Preserve the conversation's starting page separately from current page context |
| [`004_conversation_workflow_state.sql`](./004_conversation_workflow_state.sql) | Persist canonical deterministic workflow state on conversations |
| [`005_message_interactions.sql`](./005_message_interactions.sql) | Attach validated pending-interaction metadata to assistant messages |

The duplicate numeric prefix on the two `002` migrations is historical; full filenames are the
applied versions and sort deterministically. Do not rename them after use.

## Adding a migration

1. Choose the next unused numeric prefix.
2. Make the SQL safe for a database that has every previous migration.
3. Update repositories and tests.
4. Run the repeatability test in `tests/unit/test_database.py`.
5. Update `docs/LLD.md` and this table.
