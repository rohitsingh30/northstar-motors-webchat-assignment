# Deterministic test provider

This package is a test fixture used only when `ENVIRONMENT=test` and no provider is explicitly
injected. It supplies predictable `ToolCall` values for unit/integration tests and shares the same
catalogue, policy, tools, state reducer, and workflow services.

Its keyword/regex routing is deliberately not feature-equivalent to natural conversation and is
never a development or production fallback. No hosted adapter or shared orchestration module may
import it.

| Path | Responsibility |
| --- | --- |
| `provider.py` | provider-test adapter |
| `planner.py` | deterministic test proposals |
| `routing/` | isolated fixture parsers and response helpers |
