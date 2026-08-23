# Orchestration package

Orchestration connects persisted context, semantic understanding, deterministic transitions,
validated tools, and final presentation. The package is organized by responsibility so the turn
coordinator remains small.

## Direct files

| File | Responsibility |
| --- | --- |
| `__init__.py` | Package marker |
| [`orchestrator.py`](./orchestrator.py) | Per-conversation turn lock, deduplication, lifecycle, collaborator coordination, persistence boundary |

## Subpackages

| Folder | Responsibility |
| --- | --- |
| [`context/`](./context/README.md) | Build bounded model context and trusted entity/search references |
| [`planning/`](./planning/README.md) | Versioned domain-goal ontology, typed plan schema, policy, state, and exact tool transitions |
| [`presentation/`](./presentation/README.md) | Final response/view selection and suggestions |
| [`routing/`](./routing/README.md) | Post-provider conversation-response safety routing, bounded parsers, focused routes, and fake-planner support |
| [`tools/`](./tools/README.md) | Strict inputs, registry, capability handlers, forms, workflows, shared result |
| [`turns/`](./turns/README.md) | Bounded provider/tool loop and semantic-plan policy enforcement |

## Flow

```text
Orchestrator
  → ConversationHistoryBuilder
  → StructuredActionHandler OR configured provider
  → schema-validated domain + goal plan
  → SemanticPlanPolicy
  → TransitionController
  → ToolRegistry
  → BusinessInformationResolver for business-fact questions
  → ProviderToolLoop / terminal ToolResult
  → ResponsePresenter
  → message and turn repositories
```

## Dependency rules

- `orchestrator.py` coordinates; it should not accumulate parsing or card-building logic.
- `planning.ontology` is the sole owner of domain-goal identifiers and legacy state upgrades.
- `planning` may choose application tool names but must not call the dealership.
- `tools` may call focused gateway protocols but must not call AI providers.
- `presentation` consumes replies/results but must not mutate workflow or repository state.
- `context` reads persisted state but performs no network or provider work.
