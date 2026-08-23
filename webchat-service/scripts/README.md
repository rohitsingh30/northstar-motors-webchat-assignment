# Webchat build scripts

`build_knowledge_index.py` rebuilds the packaged semantic-retrieval corpus from
`PRODUCT-BRIEF.md`, `docs/BUSINESS-SEMANTICS.md`, and `docs/CUSTOMER-KNOWLEDGE.md`. Run it after
changing those sources; the generated JSON is committed as a runtime artifact because the webchat
Docker build context intentionally excludes repository-level documentation.

The source list is deliberately curated. HLD/LLD and widget instructions describe implementation,
not customer truth. `INTEGRATION-GUIDE.md` contains credentials/examples, and
`SEEDED-SCENARIOS.md` contains resettable dynamic data, so neither is sent to the model. Live data
from those areas must be retrieved through the unified tool catalogue.

The script emits every non-empty level-two/level-three section, records its exact `path#heading`,
and assigns a customer or planner audience. Customer sections must contain final customer-ready
copy because runtime materializes them verbatim; generation fails if they contain internal role
instructions. Review the JSON diff after regeneration.

CI/local freshness check:

```bash
python3 webchat-service/scripts/build_knowledge_index.py --check
```
