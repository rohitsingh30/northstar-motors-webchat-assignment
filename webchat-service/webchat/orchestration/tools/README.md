# Application tool implementations

These modules implement tools whose definitions live in the unified catalogue. The package is not
a second catalogue and does not select online intent.

| File | Responsibility |
| --- | --- |
| `executor.py` | Adapt catalogue execution to focused application handlers |
| `contracts.py` | Small shared executor protocol |
| `result.py` | Immutable `ToolResult` contract |
| `inputs.py` | Strict Pydantic input schemas |
| `actions.py` | Exact typed-widget-action handlers, including vehicle filter change/clear/reset, plus concrete execution metadata for state reduction |
| `vehicles.py` | Inventory, current-page selection, facets/preferences, details, availability, comparisons |
| `offers.py` | Published offers and authoritative finance notices |
| `dealerships.py` | Dealership directory, contact choices, departments, and opening hours |
| `business.py` | Execute exact public business-information lookups |
| `workshop.py` | Service resolution/listing, workshop locations, test-drive/workshop slots |
| `forms.py` | Private booking lookup and part-exchange estimate forms |
| `workflows.py` | Map `prepare_*` operations to persisted draft kinds and resolve form locations to live dealership IDs |
| `business_information.py` | Pure resolution of an exact policy question to authoritative facts |
| `service_resolution.py` | Conservative matching against the live workshop catalogue |
| `helpers.py` | Location normalization and bounded fuzzy matching against live towns |

Execution shape:

```text
UnifiedToolCatalog.execute(...)
  → ApplicationToolExecutor
      → capability handler → DealershipClient / WorkflowService → ToolResult
```

To add an application tool:

1. Add a strict input model and focused handler method.
2. Add its complete metadata to `orchestration/catalogue/definitions.py`.
3. Return a renderer declared by that definition, or evidence facts only.
4. Add handler, catalogue-coverage, policy, and conversation-contract tests.
5. Update the local README plus HLD/LLD.

Never add confirmation execution to the planner-visible catalogue. Models may prepare drafts only.
