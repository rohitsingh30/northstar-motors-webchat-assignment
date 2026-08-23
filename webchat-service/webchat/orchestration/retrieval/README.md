# Semantic retrieval

| File | Responsibility |
| --- | --- |
| `__init__.py` | Public candidate/knowledge exports |
| `candidates.py` | BGE query/passage embeddings and ranked tool/knowledge candidates |
| `knowledge.py` | Load and query immutable packaged `KnowledgeEntry` records |
| `knowledge.json` | Generated runtime sections with source and audience metadata |

Tool retrieval and knowledge retrieval share one local embedding model but remain different result
types. Each tool may provide catalogue-owned semantic usage examples; ranking uses the best match
across its canonical description and examples. These improve recall without runtime keyword rules
or treating cosine similarity as confidence. Retrieval narrows model context; the planner and
independent reviewer make and validate the actual proposal.

Do not edit `knowledge.json` directly. Update `PRODUCT-BRIEF.md`, `docs/BUSINESS-SEMANTICS.md`, or
`docs/CUSTOMER-KNOWLEDGE.md`, then run:

```bash
python3 webchat-service/scripts/build_knowledge_index.py
```

Every customer-audience section must be final copy that can be rendered verbatim. The generator
rejects internal role guidance in customer evidence; behavioural instructions belong in planner
sources or application policy.

Dynamic stock, price, service, dealership, offer, and slot facts must remain in tools, not this file.
Architecture/widget documents, credential-bearing integration examples, and resettable seed data are
intentionally excluded from model evidence.
