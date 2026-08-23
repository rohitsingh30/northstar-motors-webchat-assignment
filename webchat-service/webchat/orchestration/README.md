# Orchestration

This package turns a persisted customer turn into a reviewed response or a validated tool result.
It has no domain/goal ontology and no online keyword intent router.

| Path | Responsibility |
| --- | --- |
| `orchestrator.py` | Per-conversation locking, idempotent turn lifecycle, collaborator coordination |
| `context.py` | Bounded provider/reviewer context facade |
| `references.py` | Trusted displayed/page entity extraction from closed payloads |
| `retrieval/` | Semantic tool-candidate and document-evidence retrieval |
| `planning/` | Planner/reviewer policies and strict review result |
| `policy.py` | Concrete-call risk, reference, and precondition enforcement |
| `catalogue/` | One application/MCP definition, validation, discovery, and dispatch boundary |
| `tools/` | Application-backed tool implementations and structured widget actions |
| `state.py` | Workflow context reduced from actual executions |
| `provider_loop.py` | Bounded reviewed provider/tool loop |
| `presentation/` | Closed renderer validation and application-owned suggestions |

```text
context → semantic retrieval → proposal → independent review → policy
        → unified catalogue → application/MCP executor → renderer → persistence
```

For a persisted assistant prompt, the proposal/reviewer may instead return an argument-free
accept/decline decision. The orchestrator resolves only the immediately preceding stored typed
interaction; no production phrase matcher selects the intent or action.

Typed widget actions enter at the catalogue because the UI already supplied the exact operation.
Evidence-only tool results return to context for another bounded provider iteration.

Dependency rules:

- `orchestrator.py` coordinates and does not parse customer language.
- `retrieval` ranks candidates but does not make a routing decision.
- `planning` proposes/reviews but never executes.
- `policy` validates concrete calls and never calls providers or gateways.
- `catalogue` owns metadata and dispatch; implementations live behind executors.
- `tools` call business gateways but never AI providers.
- `presentation` does not mutate workflows or repositories.

Single-stage concerns stay as modules. A subpackage is reserved for a genuine multi-file subsystem
such as the catalogue, retrieval, planning, presentation, or application tools.
