# Repository aggregates

`webchat.persistence.repositories` is the stable import facade. Implementations are grouped by the
state they own:

| File | Responsibility |
| --- | --- |
| `conversations.py` | Session identity, conversation lifecycle, page context, workflow state |
| `messages.py` | Ordered transcript messages and closed view replacement |
| `turns.py` | Idempotent turn execution state |
| `workflows.py` | Drafts, operation attempts, receipts, and verified booking grants |
| `common.py` | UTC timestamp and session-token hashing primitives |

Repository modules own SQL. API, orchestration, integration, and widget code consume repository
methods and never depend on raw tables.
