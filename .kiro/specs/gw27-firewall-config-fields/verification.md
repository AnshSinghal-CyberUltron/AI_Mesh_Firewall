# GW27 verification (iteration 1, implemented from scratch)

Branch `gw27-firewall-config-fields`, worktree `/work/suraj/AI_Mesh_Firewall/.worktrees/gw27`.
No live Docker/staging gate was run or claimed (L27-1, L27-2, live Postgres EXPLAIN arm, live
entry-size model are OPEN; see `docs/plans/evidence/2026-10-27-gw27/offline-gate.txt`).

## What I ran (exact), from `gateway_v2/`

Environment: host venv `/tmp/gw27v` (outside the worktree), Python 3.12.3,
`pip install -e ".[dev,serve,conformance]"` (the baseline needs `httpx`, which `[dev]` alone lacks;
docker `python:3.12-slim` + pip was also verified to work, the host venv was used for speed).

| Check | Command | Result |
|---|---|---|
| Full pytest | `python -m pytest -q -p no:cacheprovider -rfE` | 1 failed, 3208 passed, 107 skipped, 0 errors (619.78 s) |
| Baseline (untouched HEAD 423f9e78) | same | 1 failed, 3044 passed, 101 skipped, 5 errors |
| mypy | `mypy --strict` | 9 errors, identical to baseline: optional vector SDKs not installed (`weaviate`, `qdrant_client`, `pinecone`, `pymongo`, `elasticsearch`, `opensearchpy`, `chromadb`), 0 new |
| ruff | `ruff check gateway_v2 state_control audit_control lint tests` | All checks passed |
| import-linter | `lint-imports` | 3 kept, 0 broken (same count as baseline) |
| AST gates x 2 trees | `python -m lint.{check_sizes,check_http_outside_edge_resolve,check_no_module_mutable,check_capacity_literals,check_frozen_dataclasses,check_dispatch_provenance} {gateway_v2,state_control}` | all exit 0 |
| tenant scale | `python -m lint.check_tenant_scale gateway_v2` | 1 violation, `dispatch/vector_transports/pinecone_transport.py:140`, identical to baseline (not a GW27 file; no allowlist entry added) |
| console inventory | `python -m lint.check_console_inventory` | exit 0 after `--write` (4 `quoted_in_gateway_v2` flags flipped to true because the gateway now names those fields) |
| Control tree | control image, worktree mounted: `python manage.py test core.tests.test_firewall_v2_gw27 --noinput` | Ran 12 tests, OK |
| ruff on touched control files | `ruff check control/ai_mesh_control/core/firewall_v2_{map,controls,publish}.py core/firewall_config_serializer.py scripts/check_fleet_plan_schema.py` | clean for the new code; the 2 remaining findings in `firewall_config_serializer.py` (I001 import order, RUF012) are pre-existing (verified against base commit 423f9e78) |

The single failing test (`tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean`)
fails at baseline for the Pinecone line above. The 5 `test_edge_live_l16b.py` errors at baseline
(live socket fixtures) did not recur; they are environmental. Not run: control `TestCase` tests
that need Postgres (none touch the GW27 fields; I grepped).

## Property tests (seeded `random.Random`, 10,000 iterations, tagged in source)

| Property | File |
|---|---|
| 1 total disposition (15/30/1, snapshot has 46) | `tests/plan/test_gw27_dispositions.py` |
| 2 honest save | same |
| 3 no built field stored and ignored | `tests/plan/test_gw27_publish_contract.py` |
| 4 round trip, 5 hash stability | `tests/plan/test_gw27_document.py` |
| 6 rate bound, 6b spread slack (W in 2/8/36, 10,000 trials each), 7 TPM + refund, 8 fail closed, 12 no reset | `tests/admit/test_gw27_org_limits.py` (+ burst arithmetic in `tests/runtime/test_gw27_resources_org.py`) |
| 9 keyword shape | `tests/plan/test_gw27_publish_contract.py` |
| 10 audit gating truth table | `tests/edge/test_gw27_audit_gating.py` |
| 11 retention safety and termination | `tests/audit_control/test_gw27_retention.py` |
| 13 gate allowlist | `tests/gates/test_gw27_capacity_gate_allowlist.py` |

Property 3 is exercised gateway-side (document built by the control module loaded by path, then
decoded by the real `decode_plan_body`), not control-side as tasks.md suggested, because the
control container run here has no pytest/seeded harness; the control-side example tests are in
`control/.../core/tests/test_firewall_v2_gw27.py`.

## Conservative resolutions of ambiguity (none blocks; all documented)

1. `build_app(org_limits=None)` builds a real `OrgLimits(contract)`; `ChatRoute` and `MiscRoute`
   still REQUIRE `org_limits` (TypeError without it). This keeps ~60 `build_app` test callers
   valid while no route can serve without a real limiter. Dev and production profiles pass one
   wired to `AdmissionMetrics`.
2. `built_value_errors` lives in `core/firewall_v2_controls.py`, not `firewall_v2_map.py`: the
   existing map test loads the map as a top-level module and a `core.` import there would break it.
   `selection_errors`, `retire_ledger`, `built_ledger`, `changed_fields` stay in the map.
3. `publish_for_org` keeps `status: "published"` (the `sync_v2_plan_documents` command treats any
   other status as failure) and reports the gate in a separate key, `controls: "controls_withheld"`.
4. Global monitor posture forces `mode=monitor` only on output/both-scope rows the output-policy
   fallback decided, not on every policy row, so existing GW26 input policies behave as before.
5. `threat_drafts` emits nothing unless `threat_intel_enabled` is present and true in the payload
   (real payloads always carry it); an absent key never adds blocking rules.
6. Stamp: `retention_days` only when the plan carries an audit control, `compliance_frameworks`
   only when non-empty, nothing when controls are UNSET.
7. `audit_retention_last_sweep_age_seconds` is rendered by the exporter service only (absent while
   the sweep is off, -1 before the first success), not added to `AuditMetrics.series()`, so the
   gateway Publisher's audit series count did not need a second change.
8. The audit-suppression count lives in the Publisher (`observe_audit_suppressed`, optional hook on
   the observer) and fills the `amf_audit_suppressed_by_org_total` slot that `AuditMetrics` owns.
9. Embeddings emit no audit records in this tree, so audit gating is wired and tested for chat,
   responses and MCP (the three `emit_chat_audit` callers).
10. `controls` decode refuses a rate or audit member that omits a required field.
11. `routes.py` and `app.py` sit exactly at the 800-line cap after trimming docstrings; no
    behaviour moved.

## User-visible behaviour (as approved in design.md; flagged for the reviewer)

- Retired fields: a changed value is refused with `"{field} is retired and not enforced (owner X):
  reason. Change refused so the value is never stored and ignored."`.
- While `V2_PLAN_CONTROLS_ENABLED` is false (default) or the org is not in
  `V2_PLAN_CONTROLS_ORGS`, changing any controls-backed field (rate group, TPM, response cap,
  audit group, retention, compliance frameworks) is refused with a message naming plan schema 1
  (D-10). Unchanged values are accepted so the console can post the whole row.
- Keyword and threat rows publish as monitor/flag by default (`V2_PLAN_NEW_RULES_MODE=monitor`).

No finding required a behaviour change outside the approved design, so no `warning` was sent.
