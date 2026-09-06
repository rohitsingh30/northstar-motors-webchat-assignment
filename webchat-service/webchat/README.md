# `webchat` runtime package

`webchat` is the deployable conversational service and hosted widget.

| Path | Responsibility |
| --- | --- |
| `main.py` | composition root, migration startup, adapters, repositories, routes, static widget |
| `config.py` | strict settings and hosted-provider/MCP validation |
| `api/` | browser boundary and protected endpoints |
| `domain/` | capability, conversation-state, workflow, and protected-action contracts |
| `integrations/` | dealership, hosted AI, and test-provider adapters |
| `orchestration/` | plan/policy/tool/fact/compose/ground turn lifecycle |
| `persistence/` | SQLite migrations and repositories |
| `observability/` | structured logging and redaction |
| `widget/` | web component, transcript/views, lifecycle, and protected capture |

Startup is:

```text
settings validation -> database migrations/repositories -> dealership/workflow services
 -> unified local/read-only-MCP catalogue -> hosted resolver/planner/composer + retrieval
 -> orchestrator -> API + static widget
```

Development and production require a complete hosted provider URL/key/model configuration. Only an
explicit test environment may construct `FakeLlmProvider`. Concrete infrastructure is wired in
`main.py`; feature modules depend on focused contracts.
