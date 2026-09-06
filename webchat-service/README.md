# Northstar webchat service

This FastAPI service owns the conversational AI boundary, deterministic tool policy, grounding,
webchat state, protected workflows, and the browser widget. The supplied dealership API remains
the authoritative source for inventory, dealerships, workshop data, offers, and confirmed writes.

## Production turn architecture

```text
message -> hosted AI TurnUnderstanding -> semantic validation + legal-affordance preflight
        -> hosted AI capability plan -> deterministic policy -> Northstar tools
        -> normalized facts + response obligations -> hosted AI composition
        -> deterministic trust grounding + trusted views -> atomic turn commit -> widget
```

The same configured provider/model owns three bounded phases: semantic turn resolution, capability
planning, and grounded response composition. The planner must select tools compatible with the
already validated turn meaning. It may choose reads and draft-preparation tools. It cannot perform a confirmed mutation,
inject protected customer data, or invent an executable URL. Provider-facing schemas expose only
public fields; policy resolves trusted entity and appointment metadata before runtime validation.

The same configured provider/model composes the final response from fact references returned by
the tools. Grounding resolves those references into safe text/link segments. It hard-fails only
trust-boundary violations such as invented business values, private data, stale references,
untrusted destinations, or invalid protected actions. Conversational quality requirements guide
composition and evaluation; they never discard a successful dealership result. Trusted cards and
collections are attached deterministically. Interactive choice and clarification collections own
their item presentation: duplicate AI item blocks are pruned across the turn and the collection is
attached once to its question. Informational collections may instead use one complete AI-rendered
list. There is no independent AI reviewer in the production path and no deterministic public
keyword router for business capabilities. Latest-only protected decisions and immediate approved
vehicle-hazard guidance remain deliberately narrow deterministic safety boundaries.

Policy never emits transcript prose. Typed public choices and clarifications are AI-owned; a failed
precondition returns a bounded reason to planning. Trusted chips can carry typed actions, but their
tool results still pass through AI composition and grounding.

## Run locally

From the repository root:

```bash
cp .env.example .env
# Set LLM_PROVIDER_URL, LLM_API_KEY, and LLM_MODEL.
docker compose up --build -d
```

Open `http://localhost:4173`. Runtime services use ports `4010` (dealership), `4020` (webchat), and
`4173` (website). Development and production fail startup when the hosted provider configuration
is absent or incomplete. The fake provider is available only when `ENVIRONMENT=test`; a hosted
failure never falls back to it.

Optional MCP configuration is read-only:

```dotenv
MCP_SERVERS_JSON=[{"name":"crm","url":"https://mcp.example/tools","headers":{"Authorization":"Bearer ..."}}]
```

Only MCP tools explicitly annotated read-only are planner-visible.

## State, privacy, and writes

- SQLite `state_json` stores versioned public conversation state; `agentWorkflow` owns capability
  continuation/interruptions/pauses, `dialogue.activeQuestion` owns ordinary questions, and sibling
  `latestResultSets`, `agenda`, and `pendingInteraction` members own trusted result references,
  turn progress, and the latest protected action.
- Normal successful turns use one optimistic compare-and-swap transaction for assistant messages,
  normalized result sets, dialogue/workflow state, any interaction transition, and turn completion.
- Private workflow answers remain in per-conversation `sessionStorage` until one strict protected
  endpoint receives the completed group. They do not enter `/turns`, AI context, transcript, or logs.
- Same-origin JavaScript can read session storage; deploy only trusted scripts on the widget origin.
- Confirmation/cancellation and vehicle navigation are protected, latest-interaction-only,
  deterministic boundaries with atomic state transitions and idempotent server execution.
- Normal responses require hosted composition. After an idempotent write has already succeeded,
  composer failure falls back only to the deterministic public receipt so success is not misreported.
- Card payloads are visual-only. Vehicle preview cards add one application-owned “View vehicle”
  control that derives a same-site modal target from the trusted vehicle ID; conversational choices
  and workflow actions remain in chips outside cards.
- Broad appointment results stay hidden as trusted context until the AI has a day/date and
  approximate time; one selected slot may then be shown as a read-only summary.

## Focused verification

```bash
.venv/bin/ruff check webchat-service/webchat
PYTHONPYCACHEPREFIX=/tmp/northstar-pycache python3 -m compileall -q webchat-service/webchat
node --test \
  webchat-service/tests/browser/test_conversational_workflows.mjs \
  webchat-service/tests/browser/test_read_only_cards.mjs \
  webchat-service/tests/browser/test_widget_state.mjs
```

Run the complete deterministic suite with the repository test image:

```bash
docker build --target test -t northstar-webchat-test ./webchat-service
docker run --rm northstar-webchat-test
```

Configured-model browser suites require the running Compose stack and valid provider credentials;
test discovery is not a passing result.

## Ownership

| Path | Responsibility |
| --- | --- |
| `webchat/api` | HTTP authorization, strict public/protected contracts, restoration |
| `webchat/domain` | capabilities, state contracts, protected interactions, workflow writes |
| `webchat/integrations/hosted_llm` | provider transport plus turn-resolution, planning, and grounded-composition protocols |
| `webchat/orchestration` | context, policy, tools, state reduction, fact normalization, grounding |
| `webchat/persistence` | migrations and repositories for durable webchat state |
| `webchat/widget` | render/controller shell and protected input capture |
| `tests` | focused policy, integration, module, security, and acceptance coverage |
| `pyproject.toml` | package metadata, runtime/test dependencies, and Ruff/Pytest configuration |
| `Dockerfile` | production image and embedding-preloaded deterministic test target |
