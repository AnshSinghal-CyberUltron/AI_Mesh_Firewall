# Implementation Plan: GW27 (45 unsupported FirewallConfig fields, build or retire by name)

Source of truth: `design.md` (approved, pass 5) and `requirements.md` in this directory. This plan
sequences the work bottom-up along the import-linter layers; it does not re-decide the design.

## Conventions (apply to every task)

- Worktree: `/work/suraj/AI_Mesh_Firewall/.worktrees/gw27`, branch `gw27-firewall-config-fields`. Use absolute paths.
  Never push. Commit locally, one commit per task, staging files by name.
- Package root for v2 is `gateway_v2/` (pyproject, `lint/`, `tests/`) and the importable package is
  `gateway_v2/gateway_v2/`. Product paths below are relative to `gateway_v2/gateway_v2/` unless they
  start with `gateway_v2/`, `control/`, `docs/` or `scripts/`.
- Layer contract: `edge > admit > plan > detect > resolve > dispatch > egress > audit > runtime > contracts > domain`.
  A task may import only what earlier tasks (or lower layers) already provide.
- Value types: `@dataclass(frozen=True, slots=True)`; module tables via `MappingProxyType`; no module-level mutable.
- Capacity literals only in `runtime/resources.py` and (after Task 2) `runtime/resources_org.py`.
  `runtime/resources.py` is exactly 800 lines: do not edit it. `edge/routes.py` is 799 lines: add
  nothing to it that is not offset by a move into a sibling module (`check_sizes` caps 800 lines/module,
  120 lines/function). `edge/openai_chat.py` is 712 lines: put new logic in `edge/org_gate.py`.
- Property tests: seeded `random.Random`, at least 10,000 iterations (house idiom, no hypothesis, `asyncio.run` where async),
  seed in every assertion message, tagged in-source:
  `# Feature: gw27-firewall-config-fields, Property N: <text>` and `# Validates: Requirements X.Y`.
- Fail closed everywhere; `fail_open_total` stays 0. No new dependency.
- Stop before any live Docker/staging gate (L27-1, L27-2, live Postgres EXPLAIN arm, live entry-size model).
  Live arms stay `skipif` without `AMF_PG_DSN` and are NOT run. Evidence for live gates is left as an OPEN item.

## Offline gate (G)

Container-run style: no `.venv` exists in the worktree, so run in a throwaway Python 3.12 container
(mirrors `.github/workflows/gateway-v2.yml`). Run from `gateway_v2/`:

```bash
cd /work/suraj/AI_Mesh_Firewall/.worktrees/gw27
docker run --rm -v "$PWD":/w -w /w/gateway_v2 python:3.12-slim bash -euxc '
  python -m venv /tmp/v && /tmp/v/bin/pip install -q -e ".[dev]"
  /tmp/v/bin/python -m pytest -q -p no:cacheprovider
  /tmp/v/bin/mypy --strict
  /tmp/v/bin/ruff check gateway_v2 state_control audit_control lint tests
  /tmp/v/bin/lint-imports
  for tree in gateway_v2 state_control; do
    for g in check_sizes check_http_outside_edge_resolve check_no_module_mutable \
             check_capacity_literals check_frozen_dataclasses check_dispatch_provenance; do
      /tmp/v/bin/python -m lint.$g "$tree"
    done
  done
  /tmp/v/bin/python -m lint.check_tenant_scale gateway_v2
  /tmp/v/bin/python -m lint.check_console_inventory
'
```

Expected: pytest 0 failed (record the baseline count first in Task 0; keep the 1 pre-existing strict xfail),
`mypy --strict` clean, ruff clean, import-linter all contracts kept (2 or 3, same count as baseline),
every AST gate exits 0. If `ruff` rejects the `audit_control` path, drop only that path (CI omits it).
Use `docker run --rm -v ... -w /w/gateway_v2` with `--network` default; if the container has no network
for pip, reuse a pre-built image that already has the dev deps and say so in the evidence.

