# Unit tests

Unit tests verify deterministic policies and focused modules without a running Compose stack.

## Files

| File | Coverage |
| --- | --- |
| [`test_business_information.py`](./test_business_information.py) | Data-driven authoritative business-fact matching, scoped cards, and fail-closed unsupported questions |
| [`test_business_semantics.py`](./test_business_semantics.py) | Money and availability wording |
| [`test_database.py`](./test_database.py) | Repeatable ordered migrations |
| [`test_fake_llm.py`](./test_fake_llm.py) | Fake-provider search, context, refinement, comparison, forms, slots, and fallbacks |
| [`test_semantic_review.py`](./test_semantic_review.py) | Strict review outcomes, corrected-proposal validation, clarification, citations, and invalid-correction rejection |
| [`test_routing_architecture.py`](./test_routing_architecture.py) | Fake-routing isolation, raw-wording boundary, hosted review enforcement, typed-action completeness, and workflow-kind completeness |
| [`test_location_parsing.py`](./test_location_parsing.py) | Town parsing and dealership/hour filtering |
| [`test_mcp_catalogue.py`](./test_mcp_catalogue.py) | MCP discovery, schema preservation, read/write visibility, and configuration validation |
| [`test_hosted_llm.py`](./test_hosted_llm.py) | Native calls, separate compulsory proposal/reviewer requests, shared provider/model/authentication, citations, fail-closed review, and URL validation |
| [`test_interactions.py`](./test_interactions.py) | Persisted interaction validation, typed actions, and immediately-preceding-message scope |
| [`test_read_tools.py`](./test_read_tools.py) | Vehicle/catalogue/workshop/form validation, fuzzy town resolution, and closed views |
| [`test_redaction.py`](./test_redaction.py) | Structured and embedded sensitive-data removal plus JSON formatting |
| [`test_repositories.py`](./test_repositories.py) | Session hashing and ordered message persistence |
| [`test_service_resolution.py`](./test_service_resolution.py) | Live workshop-service matched, ambiguous, and unsupported outcomes |
| [`test_suggestions.py`](./test_suggestions.py) | Result-aware chips, typed actions, facets, and status permissions |
| [`test_workflow_state_and_policy.py`](./test_workflow_state_and_policy.py) | Version-3 state reduction, legacy-state compatibility, trusted-reference policy, clarifications |
| [`test_retrieval.py`](./test_retrieval.py) | Semantic tool recall and source-traceable knowledge evidence |
| [`test_vehicle_reference_context.py`](./test_vehicle_reference_context.py) | Ordered current vehicle/offer/search reference extraction |
| [`test_workflow_tool_enrichment.py`](./test_workflow_tool_enrichment.py) | Workflow draft/confirmation payload enrichment at the tool boundary |
| [`test_workflows.py`](./test_workflows.py) | Required drafts, confirmation, hidden contact summary, booking-proof persistence boundary |

When moving a module, update test imports to the canonical owner rather than adding compatibility
aliases in production code.
