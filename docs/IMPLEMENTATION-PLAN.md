# Northstar Motors AI Webchat — Implementation Plan

This document breaks the PRD, HLD, and LLD into small tasks that a lower-capability coding model
can complete safely. The authoritative requirements remain:

- `docs/PRD.md` — required behaviour and acceptance scenarios;
- `docs/HLD.md` — architecture and security boundaries;
- `docs/LLD.md` — API, data, workflow, and test contracts.

If this plan conflicts with one of those documents, follow the PRD first, then the HLD, then the
LLD. Do not weaken a security rule to make an implementation task easier.

## Instructions for every implementation session

Give the model exactly one numbered task from this plan. Start each session with this instruction:

> Implement only Task N from `docs/IMPLEMENTATION-PLAN.md`. Read the task plus every referenced
> section of the PRD/HLD/LLD before editing. Inspect existing code first. Keep the implementation
> small and easy to follow. Add comments only where they explain a security rule, business rule,
> or non-obvious decision. Run the task's checks. Do not commit, push, open/modify a PR, or begin the
> next task. Report changed files, tests run, and any remaining issue.

The model must also follow these rules:

1. Never change `dealership-platform/`, its OpenAPI contract, or its seeded data.
2. Never put `NORTHSTAR_API_KEY` or `OPENAI_API_KEY` in browser code, HTML, logs, or committed files.
3. Never let an LLM directly perform a write. Writes require a stored draft and explicit user
   confirmation enforced by application code.
4. Never invent prices, availability, offers, opening hours, slots, statuses, or operation results.
5. Use platform-returned IDs for vehicles, dealerships, services, offers, and slots.
6. Use `textContent` and closed view schemas in the browser. Do not render arbitrary model HTML.
7. Preserve unrelated local changes. Do not use destructive Git commands.
8. Prefer clear functions and typed data over abstractions that are not yet needed.
9. Run the narrow tests for the task and `git diff --check`. Do not fix unrelated failures.
10. Stop and report a blocker if an API field or business rule is unclear; do not guess.

## Progress states

Use these markers without deleting task details:

- `[ ]` not started
- `[~]` implemented locally but not fully verified
- `[x]` implemented and its stated checks pass
- `[!]` blocked, with a short explanation beneath the task

## Phase 1 — Backend foundation

### [x] Task 1: Create the webchat service skeleton

Read: HLD sections 2, 4, and 11; LLD sections 1, 2, and 4.7.

Create only:

- `webchat-service/pyproject.toml` with Python 3.12, FastAPI, Uvicorn, HTTPX, Pydantic settings,
  pytest, pytest-asyncio, and development lint/format dependencies;
- `webchat-service/Dockerfile` and `.dockerignore`;
- `webchat-service/webchat/main.py` with an application factory;
- `webchat-service/webchat/config.py` with typed environment settings;
- `webchat-service/tests/test_health.py`;
- root `.env.example` with variable names and safe non-secret defaults;
- Compose wiring for an internal `webchat-service` on port 4020 with a named SQLite volume.

Required behaviour:

- `GET /health/live` returns process health.
- `GET /health/ready` reports configuration/database readiness without returning secrets.
- Startup fails with a clear configuration error when required production settings are absent.
- Tests can override settings without real API keys or network calls.

Checks:

```bash
cd webchat-service && python -m pytest tests/test_health.py
docker compose config
git diff --check
```

Stop after the health tests and Compose validation pass. Do not add conversations yet.

### [x] Task 2: Add structured logging and redaction

Read: HLD section 5.8; PRD sections 10 and 11; LLD section 15.

Create `webchat-service/webchat/observability/logging.py` and `redaction.py`, with unit tests.
Logs must be JSON and may contain correlation IDs, hashed conversation IDs, operation names,
durations, result categories, and sanitized error codes. They must remove API keys, authorization
cookies, message bodies, names, email addresses, phone numbers, registrations, and booking proof.

Checks:

```bash
cd webchat-service && python -m pytest tests/unit/test_redaction.py
git diff --check
```

### [x] Task 3: Add SQLite migrations and repositories

Read: HLD section 5.7; LLD sections 5 and 6.

Implement the schema described by the LLD using explicit numbered SQL migrations. Add a small
database wrapper that enables foreign keys, WAL mode, and a busy timeout. Add repositories only for
conversations, messages, and turns in this task. Use UTC timestamps and transactions. Store only a
hash of the conversation authorization token.

Tests must cover migration from an empty database, repeat startup, conversation creation, ordered
messages, unique `client_message_id`, cascade deletion, and absence of the raw token.

Checks:

```bash
cd webchat-service && python -m pytest tests/unit/test_database.py tests/unit/test_repositories.py
git diff --check
```

### [x] Task 4: Implement conversation lifecycle endpoints

