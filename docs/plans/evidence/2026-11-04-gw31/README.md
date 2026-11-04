# GW31 four-stage RAG pipeline: evidence (local half)

Branch `gw31-rag-pipeline`. Spec: `.kiro/specs/gw31-rag-pipeline/`. Full gate output and counts:
`.kiro/specs/gw31-rag-pipeline/tasks/verification.md`; deviations from the design:
`.kiro/specs/gw31-rag-pipeline/tasks/deviations.md`.

## What ran

- Stub-free in-process vector adapter (`dispatch/rag_dev_store.py`, empty metadata like the shipped
  transports), `DeterministicHashEmbedder`, in-memory ChunkStore and job store, single process.
- Full offline suite: 3120 passed, 111 skipped, 1 failed (pre-existing GW30 tenant-scale finding).
- Local L31-1, L31-2, L31-4 and a loop-lag reading.

## What did NOT run (deferred)

- Live Docker/staging gate. Real Chroma (L31-3), cloud stores, fleet capacity.
- GW36 production wiring (real embedder, grounder, durable ChunkStore and job store).
- A control-plane publisher that WRITES `rag_settings` into plan documents. Settings are honoured on
  the read path (plan document, compiler, delta applier, projector, dev loaders), but nothing in
  production publishes them yet.
- GW30 follow-up: an adapter `drop_collection` (whole-collection delete returns `unsupported_operation`).

## Limits to know

- ChunkStore and job store are per process. Run one worker. After a restart or on another worker,
  queries fail closed with `rag_chunk_store_unavailable`, never an empty answer. An in-memory job
  store loses accepted jobs on exit; `GET` then answers `rag_job_not_found`, never `stored`.
- Pinecone lists only namespaces that hold vectors. `wire_max_length = 58` assumes 63 is the strictest
  store limit; Pinecone's own limit is unverified.
- Org ids with uppercase are refused on RAG routes (`rag_namespace_refused`). Hyphenated UUID org ids
  leave 6 characters for a collection name with the default project.
- The canary is a tripwire for context-frame leakage, not proof about model behaviour. GW31 calls no
  chat model by default.
