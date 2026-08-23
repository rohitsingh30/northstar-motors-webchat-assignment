# Offline deterministic routing

This package interprets language for `FakeLlmProvider` only.

| Path | Responsibility |
| --- | --- |
| `__init__.py` | Public offline router exports |
| `router.py` | Compose focused routes and trusted tool-result follow-ups |
| `context.py` | Normalize bounded history, version-3 workflow state, and trusted IDs |
| `parsers.py` | Parse bounded vehicle, price, location, workshop, contact, date, slot, and comparison values |
| `responses.py` | Turn authoritative tool facts into a direct call or concise response |
| `base.py` | Shared offline route protocol and typed reply helpers |
| `vehicle.py` | Vehicle discovery and follow-up rules |
| `workshop.py` | Workshop discovery and booking rules |
| `support.py` | Offers, dealership information, policy, and contact rules |

Routes propose tool names and arguments but never execute them. Live facts still come from the
unified catalogue. This package must remain physically isolated from hosted execution.
