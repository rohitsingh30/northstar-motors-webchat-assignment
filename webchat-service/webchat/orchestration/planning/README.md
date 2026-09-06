# Planning contracts

| File | Runtime responsibility |
| --- | --- |
| `affordances.py` | side-effect-free policy preflight that narrows planning to legal progress operations and grants generic clarification only when progress is blocked |
| `prompt.py` | hosted planner and grounded-composer policies |

The production hosted provider makes a planning request and, after tool execution, a composition
request. Deterministic schemas, affordances, policy, and grounding validate those phases; there is
no independent reviewer request or reviewer compatibility interface.

Planning may propose public tool arguments, a declared-blocker clarification, a semantic-intent
clarification, or a trusted interaction proposal. Single-intent metadata is derived by the
application; multi-intent tools require an explicit compatible intent. Planning cannot execute
tools, supply protected fields, confirm a mutation, or emit a URL.
The planner receives generic clarification only after `affordances.py` has proved that the current
typed turn has no safe progress operation for at least one resolved intent. The probe reuses policy
and catalogue contracts and does not parse public language. When all resolved intents can progress,
blocked sibling tools and generic clarification are removed before the provider request.
Composition may reference only normalized result facts/cards/collections/links and must place one
transactional question last when a turn also contains information answers. Policy never supplies
the visible clarification wording; rejected proposals return to planning.
