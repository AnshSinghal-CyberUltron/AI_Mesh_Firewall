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
