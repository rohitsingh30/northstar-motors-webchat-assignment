# Unit tests

Unit tests verify deterministic contracts without a running Compose stack.

| File | Primary coverage |
| --- | --- |
| `test_application_fallback.py` | typed trusted-result fallback copy without public-language routing |
| `test_business_information.py` | approved fact matching and required topic qualifications |
| `test_business_semantics.py` | money and availability wording |
| `test_conversational_contracts.py` | public/protected capability schemas, grounding, card/action invariants |
| `test_hosted_llm.py` | planning/composition protocols, candidate pinning, reference/link validation |
| `test_workflow_state_and_policy.py` | capability state, interruptions, trusted preconditions, amendment metadata |
| `test_routing_architecture.py` | hosted ownership and fake-provider isolation |
| `test_service_resolution.py` | live `matched`/`ambiguous`/`unavailable` contract |
| `test_vehicle_reference_context.py` | trusted result/entity/ordinal extraction |
| `test_interactions.py` | protected latest-only interaction validation |
| `test_location_parsing.py` | isolated fake-provider location fixtures and trusted dealership context |
| `test_mcp_catalogue.py` | catalogue schema alignment, execution metadata, and read-only MCP discovery |
| `test_presentation_cardinality.py` | singular/multiple result numbering and card-owned prose normalization |
| `test_presentation_deduplication.py` | explicit versus contextual visual reuse |
| `test_read_tools.py` | authoritative read tools and closed result contracts |
| `test_redaction.py` | structured logging and secret/contact redaction |
| `test_suggestions.py` | trusted suggestion selection, filters, and follow-up contracts |
| `test_vehicle_safety.py` | narrow active-hazard guidance without benign-query interception |
| `test_workflows.py` | drafts, redaction, confirmation, verification, idempotency |
| `test_database.py`, `test_repositories.py` | migrations and repository behavior |
| `test_retrieval.py` | semantic candidate/evidence retrieval |
| `test_fake_llm.py` | deterministic test fixture only |
| `test_workflow_tool_enrichment.py` | server-trusted draft enrichment and replacement metadata |

When moving ownership, update imports/tests to the canonical module instead of adding production
compatibility aliases.
