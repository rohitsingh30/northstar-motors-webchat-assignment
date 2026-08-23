# Planning package

Planning converts a semantic domain and goal into deterministic application transitions. It is the boundary
between probabilistic language understanding and exact business-tool execution.

## Files

| File | Responsibility |
| --- | --- |
| `__init__.py` | Package marker |
| [`conformance.py`](./conformance.py) | Application-owned evidence rules that prevent a schema-valid vehicle-search plan from executing without an inventory-discovery request |
| [`ontology.py`](./ontology.py) | Canonical `GoalKey` values, full supported goal set, V1 persisted-state adapter, and V2 workflow-state constructor |
| [`plan_policy.py`](./plan_policy.py) | Observe/enforce policy for provider conversation responses, deterministic fallback, one re-plan, and safe clarification |
| [`prompt.py`](./prompt.py) | Compact hosted planner policy, domain responsibilities, privacy, reference, and grounding rules |
| [`turn_plan.py`](./turn_plan.py) | Domain-discriminated `TurnPlanPayload` union, goal-specific arguments, provider tool definition, parser |
| [`transitions.py`](./transitions.py) | `TransitionController`, workflow state ownership, reference resolution, clarification, state advancement |
| [`tool_routes.py`](./tool_routes.py) | Route map from canonical `GoalKey` values to exact filtered application tool calls |

## Design rules

- The provider emits `version`, `domain`, `goal`, and domain-specific arguments—not arbitrary business tool calls.
- Invalid domain-goal pairs and cross-domain/unknown arguments fail schema validation.
- A vehicle noun or provider-default sort is not enough to execute an inventory search; the turn
  must contain a substantive typed search constraint, explicit discovery wording, or a grounded
  current-result reference.
- A `choose_preferences` plan that already contains a concrete stock constraint is normalized to
  `vehicle.search`; the preference picker cannot hide or discard an executable filter.
- `conversation.respond` is not sufficient for a business-looking request when policy enforcement is enabled.
- A gate-triggered re-plan happens at most once; repeated prose fails to a server-owned clarification.
- New workflow state is always V2 `{version, domain, goal, stage, entities, constraints}`; V1 `intent`
  values are accepted only by the persistence-boundary adapter.
- A new task does not silently inherit stale workflow entities or filters.
- Explicit references may reuse active entities; implicit reuse is rejected.
- Offer purchase interest maps to `offer.enquire`, never reserved-stock interest.
- Workshop information remains distinct from workshop booking.
- Named-service checks resolve against the live service catalogue and return explicit matched,
  ambiguous, or unsupported outcomes; they never silently become a full-catalogue response.
- Referential location follow-ups keep the active workshop service and strip conversational text
  from the town before tool execution.
- Transition outputs are `PlannedTransition` values; execution happens elsewhere.

Primary tests are `tests/unit/test_transitions.py` and
`tests/integration/test_structured_flow_contracts.py`.