Control-tree tests (Django/DRF) run from `control/` in the control image or a container with
`pip install -e "control[dev]"`; discover the exact runner from `control/pyproject.toml` and
`control/ai_mesh_control/core/tests/` (existing files such as `test_firewall_config_org_token_budget.py`).
Cross-tree tests live in `gateway_v2/tests/plan/` and load control modules by file path (no import of `ai_mesh_control`),
exactly as `gateway_v2/tests/plan/test_firewall_v2_map.py` does.

## Tasks

- [ ] 0. Baseline and pre-change snapshot.
      Run Gate G once on the untouched branch and record pass/skip/xfail counts in
      `docs/plans/evidence/2026-10-27-gw27/baseline.txt`. Generate
      `docs/plans/evidence/2026-10-27-gw27/pre-change-unsupported.json` from the CURRENT
      `control/ai_mesh_control/core/firewall_v2_map.py` (all 46 entries with `kind == "unsupported"`: field, `off`, `reject`), before any disposition is rewritten.
      Files: `docs/plans/evidence/2026-10-27-gw27/baseline.txt`, `.../pre-change-unsupported.json`
      Verify: `python -c "import json;print(len(json.load(open('docs/plans/evidence/2026-10-27-gw27/pre-change-unsupported.json'))))"` prints 46 (adapt to the file's top-level shape); Gate G baseline recorded.

- [ ] 1. Domain value types (`domain/plan.py`).
      Add `RateControl`, `AuditControl`, `OrgControls`, `UNSET_CONTROLS`; add `controls: OrgControls = UNSET_CONTROLS` as the LAST field of `ExecutionPlan` (default keeps every constructor call valid).
      Files: `gateway_v2/gateway_v2/domain/plan.py`; new `gateway_v2/tests/domain/test_gw27_controls.py` (frozen, hashable, equality, default is UNSET).
      Verify: `pytest -q tests/domain tests/plan` (container) passes; `mypy --strict`, `lint-imports`, `check_frozen_dataclasses gateway_v2` clean.

- [ ] 2. Capacity-literal allowlist + `runtime/resources_org.py` (D-13), one commit.
      (a) `lint/check_capacity_literals.py`: replace `ALLOWED_REL` with `ALLOWED_RELS: frozenset[str] = frozenset({"runtime/resources.py","runtime/resources_org.py"})` and make the match (around line 129) a membership test. (b) New `tests/gates/test_gw27_capacity_gate_allowlist.py`: positive (literal in `runtime/resources_org.py` passes), negative (same literal in `runtime/other.py`, in `admit/org_limits.py`, and in an unlisted `resources_x.py` fails). (c) New `runtime/resources_org.py`: `OrgBounds` (request_interval_s, burst_per_worker with `_BURST_SPREAD_Z=3.0` and exact `burst` at W==1, `tpm_window_s=_ORG_TPM_WINDOW_S` 60.0, token_interval_s, token_tolerance_s = max(window, T_tok*max_single_request_cost), max_single_request_cost, slots = `contract.metrics_directory_slots()`), `derive_org_bounds(contract)`, and contract-free `RetentionBounds` + `derive_retention_bounds()` (hard_floor_days=1, sweep_batch, sweep_period_s, org_page, max_batches_per_org, stamp_max_days=365) each with a minimum check raising `CapacityUnavailable`. `runtime/resources.py` is NOT edited.
      Tests: `tests/runtime/test_gw27_resources_org.py` incl. Property 6 helper arithmetic (burst_worker >= ceil(burst/W), == burst at W==1, e.g. burst=150,W=36 -> 11; burst=5,W=36 -> 2), `derive_retention_bounds()` needs no contract and no `workers()`.
      Verify: `pytest -q tests/gates tests/runtime` passes; `check_capacity_literals gateway_v2` and `check_sizes gateway_v2` exit 0; `mypy --strict` clean.

- [ ] 3. Plan layer: `controls` in the signed body (`plan/document.py`, `plan/compiler.py`, `plan/delta.py`, `edge/dev_components.py`, `edge/dev_plan_feed.py`).
      `PlanDocument.controls`; `encode_plan_body` writes `controls` (+ top-level `"requires": ["controls/1"]`) only when controls != UNSET; `decode_plan_body` gets strict `_controls()` (wrong types, bool-as-int, negatives, non-finite, unknown `version`, unknown inner keys, duplicate/empty frameworks -> `CompileError`) and refuses any `requires` entry outside `{"controls/1"}`; `compile_plan(..., controls=UNSET_CONTROLS)`; `_hash_rules` appends `controls|<canonical-json>` only when not UNSET (plans without controls hash byte-identically). Forward `document.controls` at ALL THREE call sites: `plan/delta.py:62`, `edge/dev_components.py:218`, `edge/dev_plan_feed.py:141`.
      Tests: `tests/plan/test_gw27_document.py` (strict decode rejections, round trip Property 4, hash stability Property 5, requires gating), `tests/plan/test_gw27_compile_sites.py` (drive each of the three paths; AST test asserting every `compile_plan(` call passes `controls=`).
      Verify: `pytest -q tests/plan` passes (existing `test_lgw05c_delta.py`, `test_dev_plan_publish.py` unchanged); mypy/ruff/lint-imports clean.

- [ ] 4. Audit stamp inputs and label-free metrics (`audit/record.py`, `audit/metrics.py`, `admit/metrics.py`, state producer for `plan_schema_version`).
      Confirm `AuditRecord.detail` carries `retention_days: int` and `compliance_frameworks: list[str]` unchanged in `SCHEMA_VERSION`. Add `audit_suppressed_by_org_total` to the audit producer; add `org_limits_table_full_total` and `org_limits_slots_used` to the admit producer; export gauge `plan_schema_version` (value 1) through the existing state producer; bump each fixed `SERIES_COUNT` and update `tests/admit/test_metrics_series_count.py` (and any publisher series-count assertion in `tests/edge`) in the same commit.
      Verify: `pytest -q tests/audit tests/admit tests/edge -k "metrics or series or publisher"` then full `tests/` passes; `check_tenant_scale gateway_v2` exit 0 (no tenant-derived label).

- [ ] 5. `admit/org_limits.py` (new): `OrgLimits`, `OrgLimitParams`, `_OrgState`, `_Bucket`, `_controlled`, `OrgLimitDecider` helpers.
      Per design section 5: per-instance state only; `check(org, controls, cost, *, request_id) -> BudgetVerdict | None`; fast path `if not _controlled(controls): return None` BEFORE the `try`; fail-closed catch-all (any Exception -> `rate_limited_verdict`); `_Bucket.take` GCRA admit rule, advance only on admit; `retarget` carry-forward (`tat = now + min(max(tat-now,0), new_tau)`); no eviction, full table refuses controlled org (429) and bumps `org_limits_table_full_total`; `refund_tokens`; `invalidate`; TPM independent of `rate_limit_enabled`; no store I/O, no await; no call to `PlanStore.known()`. Add `rate_limited_verdict` in `admit/grant.py` (or reuse) with `posture.RATE_LIMITED`, `should_retry=False`, `retry_after_s >= MIN_RETRY_AFTER_S`; ensure `domain/posture.py` has `RATE_LIMITED` and `edge/errors.py` maps it to 429 `rate_limit_error` (add only if missing).
      Tests `tests/admit/test_gw27_org_limits.py`: W==1 burst 5 -> 5 admitted, 6th refused with `retry_after_s >= MIN_RETRY_AFTER_S`; simulated W=36 aggregate <= `36*burst_worker`; `_controlled` rows (rate off + tpm 0 on a full table admitted and takes no slot; rate off + tpm 1000 builds token bucket only); TPM window/refund; org_tpm_limit below `W*default_response_token_ceiling` admits an idle request (tau floor); full table refuses, never evicts; fail-closed catch-all (monkeypatched bucket raising).
      Properties (>=10,000 iters, tagged): 6 (rate bound, aggregate burst in [burst, N*burst_worker]), 6b (0.5*burst uniform spread over W in {2,8,36} refused in <1/1000 trials), 7 (TPM conservation + refund restores allowance), 8 (fail-closed), 12 (no reset on republish / interleavings, full table refuses).
      Verify: `pytest -q tests/admit` passes; `check_capacity_literals gateway_v2` (no literal in `admit/`), `check_no_module_mutable`, `check_frozen_dataclasses`, mypy, ruff, lint-imports clean.

- [ ] 6. Edge wiring part A: org gate, holds, response-token cap (`edge/org_gate.py` new, `edge/chat_deps.py`, `edge/openai_chat.py`, `edge/upstream_body.py`, `edge/openai_misc.py`, `edge/openai_responses.py` only if needed, `edge/mcp.py`, RequestHolds module).
      `RequestHolds`: slots `_controls`, `_org_id_charged`, `_org_tokens`, `_org_refund`; `note_controls()`, `controls` property, `org_charged(org_id, cost, refund)` (`refund` required, no default); inside the existing `release(admission, quota)` (signature unchanged), under the `_released` latch and best-effort try/except, `if self._org_refund is not None and not self._dispatched: self._org_refund(...)`. `edge/org_gate.py::enforce_org_limits(...)` as phase 5b after `holds.admitted(...)` and `cost = self._estimated_cost(req)`, before the per-key GCRA and budget lease; verdict -> `_refused(...)` with zero provider calls. `OrgLimitDecider` Protocol in `chat_deps.py`; `ChatRoute` and the embeddings surface get a REQUIRED `org_limits` field (no default). Embeddings (`openai_misc.py`) call `enforce_org_limits` after their admission phase with their own cost. MCP: `_tenant` calls `ctx.holds.note_controls(plan.controls)` (no org charge on MCP, no rate/burst/TPM there; moderations excluded, named in ledger). Response cap: `org_ceiling = plan.controls.max_response_tokens or contract.default_response_token_ceiling()` passed to `clamp_output_budget`, `with_openai_body` and `_apply_budget`, still bounded by `max_output_tokens()`. Boot wiring: `edge/dev_components.py`, `edge/production_profile.py` and test helpers pass a real `OrgLimits(contract)`. Keep `routes.py` at <= 800 lines (move code out if an edit would push it over; likewise `openai_chat.py`).
      Tests: `tests/edge/test_gw27_lifecycle.py` (phase order rate before budget; 429 with zero provider calls; org tokens refunded when budget refuses; no refund after dispatch; response ceiling reaches upstream body on stream and non-stream; `ChatRoute` without `org_limits` raises `TypeError`), `tests/edge/test_gw27_surfaces.py` (chat, responses delegation, embeddings each 429 with zero provider calls; end-to-end refund through the real `release` at `routes.py`, `openai_responses.py`, `openai_misc.py` callers; boot-wiring test that production and dev profiles supply `org_limits`).
      Verify: `pytest -q tests/edge tests/openai_conformance` passes (existing GW12/GW15 suites unchanged); `check_sizes gateway_v2` exit 0; mypy/ruff/lint-imports/`check_http_outside_edge_resolve` clean.

- [ ] 7. Edge wiring part B: audit gating and stamp (`edge/audit_emit.py`).
      `_is_mandatory(record)` (hard BLOCK, SHED, REJECTED) evaluated FIRST, reads only the record; then `audit.enabled is False` -> suppress+count; then `incident_logging is False` and output-phase REDACT/FLAG -> suppress+count; else emit. `emit_chat_audit` reads `holds.controls` (no signature change; callers `routes.py:363`, `openai_responses.py:147`, `mcp.py:144` not edited). Stamp via a separate `stamp: Mapping[str, object]` merged into `AuditRecord.detail` (`retention_days` int, `compliance_frameworks` list[str]); no stamp and always emitted when controls are UNSET (pre-plan refusals); audit stays ON when `AuditControl` is None.
      Tests `tests/edge/test_gw27_audit_gating.py`: Property 10 full truth table over (enabled, incident_logging, disposition, phase) with counter incremented exactly once per suppressed record and never for mandatory ones; one case per caller through the real `finally` path (routes, responses, mcp); pre-plan refusal no stamp and emitted; int/list stamp types pinned end to end (record -> serialized payload -> `DurableRecord.columns()` after Task 8, so add that assertion in Task 8).
      Verify: `pytest -q tests/edge tests/audit` passes; `check_sizes gateway_v2`, mypy, ruff clean.

- [ ] 8. `audit_control` retention: schema, sink, sweeper (outside `root_packages`; imports only `gateway_v2.runtime` and `gateway_v2.domain`).
      `audit_control/schema.py`: idempotent `ALTER TABLE amf_audit_record ADD COLUMN IF NOT EXISTS retention_days smallint` and partial index `amf_audit_record_retention (org_id, recorded_at_ns) WHERE retention_days IS NOT NULL`, both executed in `apply_schema` before any INSERT. `audit_control/sink_pg.py`: `RECORD_COLUMNS`, `_INSERT` and `DurableRecord.columns()` gain a last `retention_days` element read from `fields["detail"]["retention_days"]`, accepted only as int (not bool) in `1.._RETENTION_STAMP_MAX_DAYS`, else None; missing/non-mapping detail -> None. New `audit_control/retention.py`: sweeper with the exact batch DELETE (org-scoped, coarse `hard_floor_days` bound, `FOR UPDATE SKIP LOCKED`, `now_ns` injected, DEFAULT clock `time.time_ns`), keyset org enumeration (`org_id > after_org ... ORDER BY org_id LIMIT org_page`, each org visited at most once per sweep, at most `max_batches_per_org` batches per org), `dry_run` uses `SELECT count(*)`, one `pg.cursor_tx()` per batch, DB error -> delete nothing further + WARNING. `audit_control/service.py`: `AMF_AUDIT_RETENTION_SWEEP` = `off|dry_run|on`, default `off`, unknown -> `off` + WARNING; `derive_retention_bounds()` called only when not `off`; export `audit_retention_last_sweep_age_s`. Update tests that pin `RECORD_COLUMNS`.
      Tests: `tests/audit_control/test_gw27_retention.py` using the existing fake/in-memory style of `test_lgw14c_export.py` and `test_lgw14c_service.py` (old-shape payload without `detail` -> NULL and insert succeeds; mode parsing; exporter starts with sweep `off` and no gateway worker config; default clock within a day of `time.time_ns()`; dry_run terminates and deletes nothing; org with a locked row terminates; org with > batch*max_batches expired rows is partially swept and later orgs still swept). Property 11 (>=10,000 iters): predicate never matches NULL, never a row younger than its own `retention_days`, never another org, never a stamped value below the hard floor; sweep terminates. Live Postgres arm (`skipif` no `AMF_PG_DSN`): written but NOT run.
      Verify: `pytest -q tests/audit_control` passes (live arm skipped); `mypy --strict`, ruff, `lint-imports` clean; `records_lost` accounting tests from R2-05 still pass.

- [ ] 9. Control tree: dispositions, controls builder, threat/keyword/output-policy drafts, publish, serializer, ledger.
      (a) `control/ai_mesh_control/core/firewall_v2_map.py`: rewrite all 46 `unsupported` entries to `built` (with `via`) or `retired` (with `owner`, non-empty `reason`, keep `off`); `_rejected` per design section 1 (built/mapped reject only via `reject`; retired reject when value not in `off`); message `"{field} is retired and not enforced (owner {owner}): {reason}. Change refused so the value is never stored and ignored."`; `rewrite` message names GW33; add `built_value_errors(merged)` with the dependency pairs (rate switch <-> rpm/burst, retention_days <-> frameworks) and the controls-backed-field refusal while `controls_eligible(org_id)` is false; `retire_ledger()`, `built_ledger()`. Dispositions: 15 BUILT / 30 RETIRED as in design sections 2 and 13 (`org_token_budget` stays the 46th, delegated to GW36).
      (b) New `core/firewall_v2_controls.py`: `controls_for(payload)`, `threat_drafts(payload)` (3 rules prompt_injection/jailbreak/command, id `fw-threat-<category>-input`, threshold = value/100, block vs flag), `RETENTION_FLOOR_DAYS` (PCI-DSS 365, HIPAA 365), keyword/framework limits, `controls_eligible(org_id)`.
      (c) `core/firewall_v2_publish.py`: `keyword_row(payload)` through the existing `_policy_rule`/`_condition` (rule id `pol-fw-blocked-keywords-keywords`, case-insensitive substring semantics noted), `policy_drafts(..., output_enabled, output_fallback)` implementing the section 10 table (output scope dropped, both -> input, fallback matrix, monitor-posture override), `document_bytes` writes `controls` only when `V2_PLAN_CONTROLS_ENABLED` and org eligible, `V2_PLAN_NEW_RULES_MODE` `off|monitor|enforce` default `monitor` for keyword+threat rows; `publish_for_org` status notes (`org_limits_scope: per gateway replica`, `retired_unchanged`, `threat_intel` note, compliance violations via `validate_compliance_requirements`, `controls_withheld`). Add settings `V2_PLAN_CONTROLS_ENABLED` (default false), `V2_PLAN_CONTROLS_ORGS`, `V2_PLAN_NEW_RULES_MODE`.
      (d) `core/firewall_config_serializer.py`: call `selection_errors` plus `built_value_errors` on the merged payload; unchanged stored values are always accepted.
      (e) `scripts/check_fleet_plan_schema.py`: scrape `plan_schema_version` from replica `/metrics`; non-zero exit if any replica is missing or below 1 (unit-tested against canned exposition text; no live scrape).
      (f) Ledger `docs/plans/evidence/2026-10-27-gw27/retire-list.json` per design Data Models (`built`, `retired`, `delegated_outside_45`, `surface_exclusions` for MCP and moderations, `via` text covering per-replica scope, estimate-based TPM, case-insensitive substring keywords, content-score threat floor).
      Tests: `control/ai_mesh_control/core/tests/test_firewall_v2_gw27.py` (every BUILT field reaches the published document; keyword row shape/empty/dedupe; output-policy drop and per-row fallback table plus monitor-posture; threat drafts on/off/auto-block off/threshold 0 and 100 and that toggling off removes only `fw-threat-*`; compliance note; D-2 HIPAA+audit-off saves and surfaces a violation; refusals in both directions of each dependency pair); `gateway_v2/tests/plan/test_gw27_dispositions.py` (Property 1: 15/30/1, pre-change snapshot has 46, ledger equals map, owner+reason non-empty, message names field and owner, `rewrite` refused for all five action fields, existing `test_turning_rag_on_names_the_field` still holds); `gateway_v2/tests/plan/test_gw27_publish_contract.py` (cross-tree: build a document with control `document_bytes` loaded by path, decode with the real `decode_plan_body`, `compile_plan`, assert `ExecutionPlan.controls` and rules). Properties 2, 3, 9 (>=10,000 iters, tagged); keep Property 3's round trip control-side.
      Update `gateway_v2/tests/plan/test_firewall_v2_map.py` for the removed `unsupported` kind. Refresh the GW14b console inventory JSON if `check_console_inventory` reads field kinds.
      Verify: control-tree tests pass in the control container; Gate G `pytest -q tests/plan` passes; `python -m lint.check_console_inventory` exits 0; ruff clean on touched control files.

- [ ] 10. Evidence, changelog pointer and full offline gate.
      Write `docs/plans/evidence/2026-10-27-gw27/offline-gate.txt` with Gate G output and counts vs the Task 0 baseline (no new failures, only the one strict xfail); add the OPEN list (L27-1, L27-2, live Postgres EXPLAIN, live entry-size re-run with worst-case stamp, fleet `check_fleet_plan_schema.py` against real replicas, owner sign-offs D-8, D-12) to the evidence; add a one-line GW27 pointer in `AGENTS.md` (and the progress doc `docs/AI_MESH_RUNBOOK_v3.1_PROGRESS.md`) marking the local half CLOSED and the live half OPEN. Do NOT run any live Docker/staging gate. Do not mark GW27 fully done.
      Files: `docs/plans/evidence/2026-10-27-gw27/*`, `AGENTS.md`, `docs/AI_MESH_RUNBOOK_v3.1_PROGRESS.md`
      Verify: full Gate G (above) passes end to end in the container; `git status` clean except intended files.

## Requirement coverage

| Req / Property | Task |
|---|---|
| R1, Property 1 | 0, 9 |
| R2, Property 2 | 9 |
| R3, Properties 6, 6b, 7, 8, 12 | 2, 5, 6 |
| R4, Property 9 | 9 |
| R5, Properties 3, 4, 5, 10, 11 | 3, 6, 7, 8, 9 |
| R6, R7 | 9 (ledger, messages) |
| NFR structural, Property 13 | 2, plus Gate G |
