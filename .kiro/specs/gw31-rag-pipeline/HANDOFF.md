# GW31 RAG pipeline: handoff

Branch `gw31-rag-pipeline`, worktree `.worktrees/gw31`. Rebased onto `suraj-revamp` (1634da0c): already up to date, no conflicts, no rebase changes. Not pushed, not merged, worktree not removed.

## What landed
- Domain and plan: RAG value-codes, `Principal.project`, `ExecutionPlan.rag`; `RagBounds` mixin (`runtime/resources_rag.py`); rag settings in plan document, compiler, delta, projector (`plan/rag_settings.py`).
- Dispatch (pure modules): namespace, chunks, embedder, ranker, context; retrieval over `ChunkStore`; async ingest worker; dev in-memory vector store (`dispatch/rag_dev_store.py`).
- Edge: RAG stages (`edge/rag_stages.py`), routes and entries (`rag.py`, `rag_entries.py`), vector/admin/ingest handlers (`rag_vector.py`, `rag_admin.py`, `rag_ingest.py`), shared plumbing (`rag_common.py`, `rag_deps.py`), dev wiring, stage-aware detector.
- Admin ledger rows for `/v1/rag/*` and `/v1/vector/*` (retire -> build, owner GW31), config-key inventory regenerated, provenance-safe top_k cap.
- Tests: old rag/vector 501 assertions replaced with the declared `rag_unavailable` 503; route, stage, property and position-exact redaction tests; local L31 gates (injection recall, duplicate flood, two projects, loop lag).
- Spec docs under `.kiro/specs/gw31-rag-pipeline/` (design, requirements, tasks, evidence).

## Offline gate results (copied from tasks/offline-gate.md)
# GW31 offline gate (no live Docker/staging gate)

Run from gateway_v2/ with /work/suraj/AI_Mesh_Firewall/gateway_v2/.venv/bin (CI invocation per .github/workflows/gateway-v2.yml). Output trimmed to summary lines.

## pytest -q -p no:randomly (full offline suite)
```
=========================== short test summary info ============================
FAILED tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean
1 failed, 3175 passed, 105 skipped, 7 warnings in 605.61s (0:10:05)
57:E       AssertionError: assert [TenantScaleV... O(changes)')] == []
59:E         Left contains one more item: TenantScaleViolation(path='/work/suraj/AI_Mesh_Firewall/.worktrees/gw31/gateway_v2/gateway_v2/dispatch/vector_transports/pinecone_transport.py', symbol='keys', line=140, reason='whole-collection store read: cannot be O(changes)')
```
## mypy --strict
```
Success: no issues found in 299 source files
```
## ruff check gateway_v2 state_control lint tests
```
All checks passed!
```
## lint-imports
```

Gateway v2 layers KEPT
Resolve imports only domain KEPT
No HTTP framework outside edge KEPT

Contracts: 3 kept, 0 broken.
```
## AST gate CLIs (trees: gateway_v2, state_control) - all exit 0
```
python -m lint.check_sizes gateway_v2 -> exit 0
python -m lint.check_http_outside_edge_resolve gateway_v2 -> exit 0
python -m lint.check_no_module_mutable gateway_v2 -> exit 0
python -m lint.check_capacity_literals gateway_v2 -> exit 0
python -m lint.check_frozen_dataclasses gateway_v2 -> exit 0
python -m lint.check_dispatch_provenance gateway_v2 -> exit 0
python -m lint.check_sizes state_control -> exit 0
python -m lint.check_http_outside_edge_resolve state_control -> exit 0
python -m lint.check_no_module_mutable state_control -> exit 0
python -m lint.check_capacity_literals state_control -> exit 0
python -m lint.check_frozen_dataclasses state_control -> exit 0
python -m lint.check_dispatch_provenance state_control -> exit 0
```
## check_tenant_scale gateway_v2 -> exit 1
```
gateway_v2/dispatch/vector_transports/pinecone_transport.py:140: keys: whole-collection store read: cannot be O(changes)
1 tenant-scale violation(s). See §2.1 rule 1 and R2-02: no work proportional to tenants, keys or records on a serving path.
```

