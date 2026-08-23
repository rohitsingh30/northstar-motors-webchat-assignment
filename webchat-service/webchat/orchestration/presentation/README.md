# Presentation

Presentation preserves application-owned results and validates closed widget views.

| File | Responsibility |
| --- | --- |
| `registry.py` | Closed renderer names and optional per-tool renderer validation |
| `suggestions.py` | Application-owned labels, typed actions, facets, and next-step choices |
| `clarifications.py` | Reviewed finite clarification choices rendered as non-executing reply chips |

Renderable tool results terminate the provider loop and cannot be replaced by generated prose.
Evidence-only results have no view and may support a later reviewed text response. Dynamic business
facts do not belong in static suggestion code.

Normal suggestion groups are composed for a balanced two-or-four-chip layout. Service-type
suggestions are live catalogue choices and remain uncapped by that normal presentation rule.
Finite reviewer clarifications may also use the existing suggestion-list renderer. Those chips send
plain reply text through the next fully reviewed turn and never execute tools directly.
