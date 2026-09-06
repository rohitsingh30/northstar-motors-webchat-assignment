# Semantic retrieval

| File | Responsibility |
| --- | --- |
| `candidates.py` | BGE embeddings and ranked public tool/knowledge candidates |
| `knowledge.py` | immutable packaged `KnowledgeEntry` loading/querying |
| `knowledge.json` | generated customer-audience evidence |

Retrieval reduces model context; it is not an intent classifier, confidence boundary, or service
matcher. The hosted AI proposes operations and deterministic policy validates them. Active
capability, verification, and interaction tools are pinned so a short continuation cannot lose the
operation required by durable state.

Do not edit `knowledge.json` directly. Update `PRODUCT-BRIEF.md`, `docs/BUSINESS-SEMANTICS.md`, or
`docs/CUSTOMER-KNOWLEDGE.md`, then run:

```bash
python3 webchat-service/scripts/build_knowledge_index.py
```

Dynamic stock, price, dealership, service, offer, and slot facts belong in tools, not knowledge.