Read: PRD FR-01, FR-02, FR-09, FR-10; LLD sections 4.1–4.3 and 13.

Implement only create, restore, and delete conversation endpoints. Add strict Pydantic request and
response models that reject unknown fields. Validate allow-listed page context and the vehicle ID
pattern `^veh-[0-9]{3}$`. Set a random `northstar_chat` HttpOnly, SameSite=Lax cookie and verify its
hash for later access. JavaScript must never receive the token.

Tests must cover valid create/restore/delete, missing or wrong cookie, invalid context, maximum
lengths, cookie flags, and safe deletion. Do not add the model turn loop yet.

Checks:

```bash
cd webchat-service && python -m pytest tests/integration/test_conversations_api.py
git diff --check
```

### [x] Task 5: Add a deterministic fake LLM and bounded orchestration shell

Read: HLD sections 5.3 and 5.6; LLD sections 7 and 12.

Define a small provider protocol and a deterministic fake provider. Implement the turn endpoint,
turn persistence, one-active-turn locking per conversation, and replay by `clientMessageId`. Bound
model iterations, tool calls, text length, and timeout. At this stage the only provider result is a
plain assistant text message; no dealership tools or writes are allowed.

Tests must prove completed-turn replay returns the stored result, concurrent duplicate submission
does not create duplicate messages, invalid model output is rejected safely, and a timeout leaves a
recoverable failed turn.

Checks:

```bash
cd webchat-service && python -m pytest tests/unit/test_orchestrator.py tests/integration/test_turns_api.py
git diff --check
```

## Phase 2 — Dealership read journeys

### [x] Task 6: Build the read-only dealership adapter

Read: `dealership-platform/openapi.json`; HLD section 5.5; LLD sections 8.1 and 11.

Create typed HTTPX adapter methods for:

- vehicles and availability;
- offers;
- dealerships and opening hours;
- service types, workshop locations, and workshop availability;
- test-drive availability;
- privacy, finance, and part-exchange notices exposed by the platform.

Centralize base URL, timeouts, response limits, relative asset URL normalization, and platform
error mapping. Read calls must not include `X-API-Key`. Do not implement writes in this task.

Contract tests must use representative payloads based on `openapi.json`; do not call the internet.

Checks:

```bash
cd webchat-service && python -m pytest tests/contract/test_dealership_reads.py
git diff --check
```

### [x] Task 7: Add read tool handlers and safe view models

Read: PRD FR-03, FR-04, FR-07, and FR-08; LLD sections 5.1, 8.1, and 10.1.

Implement the read tool registry around the adapter from Task 6. Use closed Pydantic view models
for text, vehicles, comparisons, offers, dealerships, hours, services, and slots. Preserve prior
vehicle search filters during refinement. Format pence as GBP and represent a null price as “Price
on request.” Construct local vehicle links only from validated vehicle IDs.

Tests must cover persistent/refined filters, null prices, comparisons with unknown values,
available/reserved/sold semantics, no-results behaviour, exact offer terms, holiday hours, and no
invented facts.

Checks:

```bash
cd webchat-service && python -m pytest tests/unit/test_read_tools.py tests/unit/test_business_semantics.py
git diff --check
```

### [x] Task 8: Connect read tools to the bounded orchestrator

Read: HLD sections 5.3 and 7; LLD sections 7, 8, and 12.

Allow the model to request only Task 7's read tools. Validate every tool payload before execution,
persist sanitized tool outcomes, and build the final response from safe facts/view models. The
orchestrator must reject unknown tools, write-like tools, oversized arguments, and excessive calls.
Use the fake provider for all automated tests.

Checks:

```bash
cd webchat-service && python -m pytest tests/unit/test_tool_policy.py tests/integration/test_read_turns.py
git diff --check
```

## Phase 3 — Website integration

### [x] Task 9: Expose the standalone widget and restricted browser API

Read: HLD sections 3 and 4; LLD section 4.

Serve the widget bundle from `webchat-service` and publish its local port. Permit credentialed
browser requests only from `WEBCHAT_ALLOWED_ORIGIN`; retain origin checks, body limits, safe
headers, and the HttpOnly conversation cookie. Leave the supplied dealership server and
application modules unchanged. Production deployments may use a same-origin edge route without
changing widget code.

Checks must cover widget asset delivery, allowed/disallowed origins, CORS preflight, credentials,
cookies, content type, and request-size enforcement.

### [x] Task 10: Add page-context collection and the chat API client

Read: PRD FR-02; LLD sections 3.1, 3.3, and 3.4.

Create the service-hosted widget's `webchat-context.js` and `webchat-api.js`. Context may contain
only path, allow-listed section, validated vehicle ID, and an allow-listed title. The widget must
derive URL context without modifying the host application. The API client must create, restore,
send, retry with the same client ID, and start a new conversation. Store only conversation ID and
non-sensitive UI preferences in local storage.

