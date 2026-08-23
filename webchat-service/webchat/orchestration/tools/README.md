# Orchestration tools

This package is the validated business-tool boundary. `ToolRegistry` dispatches an allow-listed
name to a focused capability handler; strict inputs and narrow gateway protocols keep each handler
independent of the concrete dealership client.

## Files

| File | Responsibility |
| --- | --- |
| `__init__.py` | Package marker |
| [`registry.py`](./registry.py) | Compose handler route maps, dispatch workflow tools, reject unknown/disallowed names |
| [`contracts.py`](./contracts.py) | Small shared `ToolExecutor` protocol used by actions, turns, and presentation |
| [`result.py`](./result.py) | Immutable `ToolResult` shared by every handler |
| [`inputs.py`](./inputs.py) | Strict Pydantic schemas for vehicle, filters, IDs, services, lookup forms, and part exchange |
| [`helpers.py`](./helpers.py) | Location normalization and conservative fuzzy matching against live dealership towns |
| [`business_information.py`](./business_information.py) | Resolve an exact customer question against described live business facts, with matched, ambiguous, or unavailable outcomes |
| [`service_resolution.py`](./service_resolution.py) | Resolve customer wording against the current live service catalogue as matched, ambiguous, or unsupported |
| [`actions.py`](./actions.py) | Typed widget-action route map for pagination, comparison, selection, and workflow starts |
| [`vehicles.py`](./vehicles.py) | Inventory search, page selection, facets, details, availability, model/ID comparison, safe vehicle views |
| [`catalog.py`](./catalog.py) | Offers, dealerships, departments, opening hours, and scoped business-information results |
| [`workshop.py`](./workshop.py) | Service information/listing, locations, live/future test-drive and workshop slots, alternatives |
| [`forms.py`](./forms.py) | Private lookup and estimate forms plus deterministic part-exchange estimate |
| [`workflows.py`](./workflows.py) | Map `prepare_*` tools to workflow kinds and enrich draft/confirmation payloads |

## Dispatch shape

```text
ToolRegistry.execute(name, arguments, conversation_id)
  ├── WorkflowToolHandler when name is a supported prepare_* tool
  └── routes[name] → capability handler → strict model validation → gateway call → ToolResult
```

Unknown tools raise `ValueError`. Workflow tools require a conversation ID and configured workflow
service. Read handlers do not know about repositories or AI providers.

## Tool groups

- **Vehicle:** search, page-scoped selection, live facets, distinct list/detail views, availability,
  comparisons.
- **Catalogue:** offers, dealership contacts/departments, opening hours, and fact-scoped business
  information that fails closed when the platform has no relevant fact.
- **Workshop:** live service resolution, locations, availability and slot selection.
- **Forms:** application-owned collection surfaces and read-only indicative estimate.
- **Workflow preparation:** collecting/confirmation drafts only; no confirmation executor is
  provider-callable.

## Adding a tool

1. Decide whether it is a read/form tool or workflow preparation.
2. Add a strict input model; do not accept unbounded dictionaries from the provider.
3. Add a focused handler method and route-map entry.
4. Return a closed `ToolResult`; keep raw platform payload only in `facts` when necessary.
5. Add the canonical domain-goal transition if the provider should reach it.
6. Add unit/integration tests and update HLD/LLD plus this catalogue.

Do not add a mutation confirmation method to this registry. Confirmed execution belongs to the API
and `domain.WorkflowService` boundary.
