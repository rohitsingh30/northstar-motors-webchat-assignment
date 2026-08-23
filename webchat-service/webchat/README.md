# `webchat` runtime package

This is the complete deployable service: API, workflows, external adapters, semantic orchestration,
persistence, observability, and the hosted widget.

## Direct files

| File | Responsibility |
| --- | --- |
| `__init__.py` | Installable-package marker |
| `main.py` | Composition root, lifespan resources, middleware, routes, widget mount, health endpoints |
| `config.py` | Typed environment settings, complete hosted-provider validation, MCP configuration parsing |

## Subpackages

| Folder | Responsibility |
| --- | --- |
| `api/` | Browser schemas, routes, authorization, error mapping, restoration |
| `domain/` | Write-draft validation, confirmation, and public receipts |
| `integrations/` | Dealership, hosted LLM, and offline-provider adapters |
| `orchestration/` | Context, retrieval, review, policy, catalogue, tools, state, and presentation |
| `persistence/` | SQLite migrations and repositories |
| `observability/` | Structured logging and redaction |
| `widget/` | Web component, transport, cards, forms, styles, and accessibility |

## Startup flow

```text
Settings → logging → SQLite/repositories → dealership/workflow services
         → unified tool catalogue + optional MCP discovery
         → hosted semantic retriever/provider OR offline provider
         → Orchestrator → FastAPI routes
```

Concrete infrastructure is wired only in `main.py`. Feature modules consume focused contracts.
Production requires one complete provider URL/key/model configuration; development and tests may
use the isolated deterministic provider.

Within a layer, a concern remains a module until it genuinely needs several cohesive files. Public
facades keep imports stable for multi-file subsystems such as hosted LLM integration, repositories,
and widget renderers.
