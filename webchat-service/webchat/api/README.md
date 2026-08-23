# API package

The API package is the browser-to-service boundary. It validates untrusted requests, authorizes the
conversation session, invokes application services, and returns only public response contracts.

## Files

| File | Responsibility |
| --- | --- |
| `__init__.py` | Package marker |
| [`router.py`](./router.py) | Assemble the public `/api/chat/v1` router from focused route modules |
| [`conversations.py`](./conversations.py) | Conversation sessions, history restoration, deletion, vehicle images, and turns |
| [`enquiries.py`](./enquiries.py) | Test-drive, offer, part-exchange, callback, sales, interest, and message preparation |
| [`workshop.py`](./workshop.py) | Workshop slot selection, booking drafts, verified lookup, amendment, and cancellation preparation |
| [`drafts.py`](./drafts.py) | Generic protected-draft confirmation and cancellation lifecycle |
| [`dependencies.py`](./dependencies.py) | Shared conversation authorization, public tool-view execution, and receipt persistence |
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
2. Add the route to the capability-focused route module and authorize the conversation.
3. Delegate to orchestration/tools/workflows; do not call protected platform paths ad hoc.
4. Add integration/security tests and update `docs/LLD.md` plus `core/api.js` when browser-visible.
