# Fake LLM integration

This package supplies deterministic development and test behaviour when a hosted model is not
configured. It implements the same validated V2 domain-goal `TurnPlan` boundary as Azure/OpenAI.

## Files

| File | Responsibility |
| --- | --- |
| `__init__.py` | Export the fake provider and deterministic planner |
| [`provider.py`](./provider.py) | Enforce exactly one schema-validated typed plan per provider turn |
| [`planner.py`](./planner.py) | Cover fake semantic cases, including contextual offer enquiries, and adapt confident application routes into canonical plans |

Every fake plan is validated by the same discriminated schema as hosted output. The planner may
consume the shared deterministic router. The online path does not depend on this fake-provider
package and consults deterministic routing only after a proposed `conversation.respond`.
Dynamic facts still come through the shared tool registry.
