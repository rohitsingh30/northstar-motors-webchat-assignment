# SQLite migrations

Migrations run in filename order during startup and applied filenames are immutable.

| Migration | Change |
| --- | --- |
| `001_initial.sql` | conversations, turns, messages, ordering |
| `002_conversation_sessions.sql` | browser-session identity |
| `002_workflows.sql` | workflow drafts, attempts, idempotency, verification grants |
| `003_initial_page_context.sql` | initial/current page context split |
| `004_conversation_workflow_state.sql` | legacy workflow state column |
| `005_message_interactions.sql` | message interaction metadata |
| `006_conversation_state_results_interactions.sql` | authoritative state JSON, trusted result sets, protected interactions |
| `007_message_segments.sql` | grounded structured message segments |
| `008_turn_request_fingerprint.sql` | exact duplicate-request fingerprinting |
| `009_message_blocks.sql` | explicit grounded paragraph/list presentation blocks |

The two historical `002` filenames sort deterministically and must not be renamed. Add a new
numbered migration for every schema change and run the migration repeatability tests.
