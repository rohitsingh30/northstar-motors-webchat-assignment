# External integrations

| Path | Responsibility |
| --- | --- |
| `contracts.py` | Provider-neutral concrete calls, responses, reviewer context/provenance |
| `dealership.py` | Async platform reads/writes, image proxy, authentication, timeout/error mapping |
| `hosted_llm/` | Hosted transport, semantic candidate selection, proposal protocol, and independent-review protocol |
| `fake_llm/` | Deterministic development/test proposal implementation |

The hosted adapter package accepts the user-configured provider URL, one Bearer API key, and one model. It
does not contain provider-specific model IDs or Azure-only authentication. It retrieves candidate
tools/evidence, parses native calls, invokes the separate reviewer, and returns reviewed concrete
calls or pending-interaction decisions; it never executes business tools. Interaction decisions
carry no action arguments and are resolved from persisted application metadata by the orchestrator.

Only `DealershipClient` owns `X-API-Key` and platform idempotency headers. Tests inject HTTP clients
or gateways so integrations remain network-independent.
