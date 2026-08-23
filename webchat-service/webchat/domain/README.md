# Domain package

This package contains application-owned business semantics and the deterministic workflow safety
boundary. It does not parse HTTP requests or render browser components.

## Files

| File | Responsibility |
| --- | --- |
| `__init__.py` | Package marker |
| [`models.py`](./models.py) | Immutable persisted `Message` and `Turn` records |
| [`business_semantics.py`](./business_semantics.py) | Exact GBP and vehicle-availability wording |
| [`workflows.py`](./workflows.py) | Required fields, material hashing, draft preparation, confirmation execution, public receipts, verified booking lookup/mutation |

## Workflow invariants

- Unsupported workflow kinds fail closed.
- Empty values do not satisfy required fields.
- Contact details are not echoed in confirmation summaries.
- A complete draft is persisted before confirmation can be offered.
- Confirmation executes stored fields, not browser/model overrides.
- Creation operations use the persisted idempotency key.
- Reserved-vehicle interest rechecks live availability.
- Workshop amend/cancel obtains the platform record ID only from a valid verified grant.

When adding a workflow kind, update required fields, preparation-tool mapping, execution dispatch,
receipt text/rendering, tests, and HLD/LLD together.
