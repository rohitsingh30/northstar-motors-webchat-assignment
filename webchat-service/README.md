# Northstar webchat service

`webchat-service` is the server-side policy, persistence, AI orchestration, workflow, and widget
boundary for Northstar Motors. It serves the browser widget and `/api/chat/v1`, calls the supplied
dealership API, and stores webchat-owned state in SQLite.

## Architecture at a glance

In hosted mode, semantic retrieval selects relevant definitions from one unified application/MCP
tool catalogue plus documented knowledge. A user-supplied Responses-compatible provider proposes
concrete calls, and a separate stateless request accepts, corrects, clarifies, or rejects the
proposal. Both requests share the configured key and model; independence means separate requests,
prompts, schemas, and context. Only reviewed calls cross deterministic policy. Application code
validates references and arguments, performs reads, prepares drafts, and requires explicit
confirmation before any protected mutation.

## Run locally

From the repository root:

```bash
cp .env.example .env
docker compose up --build -d
```

Open `http://localhost:4173`. With no hosted-provider credentials, non-production environments use
the deterministic fake LLM provider.

Optional read-only MCP servers join the same catalogue. Configure a JSON array; headers remain
server-side:

```dotenv
MCP_SERVERS_JSON=[{"name":"crm","url":"https://mcp.example/tools","headers":{"Authorization":"Bearer ..."}}]
```

Only remote tools that advertise the MCP read-only annotation are model-visible. Remote mutations
remain disabled until they have an application-owned confirmation workflow.

## Verify

```bash
docker build --target test -t northstar-webchat-test:refactor ./webchat-service
docker run --rm northstar-webchat-test:refactor
docker run --rm northstar-webchat-test:refactor ruff check webchat tests
```

Also run JavaScript syntax checks after widget changes:

```bash
for file in $(find webchat-service/webchat/widget -name '*.js'); do
  node --check "$file" || exit 1
done
node --test webchat-service/tests/browser/*.mjs
```

## Top-level files and folders

| Path | Purpose |
| --- | --- |
| [`Dockerfile`](./Dockerfile) | Cached dependency/model stage plus isolated test and runtime images; ordinary source changes do not reinstall native AI dependencies |
| [`pyproject.toml`](./pyproject.toml) | Package metadata, runtime/dev dependencies, nested widget assets, Pytest/Ruff settings |
| [`scripts/`](./scripts/README.md) | Repeatable generation of the packaged document knowledge index |
| [`webchat/`](./webchat/README.md) | Runtime Python package plus service-hosted widget |
| [`tests/`](./tests/README.md) | Unit, integration, contract, security, and health verification |

## Runtime boundaries

- Browser code never receives the dealership or AI API keys.
- Dynamic business facts come from `DealershipClient` calls.
- Specific business-information questions render only relevant platform facts; unsupported facts
  fail closed with a dealership-contact offer instead of a generic notice card.
- The model cannot confirm a write or choose an arbitrary HTTP endpoint.
- Hosted plans cannot reach a business tool without an independent review result.
- The compulsory reviewer shares the configured provider, key, and model but has a separate prompt,
  schema, request, and context; it receives no planner history or hidden reasoning.
- The reviewer selects one outcome-specific accept/correct/clarify/reject function. Invalid output
  receives one deterministic repair attempt; repeated failure becomes retryable
  `LLM_REVIEW_FAILED`, not a completed customer answer.
- Workflow contact details are stored server-side in a validated draft and hidden from confirmation
  summaries.
- Workshop lookup proof bypasses model/transcript storage and is excluded from saved browser forms.
- `dealership-platform` is an external authoritative dependency and must not be edited as part of
  webchat maintenance.

## Detailed design

- [Integration guide](../docs/INTEGRATION-GUIDE.md)
- [Business semantics](../docs/BUSINESS-SEMANTICS.md)
- [Seeded scenarios](../docs/SEEDED-SCENARIOS.md)

## Where changes belong

| Change | Owner |
| --- | --- |
| HTTP route or browser request schema | `webchat/api` |
| Workflow fields, confirmation, or receipts | `webchat/domain` and `webchat/persistence` |
| Hosted provider HTTP lifecycle | `webchat/integrations/hosted_llm/provider.py` |
| Hosted proposal/reviewer protocols | `webchat/integrations/hosted_llm/protocol.py` and `review.py` |
| Offline-only deterministic language routing | `webchat/integrations/fake_llm/routing` |
| Unified local/MCP tool metadata and dispatch | `webchat/orchestration/catalogue` |
| Semantic tool and knowledge retrieval | `webchat/orchestration/retrieval` |
| Proposal/reviewer prompts and review schema | `webchat/orchestration/planning` |
| Concrete-call safety and reference grounding | `webchat/orchestration/policy.py` |
| Execution-derived follow-up state | `webchat/orchestration/state.py` |
| Dealership read presentation | `webchat/orchestration/tools` |
| Final response/suggestions | `webchat/orchestration/presentation` |
| Widget transport/context utilities | `webchat/widget/core` |
| Widget cards/forms | `webchat/widget/views` |

Keep module dependencies pointed inward through contracts; wire concrete implementations only in
`webchat/main.py`.
