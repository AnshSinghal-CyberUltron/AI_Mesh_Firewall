# Implementation Plan — GW35 console-truth

Source of truth: `requirements.md` and `design.md` (revision round 4) in this folder. Section references (`§`) and decision ids (`D-nn`) are the design's.
Worktree: `/work/suraj/AI_Mesh_Firewall/.worktrees/gw35` (branch `gw35-console-truth`). All relative paths below are inside it.
Path map: gateway code is `gateway_v2/gateway_v2/<layer>/…` (the design's `edge/x.py` means `gateway_v2/gateway_v2/edge/x.py`); gateway tests are `gateway_v2/tests/<layer>/…`; lint tools are `gateway_v2/lint/`; control is `control/ai_mesh_control/…`; frontend is `frontend/src/…`.
Commit locally, one commit per numbered group below; never push. Each group must leave `mypy --strict`, `ruff`, `import-linter` and the AST gates green.

## Resolution of the round-5 design-review findings (CHANGES_REQUESTED; treated as documented risks, conservative option chosen)

- **R5-1 (size gate).** `egress/stream.py` and `runtime/resources.py` are both exactly 800 lines (`check_sizes` max 800) and `routes.py` is 799. Decisions:
  - `unauth_audit_rate()` and its multiplier go in the existing `gateway_v2/gateway_v2/runtime/resources_vector.py` sibling pattern: create **`runtime/resources_audit.py`** holding the derivation as a free function `unauth_audit_rate(contract) -> int`, with a one-line `ResourceContract.unauth_audit_rate` delegate in `resources.py` only if it can be paid for by removing at least one line there. If `resources.py` cannot take even one line, expose the function from `resources_audit.py` only and have `build_unauth_gate` call it directly. Do not edit `resources.py` otherwise.
  - The `_terminate` classification in `egress/stream.py` moves into a `domain/failure_class.py` helper `terminal_output_action(error) -> str` (returns `"error"` or `"block"`); the `_terminate` edit is then a one-for-one line replacement (net 0). Verify with `wc -l` and `python -m lint.check_sizes` in group 2 and group 5.
  - `routes.py`: move `_ProducedModeHolder` to `edge/produced_holder.py` first (group 5) and re-measure before adding anything.
- **R5-2 (`merge_row`).** `merge_row` rebuilds stored metadata from a fixed key list, so `refused_at` and `key_fingerprint` would be dropped. Decisions: carry both keys through `merge_row`, recompute `refused_at` from the winning trace (the trace that is stored), and keep legacy rows (no `refused_at`) counted as today. Totals use an ORM `.exclude(metadata__refused_at__in=["auth","rate_limit"])` on the `flag/redact/block` filter, so rows without the key still count (exclude on a missing JSON key keeps the row). Add merge tests for both keys.
- **R5-3 (NIT).** `credential_unavailable`, `provider_unreachable`, `store_not_configured` are vector-path codes; tests drive them through `build_stages` directly, not via a chat route.
- **R5-4 (NIT).** Input redact followed by an upstream error becomes final `error` and is not counted in `redacted`/`total_threats`. Recorded as a known gap in the evidence file (conservative: no extra counting).

## Group 1 — Baseline (golden captures + baseline ledger drift)

- [ ] 1.1 Capture golden bytes of today's refusal and upstream-failure responses (status, headers incl. `x-request-id`, body) for: invalid key, shared-state unavailable, `plan_unavailable`, `overload_shed`, `rate_limited`, `budget_unavailable`, input `scan_failure`, `upstream_error`, `upstream_rate_limited`, stream identity refusal. Store as JSON fixtures in `gateway_v2/tests/edge/golden/gw35_pre_change.json` using the in-process ASGI harness already used by `gateway_v2/tests/edge/` (reuse its chat fixtures; read one existing `tests/edge/test_openai_chat*.py` first).
  Files: `gateway_v2/tests/edge/golden/gw35_pre_change.json`, `gateway_v2/tests/edge/test_gw35_golden_capture.py` (writes when `GW35_WRITE_GOLDEN=1`, else asserts equality).
  Verify: `cd gateway_v2 && GW35_WRITE_GOLDEN=1 python -m pytest tests/edge/test_gw35_golden_capture.py -q`, then without the env var, passes.
- [ ] 1.2 Record the pre-change gate baseline (failing tests, if any) in `docs/plans/evidence/2026-11-04-gw35/baseline.md`: run the full gate set from group 9 on the untouched head. Regenerate ledgers for pre-existing drift: `cd gateway_v2 && python -m lint.check_console_inventory --write` (and `python -m pytest tests/lint/test_console_inventory.py --write` equivalent flag as the test documents; read `tests/lint/test_console_inventory.py` to use its exact `--write` switch), commit alone as `chore(gw35): regenerate GW14b ledgers for baseline drift`.
  Verify: `python -m lint.check_console_inventory` exits 0.

## Group 2 — gateway_v2 domain and runtime

- [ ] 2.1 Create `gateway_v2/gateway_v2/domain/failure_class.py` (D-17): `OPERATIONAL_CODES`, `NO_ACTION`, `INPUT_SIDE`, `PREDISPATCH_OPERATIONAL` (frozensets, `PLAN_UNAVAILABLE` in the last), `is_operational_failure`, `is_no_action`, and `terminal_output_action(error)` (R5-1). Stdlib + `domain.posture` only. Read `edge/errors.py`, `domain/posture.py`, `dispatch/provider_errors.py`, `egress/stream.py::_terminate` and `egress/strict.py::_KILLED_CODE` first so the code tables are the real spellings.
  Test: `gateway_v2/tests/domain/test_failure_class.py` — scoped coverage (every posture stream code, every code `routes.py` passes to `kill_latch.kill`, every `upstream_*` code, `output_blocked`, `scan_failure`, `policy_regex_timeout`, plus the two tables, each in exactly one class), unknown-is-policy default test.
  Verify: `cd gateway_v2 && python -m pytest tests/domain -q`.
- [ ] 2.2 Create `runtime/resources_audit.py` with the unauthenticated-audit-rate derivation (D-26, R5-1), `CapacityUnavailable` below 1, multiplier named constant next to it. Wire the optional delegate only if `resources.py` stays ≤ 800.
  Test: `gateway_v2/tests/runtime/test_unauth_audit_rate.py`.
  Verify: `python -m pytest tests/runtime -q && python -m lint.check_sizes && python -m lint.check_capacity_literals` (cwd `gateway_v2`).

## Group 3 — audit schema and records

- [ ] 3.1 Edit `gateway_v2/gateway_v2/audit/console_schema.py` per §5.1: `STAGE_NAMES`, `STAGE_ACTION_VOCAB` (literal tuple), `CONSOLE_ACTIONS`, `ACTION_SEVERITY`, `final_action()`, `MCP_STAGE_NAMES`, `validate_console(console, *, stage_names=STAGE_NAMES)`, stricter `_validate_stage` rules, optional `plan_version` (D-24), subsequence ordering (D-25), non-empty `request_id`, `_check_fingerprint`. Update `edge/mcp_console.py` to import `MCP_STAGE_NAMES` and pass it. Remove `_ACTIONS`.
  Test: `gateway_v2/tests/audit/test_console_schema_gw35.py` incl. `test_mcp_console_still_validates`; run existing `tests/audit` and `tests/edge/test_mcp*`.
  Verify: `python -m pytest tests/audit tests/edge -k "console or mcp" -q`.
- [ ] 3.2 Edit `audit/record.py`: `UNAUTHENTICATED_ORG = "_unauthenticated"`, `output_failure()` builder (OUTPUT phase, `outcome="rejected"`, detail `reason/failure_class/status`). 
  Test: `gateway_v2/tests/audit/test_unauth_org_stream.py` (sink writes and reads back under `_unauthenticated`).
  Verify: `python -m pytest tests/audit -q`.

## Group 4 — trace builder (edge)

- [ ] 4.1 Edit `edge/request_holds.py` (§5.4): new slots/writers/properties, write-once `note_failure`, `note_output_action` ignoring non-dispositions. Test `tests/edge/test_holds_gw35.py` (slots, no `__dict__`, write-once, ignores `error`/empty/None).
- [ ] 4.2 Create `edge/console_stages.py` (§5.2): `StageOutcome`, `ChatFacts`, `build_stages`, `stages_to_console`, `TRACE_CODES`, rules 0–5 incl. INPUT_SIDE-first, no-input-decision skip/skip, 5(b). Test `tests/edge/test_console_stages.py` (one example per requirement table row; the R5-3 vector codes via `build_stages` directly).
- [ ] 4.3 Rewrite `edge/console_projection.py` (§5.3): `chat_console(request_id, facts)`, `facts_from_holds`, `console_from_holds(holds, refuse_code)` (exceptions → `None` + one WARNING), `refusal_console`, `apply_projection` copies `zeroshield`, `pipeline_trace`, `source`; `stream_trace_payload`/`refusal_trace_frame` read holds only. Remove `output_action=`/`provider_called=` params and fix all callers in the same commit (grep `console_from_holds(`, `chat_console(`).
  Tests: property tests P1, P2, P3, P7 in `tests/edge/test_gw35_properties.py` using `run_property(fn, n, seed)`; `tests/edge/test_console_projection_cost.py`; `tests/edge/test_gw35_value_types.py`.
  Verify (cwd `gateway_v2`): `python -m pytest tests/edge tests/audit tests/domain -q`; `python -m mypy --strict gateway_v2`.

## Group 5 — lifecycle, non-stream, stream wiring

- [ ] 5.1 `edge/identity_resolver.py`: add `mint_request_id()` (export), `refusal_request_id` delegates. Create `edge/produced_holder.py` (move `_ProducedModeHolder` out of `routes.py`, add `cut_reason`, `record_cut`). Run `wc -l edge/routes.py` and `python -m lint.check_sizes` before continuing.
- [ ] 5.2 `edge/openai_chat.py` (§5.5): `note_identity_resolved`, `note_plan`, fingerprint on identity refusal, D-30 plan-unavailable `note_tenant`/`note_request(mint_request_id())`/`note_failure`. `edge/openai_nonstream.py` (§5.6): delete `_console_for`, record failure/output action, use `console_from_holds`. `edge/openai_responses.py`: `console_from_holds(holds, outcome.refuse_code)`, `_refuse` calls `note_failure`. `egress/stream.py::_terminate` uses `terminal_output_action` (net 0 lines, R5-1). `edge/routes.py`: `_render_refusal` (note_request before `refusal_console`, `note_failure`), `_finish_stream` kill-latch branch via `record_cut`, `note_stream_output` per §5.7 (STRICT unedited).
  Tests (all in `gateway_v2/tests/edge/`): `test_gw35_refusal_traces.py` (golden diff allow-list `{zeroshield, pipeline_trace, source}`), `test_gw35_upstream_error.py`, `test_gw35_plan_unavailable.py`, `test_gw35_predispatch_failure.py`, `test_gw35_kill_switch.py`, `test_gw35_stream_operational.py`, `test_gw35_strict_failures.py`, `test_gw35_audit_equals_response.py`, `test_gw35_source_carrier.py`, `test_gw35_responses_route.py` (the audit-related assertions in the last two finish in group 6).
  Verify (cwd `gateway_v2`): `python -m pytest tests/edge tests/egress -q && python -m lint.check_sizes` (must show routes.py, stream.py ≤ 800).

## Group 6 — unauthenticated audit + metrics

- [ ] 6.1 Create `edge/unauth_audit.py` (`UnauthAuditGate`, `UnauthAuditCounters`, `build_unauth_gate` using `runtime/resources_audit.py`). Edit `edge/audit_emit.py` (§5.8: `_audit_org` seam with `route == "chat"` scope, gate, console on admission records, output-failure record, fingerprint in `detail`), `ChatRoute.unauth_audit` field + `gate=` argument in `routes.py` (re-run `check_sizes`), `dev_profile.py` builds the gate, `production_profile.py` unchanged, `edge/publisher.py` + `edge/series_count.py` register the seventh producer and update `SERIES_COUNT`.
  Tests: `tests/edge/test_unauth_audit.py`, `tests/edge/test_gw35_failed_credential_audit.py`, P4 and P5 properties, update existing publisher series-count tests.
  Verify: `python -m pytest tests -q -x` (cwd `gateway_v2`), `python -m lint.check_sizes`.
- [ ] 6.2 `tests/edge/test_gw35_negative_controls.py` (a) builder auth/rate_limit literal allow, (b) `is_operational_failure` always false, (c) legacy `_audit_org`; each asserts the specific failure type.
  Verify: `python -m pytest tests/edge/test_gw35_negative_controls.py -q`.

## Group 7 — control plane

- [ ] 7.1 `control/ai_mesh_control/core/v2_audit_adapter.py` (§6.1, R5-2): `_RANK` with `error`, `_ACCEPTED`, action validation, `refused_at` and `key_fingerprint` in `translate`, and carry both through `merge_row` (recomputed from the stored winning trace). Test `control/ai_mesh_control/core/tests/test_v2_audit_adapter_gw35.py` (rank, error/block merge, rejected/shed still -1, unknown action rejected, `refused_at`/fingerprint survive merge, `_unauthenticated=` org map parse, P6). Locate the existing adapter test directory first and follow its layout.
- [ ] 7.2 `policy/security_views.py` (~L1386): D-21 totals plus `refused_total`/`error_total`, ORM exclude per R5-2; enumerate `action__in|action ==|Count(...action` in `core/dashboard_views.py`, `policy/security_views.py`, `policy/views.py` and the threat-feed serializer and record each hit as v1-only or v2-visible in the evidence file. Test `policy/tests/test_v2_totals_gw35.py`.
- [ ] 7.3 Create `core/retired_routes.py` (D-22), settings `AMF_GATEWAY_BACKEND` (invalid → `ImproperlyConfigured`), check in `core/gateway_admin_proxy_views.py::_proxy` and the three forwarding sites in `mcp_connector/views.py` before the secret lookup. Test `core/tests/test_retired_routes.py`.
  Verify (Django lives only in the control container): `docker compose run --rm --no-deps control python manage.py test ai_mesh_control.core ai_mesh_control.policy` scoped to the new modules; if the control image is not built, build it with `docker build -t ai-mesh-control-test control/` (or the compose service the repo's progress file uses) and state in the evidence which suites ran where. Pure adapter tests also run on host: `cd control && python -m pytest ai_mesh_control/core/tests/test_v2_audit_adapter_gw35.py -q` if Django-free.

## Group 8 — frontend

- [ ] 8.1 `frontend/src/utils/pipelineTrace.js`: literal `STAGE_ACTION_VOCAB`, `isGatewayV2Trace(body)`, `notReported(trace)`, `error` label; `frontend/src/utils/liveGateway.js`: v2 traces bypass template/fabricated stages (no `kill_switch`/`policy`/`model_input`), `erroringStageName`, `resolveTotalLatencyMs` null for v2 without `total_latency_ms`. Edit `StageTimeline.jsx`, `LogDetailPage.jsx` (`StatusBadge`), `firewall-module-utils.js`, `AttackSimulatorPanel.jsx` for `error` ("Upstream error", never BLOCK or "No threat detected"); `useFirewallData.js` `v2Note` shows new totals when non-zero.
  Tests: extend `pipelineTrace.test.js`, `liveGateway.test.js`, `firewall-module-utils.test.js`.
- [ ] 8.2 Create `frontend/src/utils/retiredRoute.js` (+ `retiredRoute.test.js`) and `components/RetiredOnGatewayV2.jsx`; wire `CircuitBreakerSimulator.jsx`, `IsolationOpsSimulator.jsx`, `BedrockTestPanel.jsx`, `DatabaseConnectionPanel.jsx`, `MCPConnectorPanel.jsx`, `MCPManagerPanel.jsx` (locate with `file_search`; test asserts each imports the helper).
  Verify (cwd `frontend`): `npm run test:unit`. If host node fails: `docker run --rm -v "$PWD":/app -w /app node:22-alpine sh -c "npm ci && npm run test:unit"`.

## Group 9 — lint extensions, docs, inventory regen, gates

- [ ] 9.1 Lint (§10): `gateway_v2/lint/console_inventory.py::v2_contract_reads()` + `--contract-diff`; `check_console_inventory.py` vocabulary parity between `console_schema.STAGE_ACTION_VOCAB` (AST) and `pipelineTrace.js` (regex); `lint/admin_ledger.py` `signed`/`decision_ref`/`--require-signed`; ledger rows for `/v1/usage`, `/v1/observability`, `/v1/policy/check` (`retire`, owner `gw35`); a ledger-vs-`RETIRED_ON_V2` drift test. `tests/lint/test_stage_wording.py` plus the doc wording fix in `docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md`, GW14b plan text and the progress file (the test file must not contain the stale phrase).
  Verify: `python -m pytest tests/lint -q` (cwd `gateway_v2`; the inventory test may fail until 9.2).
- [ ] 9.2 Regenerate the inventory ledger LAST, after all frontend/control edits: `cd gateway_v2 && python -m pytest tests/lint/test_console_inventory.py --write` (use the exact `--write` mechanism the test file defines; if it is a CLI flag on `lint.check_console_inventory`, use that), review `git diff docs/plans/evidence/2026-10-26-gw14b`, commit. Then `python -m lint.check_console_inventory` and `python -m pytest tests/lint/test_console_inventory.py -q` must pass.
- [ ] 9.3 Evidence: write `docs/plans/evidence/2026-11-04-gw35/SUMMARY.md` (counts, which suites ran where, page-audit from `--contract-diff` as `page-audit.md`, enumeration audit, known gaps §14 + R5-4, human steps below), add a one-line pointer to `AGENTS.md` and `docs/AI_MESH_RUNBOOK_v3.1_PROGRESS.md`.
- [ ] 9.4 Final gate run, all from `gateway_v2` unless stated; record counts in the evidence:
  - Full offline pytest, container-run style (as the progress file does): `docker build -f gateway_v2/Dockerfile -t gw35-test --build-arg GATEWAY_V2_EXTRAS="[dev,serve,conformance]" .` then `docker run --rm gw35-test python -m pytest tests -q`; fall back to host `python -m pytest tests -q` if the image build is unavailable and say so. Compare failures against `baseline.md`; no new failures.
  - `python -m mypy --strict gateway_v2` ; `ruff check .` and `ruff format --check .` ; `lint-imports` (both contracts).
  - AST gates: `python -m lint.check_sizes`, `check_capacity_literals`, `check_no_module_mutable`, `check_frozen_dataclasses`, `check_http_outside_edge_resolve`, `check_tenant_scale` (each exits 0; `state_control/valkey.py:100` is a documented pre-existing finding), plus `python -m lint.check_console_inventory`.
  - Frontend (cwd `frontend`): `npm run lint`, `npm run test:unit`, `npm run build`; node container fallback as in 8.2 if host node is missing.
  - Control Django suites in the control container (7.x).

## Human steps (NOT planned or run by the coding agent)

- L35-1 Playwright sweep, L35-2 live Docker/staging run, L14b-3 browser walk of every console page, owner signatures on ledger rows and the `unauth_audit_rate` multiplier decision, setting `AMF_V2_AUDIT_ORG_MAP` (dev: `dev-org=zeroshield,_unauthenticated=zeroshield`) in the live control environment.
