# Unified tool catalogue

This is the one runtime inventory for application-backed and MCP tools.

| File | Responsibility |
| --- | --- |
| `__init__.py` | Public definition/catalogue exports |
| `contracts.py` | Source-independent `ToolDefinition`, schemas, risk, invocation, renderer, result mode |
| `definitions.py` | Declarative metadata for all application-backed capabilities |
| `registry.py` | Registration, filtering, validation, timeout, and dispatch only |
| `mcp.py` | Streamable HTTP MCP discovery, namespacing, schema preservation, evidence normalization |

The catalogue owns what a tool is; executors own how it runs. Definitions may include semantic
retrieval examples, which improve embedding recall without becoming keyword routes or confidence
thresholds. `planner_tools()` exposes only available planner operations and excludes confirmed
writes. Every execution revalidates arguments and uses the definition's timeout.

`search_vehicles` starts a new inventory query. `refine_vehicle_search` updates the current
server-owned query and preserves every omitted filter, so the model never has to reconstruct search
state from transcript text.

`list_workshop_slots` starts a slot search. `refine_workshop_slots` changes its service, location,
or dates while preserving omitted constraints. Supplying a new service replaces the old service
identity instead of combining incompatible IDs and names.

Dealership capabilities keep cardinality explicit: `list_dealership_departments` covers every
location, `find_dealership_departments` covers a named or sole current location, list-hours preserves
a multi-location set, and singular dealership-ID reads cannot select one arbitrary item from a set.
Regular weekly schedules use `list_opening_hours`; published bank-holiday and other exceptions use
`list_holiday_opening_hours`, preventing a holiday request from being inferred as a weekday query.
`show_dealership_contact_options` is the safe executable continuation when a customer accepts an
offer of contact help without choosing callback, message, details, or opening hours.

Internal browser operations also live in this catalogue but are excluded from `planner_tools()`.
`request_offer_enquiry_form` opens the inline offer enquiry form without persisting or submitting a
draft; `prepare_sales_enquiry` creates the validated server-side draft after the form is completed.

MCP tools are namespaced as `mcp__{server}__{tool}`. Only tools explicitly annotated read-only are
planner-visible. Remote mutation tools require a future application confirmation workflow.
