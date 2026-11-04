# GW31 Design Review

Reviewed: `design.md` against `requirements.md`, cross-checked with the shipped GW30 adapter (`dispatch/vector_adapter.py`, `vector_registry.py`, `vector_transports/*`), `resolve/conflict.py`, `edge/phase_resolvers.py`, `edge/input_enforcement.py`, `edge/dev_components.py::bind_surface`, `admit/identity.py`, and the size of `runtime/resources.py` (800) and `edge/routes.py` (799).

Verdict: CHANGES_REQUESTED (0 HIGH, 4 MEDIUM, 4 LOW).

## Findings

### F-1 (MEDIUM) Req 8.1 "durably queued" is contradicted by D-9, and no requirement clarification amends it
Req 8.1 and the Async_Ingest glossary entry require 202 only after the job is durably queued. D-9 delivers a bounded in-process store and says accepted jobs are lost on process exit. D-9 is honest, but requirements.md has clarifications C-1..C-6 and none covers this. The coder and tester will read Req 8.1 and the design as saying different things.
Fix: add requirements Clarification C-7: "In GW31 'durably queued' means recorded in the injected `IngestJobStore`. The shipped in-memory store is not durable across process exit. A store-backed implementation is GW36. Evidence must state which store ran." Also add a test that a store whose `put` returns failure yields no 202 (a refusal with a declared code, for example `rag_unavailable`).

### F-2 (MEDIUM) Generator output scan is not specified (detector, function, redaction-off behaviour)
Generator step 6 says "Output scan (stage `generator`, `Phase.OUTPUT`, `Surface.RAG`) over the answer and every chunk text" and "REDACT applies and byte-verifies". Three gaps:
1. No scan function is named. `scan_deltas` in `edge/input_enforcement.py` is the input-delta helper. Chat's output side uses `output_detector`, typed `ErrorFrameScanner`, not `InputDetector`. `RagDeps` has `detector_tier1` only, and `detector_for` returns `chat.input_detector`. It is unclear which detector runs on output.
2. With `rag_redaction_enabled = false`, step 2 says REDACT is not applied, but step 6 says REDACT applies. Req 14.4 says only BLOCK is preserved. The two steps conflict.
3. The chunk text was already scanned at retriever and ranker, so rescanning every chunk at the generator stage needs a stated purpose (stage-scoped rules for `generator` must fire on live requests, Req 6.5). The scan unit is not stated either: one call per chunk or one for the whole output.
Fix: state in the design that the generator scan reuses `scan_deltas` with `resolver_at(..., Phase.OUTPUT, Surface.RAG, "generator")` and the same `detector_for` result, one call per outgoing string (answer, then each chunk). Say whether `RagDeps` needs an `output_detector`. Gate step 6 on the same `rag_redaction_enabled` condition as step 2: when false, REDACT is recorded but not applied, BLOCK withholds. Add a test for each branch.

### F-3 (MEDIUM) `rag_connector_unsupported` is referenced but undefined, and its check has no owner
D-13 says that if a transport cannot return the original id, the connector is marked unsupported "by a fail-closed `rag_connector_unsupported` check at wiring time". That code is not in the posture additions or in the error table. No module, function, trigger or point in time is named for the check. Production wiring is GW36, so this check would never run in GW31. The id round-trip test only covers Chroma, Pinecone, Elastic and pgvector fakes. Milvus, Mongo, Redis, Weaviate and Qdrant (which uses `as_point_id`) are not covered. I confirmed in the code that Qdrant, Weaviate, Elastic and Redis return `rid` from the payload or fields, so those probably round-trip, but the design must not leave it to the coder.
Fix: either (a) delete the wiring-time check and make the round-trip test cover every transport that has a fake client, with a coverage assertion enumerating `vector_transports/*_transport.py` and an explicit expected-skip list; or (b) define `rag_connector_unsupported` (code, HTTP 503 row, a function `assert_rag_capable(adapter)` in `dispatch/rag_retrieval.py`, and where `RagDeps` construction calls it). Choose one in the design.

