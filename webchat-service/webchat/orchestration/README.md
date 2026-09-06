# Orchestration

Orchestration turns one persisted public customer turn into grounded assistant messages and state
events. It contains no browser workflow wizard and no online keyword intent router.

```text
bounded state-linked context -> hosted AI TurnUnderstanding -> semantic validation
 -> hosted AI capability plan -> policy + workflow transition compiler -> catalogue/tools
 -> fact normalization + response obligations -> hosted AI composition
 -> trust grounding + deterministic views -> atomic persistence
```

| Path | Responsibility |
| --- | --- |
| `orchestrator.py` | per-conversation lock, idempotent lifecycle, atomic persistence coordination |
| `context.py` | last-12-message window, structural relevance weights, open question, trusted page/result context |
| `references.py` | trusted entity/ordinal/appointment provenance |
| `appointments.py` | one Europe/London appointment display/choice projection and shared fallback scopes |
| `customer_language.py` | reject internal page-context terminology from customer-visible output |
| `workflow_kernel.py` | deterministic workflow outcomes, transitions, and live-result continuation decisions |
| `contracts/` | strict plan, fact, presentation, and grounded-response schemas |
| `retrieval/` | semantic candidate/evidence ranking, not routing authority |
| `planning/` | hosted planning/composition policy text and deterministic contract helpers |
| `policy.py` | schema, risk, state, reference, and precondition validation |
| `intent_contracts.py` | application-owned compatibility between declared intents and tools |
| `catalogue/` | single application/read-only-MCP tool inventory and dispatch |
| `tools/` | authoritative reads and draft preparation |
| `state.py` | public capability state reduced from actual results |
| `provider_loop.py` | bounded plan/policy/execute/replan loop preserving all results |
| `fact_normalization.py` | immutable fact/entity/card/link/collection envelopes |
| `grounding.py` | resolve trusted references, normalize optional presentation, reject unsafe output |
| `presentation/` | closed renderer/suggestion validation |

The AI first interprets language into a typed semantic decision, then proposes operations that must
match that decision. Deterministic code validates question identity, references, customer-value
provenance, intent/tool compatibility, and execution state. Tools never generate
the final normal conversational answer; the composer writes it only
after authoritative results exist. Policy rejection returns to hosted planning and never becomes
customer-facing policy prose. Confirmed writes stay outside planner authority; vehicle navigation
is proposed by AI, worded by AI composition, and executed only by the protected server boundary.

The policy also compiles declared live-field dependencies. For example, persisting a test-drive
request with a trusted vehicle necessarily continues to live appointment lookup; a schedule-only
follow-up resolves fresh slots without cancelling and recreating the same draft.

Clear transactional intent is persisted before the first public question. Every open question is
stored with its owning goal, expected fields, candidate references, and already-grounded prompt.
Capability contracts declare dependency-safe question groups and continuation tools, while planning
performs useful live reads before asking a customer to choose from catalogue data. Informational
detail interruptions preserve that question structurally but do not append its old prompt beneath
the informational answer; an explicit or implicit return surfaces it again. A catalogue operation
whose declared candidate-subject field competes with the selected transaction subject instead opens
a current browsable branch and pauses the transaction. The application compiles question
disposition from the policy-approved operation and its workflow effect; the model's dialogue-act
label does not independently mutate question state. Workflow reduction and dialogue reduction
therefore share one transition decision for every registered capability. A goal
reaffirmation without a pending field is
normalized to `continue_goal`, leaving the question open for any workflow. Same-transaction steps
retain established entities and trusted relationships; explicit replacement selectors invalidate
their old entity first. Exact repeats of that retained entity closure are redundant during a
fieldless reaffirmation, while different references continue through normal planning.

Application-owned view actions enter through the shared handler in `tools/actions.py`. The client
supplies only a closed action and its trusted identifiers. That boundary reconstructs compatible
public selectors and preferences from durable state and carries multi-step menu provenance in a
closed persisted interaction handoff. An explicit handoff consumes the source transaction; an
ordinary different transaction pauses it. Capability-and-field keyed choice sources ensure finite
workflow questions expose only values accepted by their target capability.

Finite choices remain typed through execution. A choice payload carries its entity namespace and
applicable scalar field with the exact labels and trusted records rendered to the customer.
`planning/affordances.py` evaluates policy-validated calls and withholds any operation that cannot
bind the selected candidate. The rule covers entity choices and workflow enums across capabilities;
it never infers identity from a view name, tool name, or customer phrase.

Grounding owns only the hard trust boundary: fact/link/reference authority, privacy, and protected
actions. It normalizes typography, duplicate optional references, irrelevant chips, and trusted
view attachment without failing the turn. Card/prose ownership, concise comparisons, useful chips,
complete sentences, and workflow question shape are composition and quality-evaluation rules, not
customer-facing availability failures.

Model comparison resolution uses live make/model identity before variant text, persists partially
resolved operands across clarification, and carries representative-selection provenance into the
final result. Response obligations tell the composer and offline quality checks what completion
looks like; they do not turn a safe authoritative result into a business failure. Bounded repair
and typed application fallback preserve unchanged authoritative facts.

The provider/tool loop permits at most four iterations and four business-tool calls. Tool effects
reduce an in-memory state snapshot; `TurnCommitRepository` then stores assistant messages,
normalized results, dialogue/workflow state, any protected interaction transition, and completed
turn status in one optimistic compare-and-swap transaction.
