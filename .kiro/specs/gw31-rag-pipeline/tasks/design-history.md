> **History only.** Every section from here to the end records past review responses. The sections above are normative. Rows marked SUPERSEDED, and any mention of `stamp_metadata`, `chunk_metadata_bytes` or a `contract` argument to `plan_from_record`, describe removed designs.

## Responses to design review (revision pass)

| # | Sev | Response |
| --- | --- | --- |
| 1 | HIGH | Addressed. D-3 rewritten: per-segment encoding with unambiguous `_s` separator, `org_id` validated, `parse_wire`, `wire_max_length`; P3 generator covers `-`/`_` and the `(a-b,c)` vs `(a,b-c)` pair. |
| 2 | MED | Addressed. D-12: delete-by-ids semantics; no-ids drop is `unsupported_operation` with zero calls; requirements Clarification C-1 appended. |
| 3 | MED | Addressed. One code `project_invalid` via `ProjectInvalid`; all routes; length behaviour with/without keyword; P11. |
| 4 | MED | Addressed. Size-gate edit plan (mixin base-class only, `snapshot()` untouched, routes net <= 0, `check_sizes` after each edit). |
| 5 | MED | Addressed. `_rag_*` rejected on ingest/upsert, `trust` body field defined, forgery test in P14. SUPERSEDED by pass 3 D-13: `stamp_metadata` and gateway-stamped `_rag_*` metadata were removed; `_rag_*` keys are still rejected and no client metadata is persisted. |
| 6 | MED | Addressed. Ranker scan per chunk, `merge_decisions` with `most_restrictive`, ranker BLOCK drops chunk, P15, split-secret limit documented. |
| 7 | MED | Addressed. Detector canonicalises internally (raw-text scan); `detector_for` + `bind_surface` contract with wiring-time assertion; surface-rag test under both tier2 settings. |
| 8 | LOW | Addressed. `shingle_width` in `RagBounds`. |
| 9 | LOW | Addressed. Operator note in D-3. |
| 10 | LOW | Addressed. Caller named out of scope (GW36 / console card). |
| 11 | LOW | Addressed. D-10 and requirements C-2. |
| 12 | LOW | Addressed. `wire_max_length` and boundary tests. SUPERSEDED by pass 3 D-3 (`wire_max_length` = 58, derived from 63 minus the 5-character prefix); `chunk_metadata_bytes` was removed with the metadata design (D-13 uses `chunk_text_bytes`). |

### Responses to design review (pass 3 revision)

| # | Sev | Response |
| --- | --- | --- |
| 1 | HIGH | Addressed. New D-13: GW31-owned `ChunkStore` (protocol plus bounded in-memory implementation) holds text, vector, source and trust keyed by wire collection and record id. Retriever step 4 rewritten to use only returned ids; org/project come from the Principal and the key. Ingest order, delete, store-unavailable code, facts-table row, capacity rows, requirements C-6, and tests (empty-metadata and empty-vector Stub parametrisation, miss-drop test, per-transport id round-trip, P16). The earlier `_rag_*` metadata design and `stamp_metadata` are removed. |
| 2 | MED | Addressed. Segments restricted to lowercase `[a-z0-9_-]`; uppercase refused, never folded (also for `project` in D-4); wire name asserted lowercase; P3 gains case-swapped triples; the Elastic prefix counts against the length. |
| 3 | MED | Addressed. D-3 now has the per-connector naming table read from the transports; `wire_max_length = 63 - 5 = 58` stated and derived; a transport-naming test covers every shipped transport and has a coverage assertion for new ones. Chroma start/end rule shown to hold. |
| 4 | MED | Addressed. Requirements clarification C-5 appended (amends Req 9.4, 9.5 and the Glossary). |
| 5 | LOW | Traceability only; unchanged (C-2, D-10, unknown-key rejection in D-5). |
| 6 | LOW | Addressed. D-7 states the canary limit and adds planted-canary and echoing-answerer tests. |
| 7 | LOW | Addressed. P3, P4, P5, P15 at least 10,000 iterations; others tiered. |
| - | note | The reviewer's unverified item (connectors returning a vector) is closed by D-13: the relevance vector comes from the ChunkStore, so no connector must return one. |


## Review responses (final pass)

