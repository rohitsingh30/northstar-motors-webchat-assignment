# `webchat` runtime package

This package contains the entire deployable webchat application: configuration and composition,
HTTP API, domain workflows, external integrations, orchestration, persistence, observability, and
the browser widget.

## Direct files

| File | Responsibility |
| --- | --- |
| `__init__.py` | Marks the installable Python package |
| [`main.py`](./main.py) | Composition root, lifespan resources, middleware, routes, static widget mount, health endpoints |
| [`config.py`](./config.py) | Environment-backed typed settings and runtime provider validation |

`main.py` is the only place that should know most concrete classes. Feature modules should accept
focused collaborators rather than constructing repositories, providers, or HTTP clients directly.

## Subpackages

| Folder | Responsibility |
| --- | --- |
| [`api/`](./api/README.md) | Browser-facing schemas, authorization, routes, security, error mapping, restoration |
| [`domain/`](./domain/README.md) | Business semantics and write-workflow rules |
| [`integrations/`](./integrations/README.md) | Provider contracts, dealership HTTP, hosted and deterministic fake AI providers |
| [`observability/`](./observability/README.md) | Structured JSON logging and redaction |
| [`orchestration/`](./orchestration/README.md) | Context, planning, tools, turn execution, and response presentation |
| [`persistence/`](./persistence/README.md) | SQLite migrations and repositories |
| [`widget/`](./widget/README.md) | Service-hosted web component and ES modules |

## Startup flow

```text
Settings → logging → database migrations → repositories
         → DealershipClient → WorkflowService → ToolRegistry
         → provider selection → Orchestrator → FastAPI routes
```

Production requires a configured hosted provider. Development/test can fall back to the
deterministic fake provider, which emits the same validated domain-goal plan contract.