### F-4 (MEDIUM) "Every returned id misses the ChunkStore" returns an empty success
Retriever step 4 says that when every returned id misses, the response is an empty context with a rate-limited ERROR log. The design itself calls this "a sign that the ChunkStore was lost". With an in-process store and several workers or a restart, ingest on one process is invisible to the next, so a query returns HTTP 200 with an empty context. That is the "empty success" the fail-closed rules (Req 1.6, 3.2, and the design's own row "Adapter: any non-OK code, no empty result") forbid, and the client cannot tell a store loss from a true no-match.
Fix: when the adapter returned at least one id and ALL ids miss the ChunkStore, return `rag_chunk_store_unavailable` (503) instead of 200. A partial miss keeps the current drop-and-count behaviour. Add a test and update P16. Add a line to D-9/D-13 that the in-memory ChunkStore is single-process only, and that the dev profile must run one worker.

### F-5 (LOW) Code `rag_context_blocked` is in the error table but never produced
No flow step emits it. Remove the row or name the emitting condition (for example, all chunks BLOCKed under a strict mode, which the design does not have). An unreachable code invites an untested path.

### F-6 (LOW) Relevance normalisation vs. threshold semantics
Relevance is `(cos+1)/2`, so unrelated vectors score about 0.5. A threshold value below 0.5 filters nothing for orthogonal chunks. Req 13.2 says "normalised relevance", so it is allowed, but state in the design that the default threshold constant must be chosen with this mapping and recorded with its source in the docstring. The runbook gives no number (requirements assumption 3).

### F-7 (LOW) `compile_plan(..., contract=...)` signature change
D-5 adds `rag_settings` and `contract` keyword parameters. Name the existing callers of `compile_plan` and confirm that none breaks (defaults keep them as is). Add the callers to the test list so the existing compile tests are visibly unchanged.

### F-8 (LOW) Lowercase-only org id refusal
D-3 refuses any org id with uppercase on RAG routes. That is a fail-closed, documented choice. Add a test using the dev-profile Principal org id to show it is lowercase, so local runs do not hit `rag_namespace_refused` on the first request.

## Verified Assumptions (checked against code)
- GW30 protocol has exactly six operations plus `capabilities()`; no drop-collection (matches D-12).
- `VectorRecord` has no score field; `records_from_scored` sets metadata to an empty mapping on query (matches the ChunkStore decision, D-13).
- Transport name transforms: `gw30_` prefix on Chroma, Milvus, Mongo, pgvector (`_table`), Elastic (also `.lower()`); `safe_name` maps non-alphanumerics to `_`. The D-3 table matches.
- `VectorRegistry.select` does an exact-key lookup and returns a miss `AdapterResult`, no fallback (matches Retriever step 1).
- `PlanResolver` has `stage: str | None`; `_stage_admits` in `resolve/conflict.py` matches stage exactly and skips staged rules when `stage is None` (supports Req 6 and D-8).
- `resolver_for` accepts `surface=`; `bind_surface` binds only for `DeterministicFindings` (matches the D-1/Query step 4 caveat and the wiring-time assertion).
- `principal_of` constructs `Principal` with exactly five fields and raises `StoreDataUnavailable` on a malformed record (supports the `ProjectInvalid` subclass plan).
- `runtime/resources.py` is 800 lines and `edge/routes.py` is 799, so the size-gate edit plan is necessary and the stated approach (mixin file, net-zero routes edit) is workable.
- Layering: no new import from `egress` to `detect`; stage modules in `dispatch` receive scanners as injected callables; HTTP objects stay in `edge/rag*.py`.
- Six settings are all named and each has type, default, validation and use site (D-5).
- Test plan is concrete: named files, 16 properties with generators, iteration floors, and per-part gate focus.

## Unverified / Wrong Assumptions
- Whether the Milvus, Mongo and Redis transports return the original record id on query was not confirmed for every transport (F-3).
- The design's claim that chat's output side can be reused for RAG generator output is unverified; chat's `output_detector` has a different type (F-2).
- Whether any existing caller of `compile_plan` passes positional arguments after `rag_settings` is not verified (F-7).
- Behaviour under multiple workers for in-memory ChunkStore and job store is assumed single-process (F-4).
