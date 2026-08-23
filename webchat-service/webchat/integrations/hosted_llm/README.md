# Hosted LLM adapter

This package adapts a Responses-compatible provider to the service's provider-neutral contracts.
It proposes and reviews operations but never executes business tools.

| File | Responsibility |
| --- | --- |
| `provider.py` | HTTP transport and the two-request planner/reviewer lifecycle |
| `candidates.py` | Direct and follow-up-aware semantic candidate selection |
| `protocol.py` | Native function schemas, proposal parsing, and application-owned replies |
| `review.py` | Reviewer context projection, validation feedback, and repair constraints |
| `__init__.py` | Stable `HostedLlmProvider` facade |

Provider transport depends on the protocol modules; protocol modules do not perform HTTP requests.
No module in this package imports the offline `fake_llm` implementation.