Each claim below was checked against the code in this worktree (transport query paths, `scan_deltas` signature, `ErrorFrameScanner`, `compile_plan` callers, `DEFAULT_DEV_ORG_ID`), not assumed.

| Finding | Sev | Resolution |
| --- | --- | --- |
| F-1 durable-queue wording conflict | MED | Resolved. Requirements Clarification C-7 added: "durably queued" means recorded in the injected `IngestJobStore`; the in-memory store is not durable across process exit; evidence states which store ran. D-9 rewritten to match. `IngestJobStore.put` returns `bool`; a `False` or raise refuses with `rag_unavailable` (503), no 202, nothing queued. Tests for both branches. Error table and fail-closed table updated. |
| F-2 generator output scan unspecified | MED | Resolved. Generator step 6 specified: reuses `scan_deltas` (its `detector` is `Callable[[str], Sequence[Finding]]`, the same shape as chat's `ErrorFrameScanner`) with `detector_for(...)` bound to `Surface.RAG` and `resolver_at(..., Phase.OUTPUT, Surface.RAG, "generator")`; one call per outgoing string (answer, then each chunk); no `output_detector` added to `RagDeps`; purpose stated (output-phase rules and the final gate on exact outgoing bytes). Gated on `rag_redaction_enabled` identically to step 2: off means REDACT recorded as `redact_not_applied`, BLOCK still withholds. A test per branch is listed. |
| F-3 `rag_connector_unsupported` undefined | MED | Resolved by option (a): the check and the code are deleted. Reading the query path of all nine transports shows each returns the original id (Chroma/Pinecone/Milvus/Mongo/pgvector from the id column or field, Redis/Elastic/Qdrant/Weaviate from a stored `rid`). Proof is a test: id round trip through every transport with a fake, a coverage assertion over `vector_transports/*_transport.py` with an explicit `EXPECTED_SKIP` set, and a source-pin test on the id-source lines. |
| F-4 empty success on lost ChunkStore | MED | Resolved. Adapter returned at least one id and all miss gives `rag_chunk_store_unavailable` (503), not 200. Partial miss still drops and counts; zero adapter records stays a true empty success. D-13 states the single-process limit and that the dev profile runs one worker. P16, fail-closed table and tests updated. Requirements C-8 added. |
| F-5 `rag_context_blocked` never produced | LOW | Resolved. Row removed from the error table; no flow step emits it and the design has no strict mode that would. |
| F-6 relevance mapping vs threshold | LOW | Resolved. Ranker step 1 states orthogonal scores about 0.5, requires the default `_RAG_DEFAULT_RELEVANCE` to be chosen with the mapping and documented with its source, and adds a unit test that the default is above 0.5 and within `[0,1]`. |
| F-7 `compile_plan` signature change | LOW | Resolved. New parameters are keyword-only after `epoch`; callers named (4 source sites, 10 test files, all pass `org_id, drafts` positionally and the rest by keyword); none edited; the task gate runs them as an unchanged-and-green subset. |
| F-8 lowercase org id refusal | LOW | Resolved. `DEFAULT_DEV_ORG_ID = "dev-org"` is lowercase and in charset (`edge/dev_profile.py:105`); a route test builds the default dev Principal and asserts the first query is not refused with `rag_namespace_refused`. |

## Responses to design-loop review (round 2, `tasks/design-loop-review.md`)

`tasks/design-review.md` is superseded by `design-loop-review.md`; its findings are not open (F-10).

| Finding | Sev | Resolution |
| --- | --- | --- |
| F-1 501-pinned tests, ledger, gates | MED | Addressed. New "Pre-existing artefacts that change" subsection (Wiring): file list, replace-never-delete rule, ledger rows retire to build (or scan roots extended), `lint.admin_ledger`, `check_console_inventory`, `check_secret_headers` added to the offline gates. |
| F-2 app.py / dev_profile.py injection | MED | Addressed. Module table gains `edge/app.py`, `edge/dev_profile.py`, `edge/dev_rag.py`; size-gate plan covers `app.py` and `dev_profile.py` (net <= 0); Wiring states the injection path. |
| F-3 ingest to a missing collection | MED | Addressed. D-14: worker calls `create_collection` (embedder dimension) before first upsert, `VECTOR_OK` and `collection_exists` both success; 409 on the public create is expected on Pinecone; fresh-collection ingest-then-query test sequence. |
| F-4 list shapes | MED | Addressed. D-14 `listed_name` normalisation, create-to-list round-trip test per fake transport, Pinecone empty-namespace limit documented in `EXPECTED_SKIP`. |
| F-5 `rag_settings` dropped on production path | MED | Addressed. D-5: `plan_from_record` and the projector pass `rag_settings`. SUPERSEDED by round 3 F-2: no contract is passed; ceiling is clamped at request time. |
| F-6 undefined protocols, grounding meaning | MED | Addressed. `Embedder.dimension`, `Grounder`, `GroundingVerdict` (closed codes `ok`/`unavailable`), `Answerer` signatures defined; grounding stated as an availability and fail-closed gate only, scoring left to GW33. |
| F-7 effective project | LOW | Addressed. List row, `parse_wire` text and job GET use the effective `(org, project)`; job stores the effective project. Test: isolation-`org` list and job GET cases belong to `test_gw31_admin_routes.py` / `test_gw31_ingest_routes.py`. |
| F-8 id and `top_k` validation | LOW | Addressed. D-14 id bounds (`id_max_length`, `ids_per_request`, printable ASCII), `top_k` `int >= 1` else `rag_invalid_request`; rows in capacity table. |
| F-9 Pinecone limit | LOW | Addressed. Table row says unverified; 58 recorded as a conservative assumption confirmed only by the deferred cloud run. |
| F-10 stale review | NIT | Addressed. Noted above. |

## Round 1 consistency pass (earlier revision)

No design-gate existed, so this was a read-through of `design.md` and `requirements.md` against the already-resolved findings. Verified without change: layering (`Decision`/`Finding` live in `domain`, so `dispatch/rag_context.py::merge_decisions` and `domain/rag.py::ScoredChunk` import only downward; the import-linter order in `pyproject.toml` matches), no `egress -> detect` edge, clarifications C-1..C-8 match the design, the deleted `rag_connector_unsupported` and `rag_context_blocked` appear only in resolution notes. Fixed contradictions:

- Module table said `parse_rag_settings(raw)`; D-5 requires `contract`. Table now matches.
- D-7 canary check listed "every metadata value", but no client metadata is persisted or returned (D-13). Removed.
- `rag_unavailable` is also the ingest job-store failure code (D-9); the error table now says so and gives both log levels.
- Earlier-pass response rows 5 and 12 referenced `stamp_metadata` and `chunk_metadata_bytes`, removed by D-13. Marked superseded.
- `/v1/vector/config` method was unstated; now GET only.

## Responses to design-loop review (round 3, `tasks/design-loop-review.md`)

| Finding | Sev | Response |
| --- | --- | --- |
| F-1 ChunkStore rollback orphans | MED | Addressed. D-13 rollback rule: delete entries only for a declared wrote-nothing code (`WROTE_NOTHING_CODES`); raise, timeout, count mismatch and unknown codes keep entries and fail the job. P8/P16 gain write-then-raise, timeout and short-count cases followed by a query. |
| F-2 contract source for compile | MED | Addressed. D-5 split validation: compile checks structure/types with no contract; the ceiling is clamped down in `effective_settings` with `settings_clamped` in the trace. The `contract` parameters are removed from `compile_plan`, `parse_rag_settings` and `plan_from_record`; no call-site or boot change is needed. P7 and the module table updated. |
| F-3 `/v1/vector/upsert` | MED | Addressed. Vector routes: synchronous, reuses the ingest `write_batch` (ensure collection, embed, chunk put, adapter upsert), same trust, bounds, `rag_embedder_unavailable` and F-1 rule; fresh-Chroma upsert-then-query route test. |
| F-4 length arithmetic | LOW | Addressed. D-3 gives maxima 36/32/32, overhead 5, budget 53, UUID org encodes to 40 leaving 13 (6 for the collection beside `default`), compact-id advice, boundary test. |
| F-5 scan cost | LOW | Addressed. "Scan cost bound": ceiling 20, 62 worst-case detector calls, chunk scans via `ScanExecutor`, ranker inline at 190 pair tests, loop lag in L31 evidence, fail-closed on executor failure. |
| F-6 history tables | NIT | Addressed. History-only banner above the response sections. |