Add focused JavaScript tests if a test runner exists; otherwise add pure exported functions and a
small documented Node test command before continuing.

### [~] Task 11: Build the accessible chat shell

Read: PRD FR-01 and section 9; LLD sections 3.1 and 3.2.

Add the launcher, panel, transcript, composer, close/new-conversation actions, loading state,
retry state, unread badge, and live status announcement. Opening moves focus inside; closing
returns focus to the launcher. Prevent duplicate sends. Keep the composer reachable on mobile.
Use DOM creation and `textContent`; do not use model-provided `innerHTML`.

Manually verify with keyboard at desktop and mobile widths. Keep all widget source in
`webchat-service/webchat/widget` and isolate styling with a shadow root.

### [~] Task 12: Render typed cards and restore state

Read: LLD sections 3.1–3.4 and 5.1.

Render the closed view types produced in Task 7: vehicle, comparison, offer, dealership/hours,
service, slot, choice list, confirmation, receipt, and private lookup. Validate payloads again in
the browser and fall back to safe text on an unknown/invalid view. Restore transcript and pending
non-sensitive workflow state after refresh. Ensure vehicle links use `?vehicle={vehicleId}`.

Checks must cover malicious strings as text, invalid link IDs, unknown cards, refresh restore,
selected-vehicle context, and unread behaviour.

## Phase 4 — Confirmed writes

### [x] Task 13: Implement workflow drafts and confirmation policy

Read: PRD FR-05–FR-09; LLD sections 5.2, 5.3, 6, and 9.

Add repositories and domain code for `workflow_drafts` and `operation_attempts`. Implement required
fields for every operation kind, deterministic material-field hashing, versioning, expiry, edit
invalidation, cancel, and states from the LLD. A draft may be prepared by a tool, but confirmation
must be an application-owned endpoint receiving only `clientActionId`.

Unit tests must cover missing fields, edits invalidating confirmation, expired drafts, double
confirmation, conflicting client action IDs, and every allowed/forbidden transition. Do not call
platform write endpoints yet.

### [~] Task 14: Add sales write adapters and confirmed execution

Read: PRD FR-04, FR-05, and FR-09; LLD sections 8.2, 10.2, 10.3, 11, and 14.

Add protected adapter methods and workflow executors for sales enquiry, test drive, reserved
vehicle interest, and callback. Attach `X-API-Key` only server-side. Recheck vehicle availability
immediately before dependent actions. Persist one UUID idempotency key before each creation call
and reuse it on eligible retry. Return exact platform states and public references.

Integration tests must prove:

- `veh-001` test drive succeeds once;
- `veh-007` test drive is blocked and interest is offered;
- `veh-013` test drive/interest are blocked but sales enquiry is allowed;
- duplicate confirmation/retry creates no duplicate record;
- no response promises timing or queue position.

### [~] Task 15: Add new workshop booking

Read: PRD FR-06 and FR-09; LLD sections 10.4, 11, and 14.

Implement service/location/slot selection, the workshop booking draft, availability recheck,
confirmed creation, idempotent retry, and receipt. The user must choose a slot returned by the
current search. Collect registration, mileage, contact details, optional notes, and final
confirmation.

Integration tests must cover a successful booking, stale/unavailable slot recovery, Bolton's valid
no-results case, validation preservation, and duplicate-confirmation safety.

### [~] Task 16: Add private booking lookup and verified grants

Read: PRD FR-06 and section 10; LLD sections 4.5, 6, and 10.5.

Implement the dedicated structured lookup endpoint and `verified_booking_grants` repository. Send
reference, surname, registration, and phone directly to deterministic backend code. Never add
these proof values to transcript, model input, drafts, grants, or logs. Return the same generic
`BOOKING_NOT_FOUND` presentation for every mismatch. Clear browser fields after submission.

Tests must cover seeded `WORK-10001`, each single-field mismatch, expiry, conversation scope, raw
database inspection, captured fake-model input, captured logs, and browser field clearing.

### [~] Task 17: Add workshop amendment and cancellation

Read: PRD FR-06 and FR-09; LLD sections 10.5 and 14.

Require a current verified grant and explicit confirmation for amend/cancel. Only accept a live
returned slot for moves. Since platform PATCH/DELETE does not accept idempotency keys, deduplicate
locally by `clientActionId` and request fingerprint. A failed move must leave the original booking
unchanged. Revoke or update grants after success as appropriate.

Tests must cover successful amend/cancel, claimed slot, expired/foreign grant, repeated action,
changed action body, and original-booking preservation after failure.

### [~] Task 18: Add remaining contact and valuation workflows

