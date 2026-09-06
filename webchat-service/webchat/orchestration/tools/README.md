# Application tool implementations

Tool modules implement catalogue definitions; they do not select customer intent or write the final
normal conversational response.

| File | Responsibility |
| --- | --- |
| `contracts.py` | minimal execution protocol shared with orchestration/catalogue layers |
| `executor.py` | dispatch catalogue execution to focused handlers |
| `result.py` | immutable typed `ToolResult` |
| `inputs.py` | separate provider-public and server-runtime Pydantic schemas |
| `actions.py` | exact trusted client actions and execution metadata |
| `vehicles.py` | inventory, filters, details, availability, comparisons |
| `offers.py` | published offers and finance facts |
| `dealerships.py` | dealership details, departments, hours, contact facts |
| `business.py`, `business_information.py` | approved static/business-policy facts |
| `workshop.py` | service discovery/resolution, locations, test-drive/workshop slots |
| `service_resolution.py` | live `matched`/`ambiguous`/`unavailable` service matching |
| `forms.py` | legacy-named protected booking lookup and part-exchange input activation |
| `workflows.py` | capability/draft preparation, protected-field activation, cancel/resume |
| `helpers.py` | bounded location normalization/matching |

Execution is `UnifiedToolCatalog -> ApplicationToolExecutor -> handler -> dealership/workflow
service -> ToolResult`. Provider schemas must never expose protected fields. Confirmed writes remain
outside the planner-visible catalogue.
