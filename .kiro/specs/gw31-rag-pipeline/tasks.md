# Implementation Plan: GW31 four-stage RAG pipeline

Worktree: `/work/suraj/AI_Mesh_Firewall/.worktrees/gw31` (branch `gw31-rag-pipeline`). All paths below are relative to `gateway_v2/` (the package root containing `pyproject.toml`) unless prefixed `docs/` or `.kiro/`. Design is normative: `.kiro/specs/gw31-rag-pipeline/design.md` (D-1..D-14, P1..P16, size-gate plan, error table). Requirements: `requirements.md` (including clarifications C-1, C-9). Do NOT run or claim the live Docker/staging gate. Do not rebuild or amend GW30 (`dispatch/vector_*`).

## Environment and gate commands (used by every task)

No venv exists and the host python has no pytest. Create one once (outside the repo) and reuse it:

```bash
python3 -m venv /tmp/gw31venv && /tmp/gw31venv/bin/pip install -e "./gateway_v2[dev]"   # run from the worktree root; add extras only if tests skip for a missing dep
```
If pip has no network, run the gates in a container instead: `docker run --rm -v "$PWD":/w -w /w/gateway_v2 python:3.12 bash -c '<pip install -e .[dev] && gates>'`.

Let `PY=/tmp/gw31venv/bin/python`. From `gateway_v2/`:

