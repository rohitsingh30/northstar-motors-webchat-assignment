# Presentation contracts

Presentation validates the closed view and external suggestion vocabulary used after grounding.

| File | Responsibility |
| --- | --- |
| `registry.py` | closed tool-result view protocol |
| `suggestions.py` | application-owned external quick replies and typed actions |
| `clarifications.py` | safe finite clarification choices |

Tool results do not terminate the normal hosted turn before AI composition. Result views and facts
are normalized, the composer writes the response, grounding resolves trusted references, and the
orchestrator attaches trusted views deterministically. Direct protected endpoints are the
exception: because their private payload is never AI input, they may return application-authored
secure/confirmation/receipt copy and closed views.

The registry checks whether a result uses a view implemented by the application. It deliberately
does not repeat a tool-to-view matrix: a tool handler owns its output choice, and duplicating that
choice in catalogue metadata makes valid results fail when the two lists drift apart.

Suggestions render outside cards. Selecting one appends its visible label as a customer message and
uses the same turn boundary as typed input. Ordinary suggestions cannot bypass policy. Explicit
confirmation/navigation controls enter the latest-only protected action boundary and cannot supply
or replace operation arguments.

Trusted suggestions are application actions or grounded entity choices. Separately, the composer
may propose non-executable conversational quick replies that simply re-enter `/turns`. Optional
conflicts are normalized instead of failing the turn; protected controls are always retained and
strictly validated when used. Card/prose ownership and chip relevance are composer/quality rules,
not hard availability validators.

The same normalizer owns optional chip cardinality: three trusted references are completed with the
next compatible candidate from that result, while an unsafe-to-extend three-item set is reduced to
two. A complete finite collection is not an optional suggestion row and may contain three choices.
The collection schema keeps informational results and detailed catalogue/reference choices as
`bullet_list`; only compact answer values use `chip_grid`. Choice and clarification semantics do not
force a visual layout.
`chip_grid` is rendered by the ordinary one-line reply component, not a rich collection mini-card.
An optional item `message` lets the server keep a short visible label while submitting the exact
ordinary-language answer; otherwise the label is submitted.
Appointment detail deliberately uses plain bullets. A separate compact reply row is projected from
the same trusted appointment records and sends the exact canonical date/time label; compact workflow
values and intentionally selected follow-up answers use the same simple chip mechanism. Clicking a reply or typing a
bullet's date/time uses the ordinary `/turns` language path, where the open interaction and trusted
context resolve it.
