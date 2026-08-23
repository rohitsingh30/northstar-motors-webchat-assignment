# Deterministic application routes

Routers recognize bounded, high-confidence application operations and return provider-neutral replies. Their
handler pipelines make priority explicit and avoid one large `if/elif` dispatcher.

## Files

| File | Responsibility |
| --- | --- |
| `__init__.py` | Export router types |
| [`base.py`](./base.py) | `ApplicationRouteHandler` protocol and `tool()` reply factory |
| [`support.py`](./support.py) | Offers, callbacks, messages, departments, business information, part exchange, sales enquiries, hours, dealerships |
| [`vehicle.py`](./vehicle.py) | Typed vehicle actions, comparison, interest, availability, details, test drives, search/refinement |
| [`workshop.py`](./workshop.py) | Existing-booking lookup, workshop locations, and explicit legacy service-selection actions |

## Rules

- Prefer trusted structured IDs over text reconstruction.
- Return a clarification when an entity cannot be resolved safely.
- Never invent catalogue facts in a plain response.
- Return a tool call for live facts and workflow preparation.
- Keep business permissions in application tools/workflows, not keyword matching.
