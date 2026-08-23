# API package

The API package is the browser-to-service boundary. It validates untrusted requests, authorizes the
conversation session, invokes application services, and returns only public response contracts.

## Files

| File | Responsibility |
| --- | --- |
| `__init__.py` | Package marker |
| [`conversations.py`](./conversations.py) | `/api/chat/v1` routes for conversations, turns, options, drafts, confirmation, lookup, and workshop management |
| [`models.py`](./models.py) | Strict Pydantic request models, field limits, contact normalization, typed-action validation |
| [`errors.py`](./errors.py) | Central `DealershipError` → safe JSON error response mapping |
| [`restoration.py`](./restoration.py) | Reconcile persisted messages with current workflow statuses and missing receipts |
| [`security.py`](./security.py) | Body/content-type/origin/rate checks and security response headers |

## Boundary rules

- Every conversation route calls `_authorize` before accessing state.
- Request models reject unknown fields.
- Route handlers may coordinate application services but should not reproduce tool/business logic.
- Private booking proof is accepted only by the dedicated lookup endpoint.
- Confirmation accepts `clientActionId`; it never accepts replacement draft fields.
- Upstream errors are normalized once in `errors.py`.

## Adding an endpoint

1. Add or reuse a strict model in `models.py`.
2. Add a route in `conversations.py` and authorize the conversation.
3. Delegate to orchestration/tools/workflows; do not call protected platform paths ad hoc.
4. Add integration/security tests and update `docs/LLD.md` plus `core/api.js` when browser-visible.