Read: PRD FR-07–FR-09; LLD sections 5.3, 8.2, and 10.6.

Implement dealership message and part-exchange valuation drafts, confirmation, protected adapter
calls, idempotency, and receipts. A saved message is `received`, not answered. A valuation uses the
exact returned pence range and always includes the platform qualification. Show the privacy notice
before or when collecting personal information.

Tests must cover exact statuses, exact money formatting, qualification, privacy notice, validation,
and duplicate prevention.

## Phase 5 — Production provider and hardening

### [x] Task 19: Add the OpenAI provider adapter

Read: HLD section 5.6; LLD sections 7 and 12.

Implement the OpenAI Responses API behind the existing provider protocol. Keep model name,
timeout, output limit, and retries configurable. Translate only the internal read and draft-builder
tool definitions. Never expose the confirmation executor as a model tool. Log usage and latency
without prompts, message bodies, tool arguments containing personal data, or credentials.

All deterministic tests must continue to use the fake provider. Add mocked adapter tests; do not
require a real network call in CI.

### [x] Task 20: Add security and resilience controls

Read: PRD FR-10 and sections 10–11; HLD sections 9–10.

Add same-origin validation for state-changing requests, JSON-only enforcement, request size/rate
limits, safe dependency timeouts, safe-read retries, retention cleanup, CSP and security headers,
and sanitized public errors. Preserve pending drafts during dependency failures. Health readiness
may name an unavailable dependency but must not expose credentials or private error bodies.

Tests must cover cross-origin rejection, wrong content type, oversized payload, rate limiting,
timeouts, recovery, retention, headers, and secret/PII scanning of logs and responses.

## Phase 6 — Evidence and handoff

### [ ] Task 21: Add complete seeded integration coverage

Read: PRD section 12; LLD sections 16.2 and 16.3; `docs/SEEDED-SCENARIOS.md`.

Create deterministic integration tests for AC-01 through AC-25 where the behaviour is server-side.
Use the real local dealership container and fake LLM. Reset seeded state between mutation groups or
use isolated generated inputs. Name tests after acceptance IDs so failures map back to the PRD.

Do not weaken assertions to make a scenario pass. Record an actual product limitation in the plan
with `[!]` if a requirement cannot be met.

### [ ] Task 22: Add browser and accessibility tests

Read: PRD section 9 and AC-16 through AC-20; LLD section 16.4.

Add browser tests for launcher placement, focus return, Escape, tab order, mobile layout, loading,
retry, duplicate-click prevention, restore after refresh, context changes, typed cards, private
lookup clearing, unread badge, and unavailable states. Assert that secrets are absent from DOM,
storage, scripts, and browser request headers.

### [x] Task 23: Finish setup and operational documentation

Read: PRD success criteria and HLD section 11.

Update `README.md` with clean-checkout setup, environment variables, architecture summary, service
URLs, test commands, reset/stop instructions, and troubleshooting. Document migration behaviour,
retention, fake-provider testing, important design decisions, and known limitations. Never include
real secrets. Check every documented command from a clean checkout where practical.

### [~] Task 24: Final verification against the definition of done

Read: LLD section 17 and all PRD acceptance scenarios.

Run the complete test suite, Docker Compose startup, health checks, browser smoke tests, formatting,
lint, dependency audit, and secret scan. Verify `dealership-platform/` has no changes. Produce a
short matrix mapping AC-01 through AC-25 to test names or manual evidence. Do not implement new
features during this task; report failures precisely and return them to the relevant earlier task.

Suggested final commands (adjust only to match commands established by earlier tasks):

```bash
docker compose config
docker compose up --build -d
cd webchat-service && python -m pytest
git diff --check
git status --short
```

## Milestone gates

Do not move to the next phase until the current gate passes:

| Gate | Required evidence |
| --- | --- |
| Foundation | Tasks 1–5 tests pass; conversations survive service restart; no real LLM needed |
| Reads | Tasks 6–8 tests pass; platform-backed answers contain no invented values |
| Website | Tasks 9–12 tests pass; refresh/context/accessibility paths work |
| Writes | Tasks 13–18 tests pass; confirmation and duplicate prevention proven |
| Hardening | Tasks 19–20 tests pass; fake provider remains deterministic; no secret/PII leakage |
| Done | Tasks 21–24 pass; AC-01 through AC-25 have evidence or an approved documented limitation |

## Review checklist for each completed task

Before marking a task `[x]`, confirm:

- the diff contains only that task;
- file and function names match the LLD unless there is a documented reason;
- comments explain “why,” not obvious syntax;
- security and business rules are enforced outside the model;
- error messages are useful but do not leak private details;
- narrow tests pass and `git diff --check` is clean;
- no commit or push was made unless the user separately requested it.
