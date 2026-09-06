# Hosted LLM adapter

This package adapts a Responses-compatible provider to three bounded AI phases: semantic turn
resolution, capability planning, and grounded response composition. It never executes tools.

| File | Responsibility |
| --- | --- |
| `provider.py` | provider transport, candidate pinning, plan request, composition request, repair bounds |
| `turn_resolution.py` | typed dialogue-act, goal, reference, input-provenance, and ambiguity resolution |
| `candidates.py` | semantic and active-state-aware candidate selection |
| `protocol.py` | native tool schemas and strict plan/composition parsing |
| `__init__.py` | stable `HostedLlmProvider` facade |

Turn resolution receives the active goal, open question, typed references, calendar, and ranked
evidence. Its validated `TurnUnderstanding` is authoritative for planning. Planning receives only
public schemas, that understanding, structured state, trusted references, and approved evidence.
Composition receives normalized fact envelopes and must return
closed references. Structural parsing rejects malformed output; orchestration grounding rejects
unsupported fact/link/card/collection identifiers and other hard trust-boundary violations.

Single-intent business functions derive their application-owned meaning during protocol parsing.
Only functions that can serve several intents require an `intentKind` enum from the shared intent
registry; protocol parsing and policy both reject an incompatible declaration. Genuine ambiguity
between retrieved intents has a separate typed clarification outcome. Composition also receives
advisory response obligations and, after a hard grounding failure, one bounded repair reason; it never
receives new or mutable business facts.

Composition may propose a small set of non-executable conversational quick replies. It must use
trusted suggestion references for entity selections or protected actions. Prompts ask it to avoid
repeating list-card detail, keep comparisons concise, and turn broad discovery into a guided
question. Those writing goals do not make a successful business result fail at runtime.

Typed selections, misspellings, relative dates, topic changes, and open-question answers are turn-
resolution responsibilities. Planning cannot reinterpret them.
For protected vehicle navigation the plan can contain only a trusted vehicle reference; a second
composition call writes the final question, and the application attaches both server-authored
protected choices regardless of optional AI presentation references.

The package does not import `fake_llm`, and hosted failures never fall back to it.
