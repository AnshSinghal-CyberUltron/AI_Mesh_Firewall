# RAG Validation Report

## Flow under test

```
Upload/index → POST /v1/rag/ingest → POST /v1/rag/query → responses.create synthesis
```

## Test procedure

1. Index sample document in **RAG** tab
2. Query with relevant question — verify grounded answer
3. Query with injection attempt — expect block or empty retrieval
4. Cross-tenant collection name — expect 403

## SDK calls

```python
client.post("/rag/ingest", body={"collection": "demo_knowledge", "documents": [...]})
client.post("/rag/query", body={"collection": "demo_knowledge", "query": "...", "top_k": 4})
```

## Pass criteria

- Ingest returns 200 with receipt metadata
- Query returns `documents[]` with scan_verdict
- Synthesized answer cites retrieved content
- Injection queries blocked with `rag_query_blocked` or similar

## Infrastructure

- Chroma profile: `docker compose --profile chroma up -d`
- Or org-level vector provider configured in control plane
