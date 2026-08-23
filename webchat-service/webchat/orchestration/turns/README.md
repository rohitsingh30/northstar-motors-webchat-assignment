# Turn execution loops

This folder owns bounded interaction with the configured provider after context has been assembled.

## Files

| File | Responsibility |
| --- | --- |
| `__init__.py` | Package marker |
| [`provider_loop.py`](./provider_loop.py) | Provider-first domain-goal planning, semantic policy/re-plan, tool-result history, workflow-state persistence, loop limits |

## Limits and termination

- At most four provider iterations.
- At most one rejected `conversation.respond` re-plan; it consumes one of the four iterations.
- At most four calls in a provider reply.
- Provider call timeout supplied by the orchestrator; default 20 seconds.
- A closed renderable tool view terminates the turn.
- Service information/vehicle availability may terminate as direct answers without a view.
- Non-terminal facts are serialized into in-memory tool history; the closed customer view remains
  application-owned.

`ProviderToolLoop` always asks the configured provider to interpret free text before applying the
typed semantic plan. `SemanticPlanPolicy` evaluates every validated plan but intervenes only on a
proposed `conversation.respond`; it never preempts provider interpretation. The loop owns policy enforcement,
typed semantic-plan application, tool execution, and canonical workflow-state updates.
