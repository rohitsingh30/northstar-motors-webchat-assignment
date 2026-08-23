# Offline provider

This package supplies repeatable development/test behavior when hosted configuration is absent.
It emits the same concrete `ToolCall` boundary as hosted mode, then shares policy, catalogue,
application tools, workflow state, drafts, and rendering.

| Path | Responsibility |
| --- | --- |
| `provider.py` | Thin provider adapter |
| `planner.py` | Offline-only semantic rules and direct tool proposals |
| `routing/` | Normalized context, parsers, focused deterministic routes, trusted fact responses |

The fake provider does not simulate independent hosted review because its output is deterministic.
No hosted adapter, shared policy, or provider loop may import this package. Offline keyword/regex
rules are test fixtures for local parity, not production routing architecture.

For repeatable interaction tests, the fake recognizes only the canonical exact replies `yes` and
`no` when application-owned pending interaction metadata exists. This is an offline protocol, not
a production language classifier. Hosted mode interprets arbitrary natural-language acceptance or
rejection with the planner and validates it with the independent reviewer.
