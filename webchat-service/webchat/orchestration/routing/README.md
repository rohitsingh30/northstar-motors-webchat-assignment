# Deterministic application routing

This package contains provider-independent, high-confidence fallback routing used after an online
provider proposes `conversation.respond`, plus deterministic support for the fake provider. It is
application orchestration: it does not call Azure/OpenAI and it does not own business data.

## Files and folders

| Path | Responsibility |
| --- | --- |
| `__init__.py` | Export the application router and focused route types |
| [`router.py`](./router.py) | Compose deterministic routes and tool-result follow-ups without a conversational fallback |
| [`plan_adapter.py`](./plan_adapter.py) | Convert deterministic fallback tool routes into schema-validated V2 plans before transition conformance |
| [`context.py`](./context.py) | Normalize bounded conversation history and trusted application identifiers |
| [`parsers.py`](./parsers.py) | Parse bounded vehicle, price, location, workshop, contact, day, slot, and comparison inputs |
| [`responses.py`](./responses.py) | Convert authoritative tool facts into follow-up calls or concise introductions |
| [`routes/`](./routes/README.md) | Focused support, workshop, and vehicle application routes |

## Runtime role

`DeterministicApplicationRouter.route()` identifies a tool-shaped route only when application code
can identify the operation confidently. `DeterministicPlanRouter` immediately adapts that internal
route to the same schema-validated V2 plan emitted by hosted and fake providers. The plan then passes
through `TransitionController`; fallback routing cannot execute a tool around goal conformance.

Online natural language always reaches the configured semantic provider first. The semantic plan
policy consults this plan router only after that provider proposes plain prose for a supported
business request.

Returning `None` allows the proposed conversation response or a bounded semantic re-plan. This keeps
terminology questions and ambiguous language out of deterministic fallback routing.

## Dependency rules

- Routing may select provider-neutral tool names but never executes tools; safety fallbacks re-enter
  the typed plan and transition pipeline.
- Routing never calls provider or dealership integrations.
- Live facts always come from the tool registry and dealership client.
- Generic conversation belongs to a semantic provider, not this package.