```bash
# G-LINT (fast, run after each task)
for g in check_sizes check_http_outside_edge_resolve check_no_module_mutable check_capacity_literals check_frozen_dataclasses; do $PY -m lint.$g gateway_v2 || echo FAIL $g; done
$PY -m lint.check_tenant_scale gateway_v2; $PY -m lint.check_dispatch_provenance gateway_v2
/tmp/gw31venv/bin/lint-imports && /tmp/gw31venv/bin/ruff check gateway_v2 state_control lint tests && /tmp/gw31venv/bin/mypy --strict
# G-FULL (end of each phase): $PY -m pytest -q   (0 failures; no test removed or weakened; record pass/skip counts vs baseline)
```
Also at the end: `$PY -m lint.admin_ledger`, `$PY -m lint.check_console_inventory`, `$PY -m lint.check_secret_headers` (check the module's CLI args before running).

Conventions: frozen+slotted dataclasses, `MappingProxyType` for mappings, no module-level mutable state, no capacity literal outside `runtime/resources.py` / `runtime/resources_rag.py`, seeded `random.Random` properties tagged `# Feature: gw31-rag-pipeline, Property N: <name>`, pure properties P3/P4/P5/P15 at least 10,000 iterations, P6/P7/P8/P11/P12/P13 at least 1,000, P1/P2/P9/P10/P14/P16 at least 100. Commit locally after each task (specific files only, never push).

Dependency order: T0 -> T1 -> T2 -> T3 -> (T4, T5 independent but sequential) -> T6 -> T7 -> T8 -> T9 -> T10 -> T11 -> T12 -> T13.

---

- [ ] 0. Baseline and size/ledger recon.
      Create the venv, run G-FULL and G-LINT on the untouched tree and record pass/skip counts and `check_sizes` line counts for `runtime/resources.py` (800), `edge/routes.py` (799), `edge/app.py` (800), `edge/dev_profile.py`, `edge/surface_entries.py`. Run `grep -rn "501" tests | grep -i "rag\|vector"` and `grep -rn "compile_plan(" .` to confirm the call-site lists in design D-5 and "Pre-existing artefacts". Read `lint/admin_ledger.py` to decide ledger-row vs scan-root fix. Write findings to `.kiro/specs/gw31-rag-pipeline/tasks/baseline.md`.
      Files: `.kiro/specs/gw31-rag-pipeline/tasks/baseline.md`
      Verify: baseline.md lists counts; G-FULL baseline recorded (pre-existing failures named, if any).

- [ ] 1. Domain layer: RAG types, codes, Principal.project, ExecutionPlan.rag.
      Add `domain/rag.py` (`RagSettings`, `Namespace`, `TrustLevel`, `Chunk`, `ScoredChunk`, `StageNote`, stage-name constants). Add all RAG value-codes from the design error table to `domain/posture.py` (codes only). Add `Principal.project: str = "default"` in `domain/identity.py` and `ExecutionPlan.rag: RagSettings = RagSettings()` (default last, after `feed_seq`) in `domain/plan.py`.
      Files: `gateway_v2/domain/rag.py`, `domain/posture.py`, `domain/identity.py`, `domain/plan.py`, `tests/domain/test_gw31_domain_types.py`
      Verify: `$PY -m pytest -q tests/domain tests/plan tests/admit` all pass (existing tests unchanged); G-LINT clean.

- [ ] 2. Runtime bounds: `RagBounds` mixin.
      Create `runtime/resources_rag.py` (mixin, mirroring `runtime/resources_vector.py`) with every bound in the design capacity table (`rag_bounds()`, `rag_max_documents_ceiling()`, `rag_default_relevance_threshold()` > 0.5, `wire_max_length = 58` with docstring citing the D-3 connector table, segment maxima 36/32/32, ChunkStore/ingest/job-store bounds, `shingle_width`, `canary_bytes`, id bounds). Change `ResourceContract` to inherit the mixin with a net-zero line change in `runtime/resources.py` (do not touch `snapshot()`). Fail-closed with `CapacityUnavailable` on non-positive values.
      Files: `runtime/resources_rag.py`, `runtime/resources.py`, `tests/runtime/test_gw31_rag_bounds.py`
      Verify: `$PY -m pytest -q tests/runtime`; `$PY -m lint.check_sizes gateway_v2` (resources.py still <= 800); `$PY -m lint.check_capacity_literals gateway_v2` exits 0.

- [ ] 3. Plan layer: rag settings parse/validate, effective_settings, document round trip, compiler and delta/projector pass-through.
      Add `plan/rag_settings.py` (`parse_rag_settings` structure/type validation raising `CompileError`; `effective_settings(plan, contract)` clamping `rag_max_documents` down with `settings_clamped`; `EffectiveRag`). Add keyword-only `rag_settings` to `compile_plan` (after `epoch`), `PlanDocument.rag_settings`, encode/decode, and hash mixing only when non-default (existing hashes unchanged). Pass `rag_settings=document.rag_settings` in `plan/delta.py::plan_from_record` and `state_control/projector.py`. No contract parameter anywhere (D-5/F-2).
      Files: `plan/rag_settings.py`, `plan/compiler.py`, `plan/document.py`, `plan/delta.py`, `../state_control/projector.py` (path relative to `gateway_v2/`; confirm location), `tests/plan/test_gw31_rag_settings.py`, `tests/plan/test_gw31_settings_roundtrip.py` (P7, P12)
      Verify: `$PY -m pytest -q tests/plan tests/state_control` including the explicit unchanged-and-green subset listed in D-5 (`test_applied_version`, `test_dev_plan_publish`, `test_firewall_v2_map`, `test_gw26_org_plan`, `test_lgw05`, `tests/detect/test_gw26_conditions.py`, `test_v2_leaks_*`, `tests/egress/test_output_streaming_modes_gw13.py`); a signed body with `{"rag_enabled": false}` yields `plan.rag.rag_enabled is False` via `plan_from_record`; import-linter passes.

- [ ] 4. Identity: project on Principal.
      In `admit/identity.py` add `ProjectInvalid(StoreDataUnavailable)`, read optional `project` in `principal_of` (absent/null -> `default`; non-string/empty/charset `[a-z0-9_-]` violation -> `ProjectInvalid`; optional `max_project_len` keyword), inject `contract.rag_bounds().project_max_length` in the boot `fetch` closure. Update `edge/dev_components.py::_identity_for`. In `edge/identity_resolver.py` catch `ProjectInvalid` before `StoreDataUnavailable` and render `project_invalid`.
      Files: `admit/identity.py`, `edge/identity_resolver.py`, `edge/dev_components.py`, `edge/errors.py` (row only if needed here), `tests/domain/test_gw31_principal_project.py` (P11)
      Verify: `$PY -m pytest -q tests/admit tests/edge -k "identity or principal or project"` plus full `tests/admit`; existing identity tests unchanged and green.

- [ ] 5. Dispatch pure modules: namespace, chunks, embedding, ranker, context.
      Create `dispatch/rag_namespace.py` (`validate_segment`, `encode_segment`, `wire_collection`, `parse_wire`, `build_namespace`, `NamespaceRefused`), `dispatch/rag_chunks.py` (`ChunkEntry`, `ChunkStore` protocol, bounded `InMemoryChunkStore` that refuses when full, idempotent `put_many`), `dispatch/rag_embedding.py` (`Embedder`, `DeterministicHashEmbedder`), `dispatch/rag_ranker.py` (cosine relevance, threshold, MAD anomaly, fixed-point near-duplicate cap, trust re-rank, deterministic tie-break), `dispatch/rag_context.py` (binding check, `merge_decisions`, `mint_canary`/`canary_present`, `ContextFrame` holding sha256 only, `Grounder`/`GroundingVerdict`/`Answerer` protocols). None may import `detect`, `resolve`, `plan`, or HTTP.
      Files: the five modules above; tests `tests/dispatch/test_gw31_namespace.py` (P3, transport-naming test importing every `vector_transports/*_transport.py` naming function with a coverage assertion; UUID-org + `default` + 6-char collection passes, 7 fails), `test_gw31_ranker.py` (P4, P5, P6), `test_gw31_chunks.py`, `test_gw31_context.py` (P15, P13 byte-verify pure parts)
      Verify: `$PY -m pytest -q tests/dispatch -k gw31`; `lint-imports` shows dispatch layer contract kept; G-LINT clean.

- [ ] 6. Dispatch: retrieval and ingest worker.
      Create `dispatch/rag_retrieval.py` (`retrieve`, `Retrieval`, `listed_name`, all-miss / partial-miss ChunkStore logic helpers; metadata is never read, only `record.id`; `records is None` -> `rag_retrieval_invalid`) and `dispatch/rag_ingest.py` (`IngestJob`, `IngestJobStore` protocol + bounded in-memory store, `IngestQueue` with injected `spawn`/`clock`/`token_source`, `write_batch` in the single order ensure-collection -> embed -> `chunk_store.put_many` -> `adapter.upsert`, `WROTE_NOTHING_CODES` rollback rule, `stored` only on observed OK with matching count, `submit` non-blocking with `rag_ingest_overload`). Namespace computed at accept time and carried in the job.
      Files: `dispatch/rag_retrieval.py`, `dispatch/rag_ingest.py`, `tests/dispatch/test_gw31_retrieval.py` (Stub connector returning `metadata={}` and `vector=()`; id round-trip over every transport with an in-process fake, plus `EXPECTED_SKIP` coverage assertion and source-text pin of the id line; `listed_name` incl. `Gw30_`; P16), `tests/dispatch/test_gw31_ingest.py` (P8, P14 pure parts; ensure-collection cases; N failed ensures leave ChunkStore count 0; adapter writes-then-raises / timeout / short count keep entries and a later query returns documents)
      Verify: `$PY -m pytest -q tests/dispatch -k gw31`; full `tests/dispatch` (GW30 tests untouched and green); G-LINT.

- [ ] 7. Edge: errors, deps, stages.
      Add `_ENVELOPE` rows for every new code in `edge/errors.py` (HTTP/type per the design table; `rag_ingest_overload` carries `Retry-After`). Create `edge/rag_deps.py` (`RagDeps`, no `scan_executor`), `edge/rag_stages.py` (`resolver_at` fail-closed to `rag_internal_error` on non-`PlanResolver`; `detector_for` with Tier-1/Tier-2 selection and `bind_surface`; `run_query`, `run_retriever`, `run_ranker`, `run_generator` per the design: one `await chat.executor.run(...)` per chunk, sequential; per-chunk ranker scan with `merge_decisions`; generator output scan per string with `Phase.OUTPUT`, stage `generator`, redaction gate, grounding availability gate, optional answerer, canary check). Scans of every stage pass the stage name.
      Files: `edge/errors.py`, `edge/rag_deps.py`, `edge/rag_stages.py`, tests `tests/edge/test_gw31_errors.py`, `test_gw31_query_stage.py`, `test_gw31_retriever_stage.py` (incl. all-miss -> `rag_chunk_store_unavailable`, partial miss, empty result), `test_gw31_ranker_stage.py`, `test_gw31_generator_stage.py` (each branch listed in design step 6), `test_gw31_stage_scoping.py` (P10: rule fires only in its stage, empty stage fires in all four, chat-only rule does not fire; Tier-2 on/off), plus isolated unit tests for each of the six settings
      Verify: `$PY -m pytest -q tests/edge -k gw31`; `$PY -m lint.check_http_outside_edge_resolve gateway_v2`; `lint-imports`.

- [ ] 8. Edge: RagRoute lifecycle, vector routes, admin/collections, ingest handlers.
      Rewrite `edge/rag.py` (`RagRoute`, `_Ctx`, `_Refuse`, body-bound -> JSON -> identity -> plan -> settings -> `rag_enabled` -> project re-check -> admit/quota -> pipeline; `finally` audit via `emit_chat_audit` and holds release; outermost exception -> `rag_internal_error`, class name only). Add `edge/rag_vector.py` (`/v1/vector/query|upsert|delete`, `GET /v1/vector/config` with no credential fields; upsert shares `write_batch`; `top_k` validation), `edge/rag_admin.py` (collections GET/POST/DELETE, documents DELETE, `/v1/admin/rag/collections`; whole-collection drop without `ids` -> `unsupported_operation` with zero adapter calls; list filtered by `listed_name` + `parse_wire` against the effective namespace), `edge/rag_ingest.py` (`POST /v1/rag/ingest` 202 only after `store.put` True and enqueue; 413/400/503 paths; `GET /v1/rag/ingest/{job_id}` tenant+project-scoped 404). Keep each module < 600 lines.
      Files: `edge/rag.py`, `edge/rag_vector.py`, `edge/rag_admin.py`, `edge/rag_ingest.py`, tests `tests/edge/test_gw31_routes.py` (P1, P2, P9, P13; lost ChunkStore never empty 200; 401 without key; `rag_disabled`), `test_gw31_admin_routes.py`, `test_gw31_ingest_routes.py` (job-store put False/raises -> 503; P14), `test_gw31_leak_properties.py`
      Verify: `$PY -m pytest -q tests/edge -k gw31` passes; no error body, log line or audit detail contains chunk text, canary or credential (P9).

- [ ] 9. Edge wiring and size-gate edits.
      Create `edge/rag_entries.py` (`rag_entries(chat, rag)`; `rag is None` -> same paths answering `rag_unavailable` 503; prefix route `("GET", "/v1/rag/ingest/")`), merge in `edge/surface_entries.py`, add `rag: RagDeps | None = None` to `build_routes` (`edge/routes.py`, net <= 0, remove the two `_placeholder` entries) and `build_app` (`edge/app.py`, net <= 0 lines, spans <= 120). Create `edge/dev_rag.py` (`build_dev_rag`, `attach_dev_surfaces(deps, mcp, env, contract)`) and replace the two-line `if mcp is not None:` block in `edge/dev_profile.py` with one call (net -1). Add an `ast` unit test asserting `build_app`, `build_dev_dependencies` and every new function span <= 120 lines.
      Files: `edge/rag_entries.py`, `edge/surface_entries.py`, `edge/routes.py`, `edge/app.py`, `edge/dev_rag.py`, `edge/dev_profile.py`, `tests/edge/test_gw31_dev_wiring.py` (dev profile boot: `/v1/rag/query` served not 501/503, exactly one `ScanExecutor.__init__`; default dev org `dev-org` not refused with `rag_namespace_refused`; chat route without `phase_resolvers` refuses RAG with 500 and zero adapter calls), `tests/lint/test_gw31_no_placeholder.py` (no `_placeholder("rag")` or `("vector")` remains; no `rag_connector_unsupported`; no builder of `_s` joins outside `wire_collection`)
      Verify: `$PY -m lint.check_sizes gateway_v2` (resources.py, routes.py, app.py <= baseline; all <= 800 / 120); `$PY -m pytest -q tests/edge -k "gw31 or dev"`.

- [ ] 10. Update pre-existing 501 assertions and ledgers.
      Replace each 501 assertion for rag/vector with the new declared behaviour (never delete or loosen): `rag=None` -> `rag_unavailable` 503, deps injected + no key -> 401. Files per design: `tests/edge/test_gw16_app.py`, `test_gw16_routes.py`, `test_gw16_matcher_edges.py`, `test_lgw15_app.py`, `test_responses_route.py`, `tests/openai_conformance/test_mcp_rag_vector_shapes.py`, `test_live_tcp_8300.py` (edit only; do not run the live test). Fix `docs/plans/evidence/2026-10-26-gw14b/admin-ledger/routes.json` rows (retire -> build, caller `edge/rag_entries.py`) or ledger scan roots per the T0 decision.
      Files: the test files above, admin-ledger routes.json (path relative to repo root)
      Verify: `$PY -m pytest -q tests/edge tests/openai_conformance tests/lint` passes; `$PY -m lint.admin_ledger` and `$PY -m lint.check_console_inventory` exit 0.

- [ ] 11. L31 local-half tests and Chroma local run.
      Add tests for L31-1 (indirect-injection fixture family, recall printed, on `/v1/rag/query` and `/v1/vector/query`), L31-2 (near-duplicate flood capped to post-cap share), L31-3 local (ingest to never-created collection then query; Chroma marked and skipping cleanly when not importable), L31-4 (two projects in one org read only their own collections), and the Req 6.5 per-stage rule cycle (publish, block, delete, allow) against the in-process plan. Record loop lag with 20 chunks at `chunk_text_bytes`.
      Files: `tests/edge/test_gw31_live_local.py` (name `local` not `live`; no Docker/staging)
      Verify: `$PY -m pytest -q tests/edge/test_gw31_live_local.py -rs` passes with skips explained.

- [ ] 12. Evidence and docs.
      Write `docs/plans/evidence/<today>-gw31/README.md` stating what ran (Stub, local Chroma yes/no, in-memory ChunkStore/job store, single-worker limit), what is deferred (cloud stores, fleet capacity, live Docker gate NOT run, GW36 production wiring, drop_collection follow-up, `rag_settings` publisher), gate outputs and counts vs T0 baseline. Add GW31 entry to the changelog pointer location used by sibling cards (check how GW12/GW14d recorded theirs; do not touch unrelated sections of `AGENTS.md`).
      Files: `docs/plans/evidence/<date>-gw31/README.md`
      Verify: file exists and numbers match the actual last gate run.

- [ ] 13. Final offline gate.
      Run G-LINT, G-FULL, ledger and inventory gates on the whole tree; fix regressions; confirm no test removed or weakened (`git diff --stat -- tests` review) and every property meets its iteration floor.
      Verify: all gates exit 0; G-FULL has 0 failures and pass count >= baseline + new tests.
