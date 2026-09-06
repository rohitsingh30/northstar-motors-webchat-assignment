# External integrations

| Path | Responsibility |
| --- | --- |
| `contracts.py` | provider-neutral semantic, planning, and composition contracts and concrete calls |
| `dealership.py` | authoritative platform reads/writes, authentication, timeouts, errors |
| `hosted_llm/` | Responses-compatible turn-resolution, planning, and grounded-composition adapter |
| `fake_llm/` | deterministic test fixture only |

The hosted adapter asks the configured AI to resolve one typed `TurnUnderstanding`, retrieves and
preflights relevant public tools/evidence, asks the same model to plan, and later asks it to compose
from normalized result references. It does not execute tools or writes. A configured failure never
selects the fake provider. Before an operation completes it propagates as a retryable/validated
turn failure; after a confirmed write, orchestration may emit only the deterministic public receipt
so a real success is never reported as failure.

Only `DealershipClient` owns `X-API-Key` and platform idempotency headers. Integration tests inject
clients/gateways so normal tests remain network-independent.