**PRE-EXISTING, not GW31:** the single violation is in dispatch/vector_transports/pinecone_transport.py (GW30 commit 564e2ecf, in this branch's base; GW31 diff touches no file in vector_transports). The same violation is the one failing pytest test (tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean). No GW31 code was changed by this gate.

## Design deviations (copied from tasks/deviations.md)
# GW31 deviations from design.md

Each item is a point where implementation showed the design was wrong or unworkable. design.md was
corrected in the same commit series (see "Implementation deviations" appended to design.md).

1. **Customer-rule stage scoping never reached the detector (design D-2).** The design said stage
   scoping is purely a resolver concern (`PlanResolver(stage=...)`). In the code,
   `detect/conditions.py::_applies` ALSO filters on `pipeline_stage` inside `scan_conditions`, and
   `DeterministicFindings.findings_on` passed no stage, so a rule with `pipeline_stage` set never
   produced a finding on any path. Fix: `findings_on(text, surface, stage)` and
   `bind_surface(detector, surface, stage=None)` gained an optional `stage` (default `None`, so every
   existing caller is unchanged). All RAG scans pass their stage to both detector and resolver.
2. **Generator output scan needs the OUTPUT detector (design step 6 "identical detector").**
   `DeterministicFindings.phase` selects which customer conditions it evaluates, so the input
   detector never fires output-scoped rules. The generator scan uses `chat.output_detector or
   chat.error_scanner` (as MCP does); `RagDeps` gained optional `detector_tier1_output` for the
   Tier-2-off case. Design said `RagDeps` gains no output detector; that was wrong.
3. **Dev plan loaders dropped `rag_settings`.** `edge/dev_components.py::load_dev_plan` and
   `edge/dev_plan_feed.py::_compile` called `compile_plan` without the settings, so a published
   `rag_enabled: false` was ignored in the dev profile (the F-5 hazard the design describes for
   `plan_from_record`). Both now pass `rag_settings=document.rag_settings`. The design's caller list
   missed them.
4. **Dev store is not `Stub_Vector_Connector`.** The shipped stub replays a script and cannot hold
   data. `dispatch/rag_dev_store.py::InMemoryVectorAdapter` (dict-backed, empty metadata, no
   returned vector, like the real transports) backs the dev profile instead.
5. **`build_dev_rag(env, contract, *, deps=...)`** takes the dev deps mapping (it needs
   `phase_resolvers` and `input_detector`/`output_detector`) instead of only `phase_resolvers`.
6. **Dev grounder reports `ok`.** The dev profile has no Tier-2 backend and grounding is an
   availability gate only, so without a dev grounder every default dev query would be refused
   `rag_grounding_unavailable`. Production wiring (GW36) supplies the real one.
7. **`max_project_len` is not injected at boot.** `edge/state_runtime.py`'s key-fetch closure has no
   `ResourceContract` in scope. `principal_of(..., max_project_len=)` exists and is tested, and
   `edge/rag_common.py` re-checks the length against `rag_bounds()` on every RAG route (design D-4
   case (b) is therefore the only live case until GW36 threads the contract in).
8. **No new allowed file for capacity literals.** The design allowed `runtime/resources_rag.py`
   to hold RAG numbers. `check_capacity_literals` passes without adding it to `ALLOWED_REL`
   (it only flags capacity-named keywords), so the gate is unchanged. The numbers are field
   defaults of `RagBounds`, never bare literals elsewhere.
9. **All-miss rule lives in `dispatch/rag_retrieval.retrieve`**, not `rag_stages.run_retriever`
   (same observable behaviour, covers both routes, easier to test).
10. **Ledger gate scan root.** `lint/admin_ledger.py` scanned only `edge/routes.py` for gateway-v2
    callers. Because routes now live in `edge/rag_entries.py`, that file was added to the scan roots
    and the `/v1/rag/*` and `/v1/vector/*` ledger rows were flipped from `retire` to `build`
    (owner GW31). `config-keys/firewallconfig.json` was regenerated with `--write`: four RAG
    settings are now `quoted_in_gateway_v2: true`.
11. **Redaction verification is position-exact.** The first design wording ("raw span absent from
    the output") raised false alarms when the raw value also occurs legitimately elsewhere.
    `redact_text` now checks the output equals the text with each span swapped for its replacement.
12. **Admin and vector routes were not in design's size plan:** `rag_common.py` (shared plumbing) was
    added so each handler module stays small.

## Not done (explicitly out of this run)

- L31-3 against real Chroma: there is no transport-to-connector wiring in GW31 (skipped, reason in
  the test). Live Docker/staging gate NOT run, as instructed.
- Req 6.5 publish/block/delete/allow rule cycle against a live plan feed: covered only by per-stage
  rule tests on the in-process plan; no plan-feed publisher cycle test.
- Streaming and `Answerer`-based generation beyond the optional seam.

## Live gate
The live Docker/staging gate was NOT run. Only the offline gate above was executed. The post-rebase gates were not re-run because the rebase was a no-op.

## Remaining for the human
- Decide on the pre-existing `check_tenant_scale` failure (`pinecone_transport.py:140`, from GW30), which is the one failing pytest test.
- Run the live Docker/staging gate (including L31-3 against real Chroma and the plan-feed rule cycle for Req 6.5).
- Merge `gw31-rag-pipeline` into `suraj-revamp`.
- Remove the worktree `.worktrees/gw31` afterwards.
- Untracked `tasks/review.md` and `tasks/review.json` are not committed.
