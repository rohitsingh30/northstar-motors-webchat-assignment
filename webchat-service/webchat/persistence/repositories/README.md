# Repository aggregates

`webchat.persistence.repositories` is the stable facade. Implementations own SQL and return domain
or repository contracts rather than raw rows.

| File | Responsibility |
| --- | --- |
| `conversations.py` | session identity, lifecycle, context, versioned conversation/capability state |
| `messages.py` | transcript messages, structured segments/views, receipts |
| `turns.py` | turn idempotency and running/completed/failed state |
| `turn_commit.py` | atomic turn completion plus protected prompt/interaction/state transitions |
| `result_sets.py` | internal append-only writes for trusted normalized result envelopes |
| `interactions.py` | persisted protected interactions and supersession |
| `workflows.py` | drafts, operation attempts, receipts, verification grants |
| `common.py` | timestamps and session-token hashing |
