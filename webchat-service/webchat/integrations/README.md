# Integrations package

This package isolates external systems and provider-specific formats from application orchestration.

## Files and folders

| Path | Responsibility |
| --- | --- |
| `__init__.py` | Package marker |
| [`contracts.py`](./contracts.py) | Provider-neutral `ToolCall`, `TurnPlan`, `ProviderReply`, and `LlmProvider` protocol |
| [`dealership.py`](./dealership.py) | Async platform adapter for public reads, protected writes, image proxying, timeout/error normalization |
| [`openai_provider.py`](./openai_provider.py) | OpenAI/Azure Responses API request conversion and typed turn-plan parsing |
| [`fake_llm/`](./fake_llm/README.md) | Fake provider adapter and deterministic typed planner for development/tests |

## Dependency rules

- Callers depend on contracts, not OpenAI/Azure response JSON.
- Only `DealershipClient` attaches `X-API-Key` or `Idempotency-Key`.
- Provider adapters return semantic plans/replies; they do not execute application tools.
- Hosted and fake providers both return a schema-validated `TurnPlan`; orchestration owns the same
  gate, transition controller, tools, and presentation after that boundary.
- Provider-independent deterministic routing lives under `orchestration/routing`, not integrations.
- Inject `httpx.AsyncClient` in tests to avoid network access.

## Current limitations

The dealership adapter normalizes timeouts and HTTP errors but does not currently retry or
reconcile ambiguous PATCH/DELETE outcomes.
