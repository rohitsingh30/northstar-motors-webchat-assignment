# Domain package

Domain modules define application-owned invariants without HTTP, model transport, or rendering.

| File | Responsibility |
| --- | --- |
| `capabilities.py` | one registry for public fields, protected fields, optionality, dependency-safe public question groups, live-field resolvers, continuation tools, and agent tool per workflow |
| `conversation_state.py` | versioned workflow/dialogue state, open-question contracts, and validated event reducer |
| `protected_interactions.py` | latest-only protected confirmation/navigation contracts |
| `turn_actions.py` | closed typed client-action contract |
| `approved_content.py` | approved application copy/fact identifiers |
| `field_guidance.py` | shared labels, prompts, examples, and closed choices for collected input |
| `vehicle_safety.py` | narrow first-response guidance for unambiguous active vehicle hazards |
| `interactions.py` | pending assistant interaction models |
| `models.py` | immutable message/turn records |
| `business_semantics.py` | exact money and availability semantics |
| `workflows.py` | draft completeness, redaction, idempotent confirmed writes, receipts, verification grants |

Provider-facing workflow schemas may contain only registry `public_fields`. Protected values enter
only dedicated endpoints. A draft must be complete and persisted before confirmation; confirmation
executes stored data, never browser/model overrides. Booking amendment/cancellation requires a live
verified grant, and vehicle-sensitive writes recheck availability where required.

When a required public value must be selected from changing business data, the capability declares
its live-field resolver. The policy compiler then makes that read part of the workflow transition;
the model cannot leave the customer at an internal draft placeholder or invent the missing value.
Customer wording is resolved before policy. Domain code validates the resulting IDs, question
relationship, and source provenance; it contains no phrase or ordinal intent router.

The sole raw-language domain exception is immediate harm-reduction guidance for a closed set of
unambiguous active vehicle hazards. It cannot select a business capability, diagnose a fault, or
mutate trusted state.
