# Unified tool catalogue

This package is the single inventory for application-backed and optional read-only MCP tools.

| File | Responsibility |
| --- | --- |
| `contracts.py` | `ToolDefinition`, provider schema, runtime schema, risk/result metadata |
| `definitions.py` | declarative application capabilities and preconditions |
| `registry.py` | registration, validation, timeout, and dispatch |
| `mcp.py` | MCP discovery, namespacing, read-only filtering, evidence normalization |

Each workflow tool has a provider-public input model and, where necessary, a richer runtime model.
Policy validates the public model first, then injects only server-trusted IDs/metadata and validates
the runtime model. Protected fields never appear in planner schemas. Confirmed mutation tools are
not planner-visible.

Execution-only metadata stays outside both argument schemas. A protected draft replacement ID is
validated by the HTTP/state boundary, then catalogue dispatch forwards it to the workflow executor
without exposing it to planning. Workshop, test-drive, enquiry, callback, message, vehicle-interest,
and part-exchange replacements all use this path.

`ToolDefinition.reference_inputs` declares how resolved trusted entity references bind to generic
provider fields such as `id`. Startup validation requires every declared field to exist in the
provider schema and every namespace to belong to the shared reference vocabulary. Planning
preflight therefore never infers entity types from tool names, policy-condition names, or customer
phrasing.

Search/refinement tools preserve omitted server-owned filters. Dealership/service cardinality is
explicit. Workshop service resolution produces `matched`, `ambiguous`, or `unavailable`; semantic
retrieval does not convert similarity into a business match.

Some internal identifiers such as `request_offer_enquiry_form` are retained for request
compatibility. They now activate the corresponding capability/protected boundary and do not render
or open an HTML form.
