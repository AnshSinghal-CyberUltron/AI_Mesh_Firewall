# GW27 Handoff: FirewallConfig fields (45 unsupported + org_token_budget)

Branch: `gw27-firewall-config-fields`. Based on `suraj-revamp` at `1634da0c` (rebase was a no-op; branch already sits on the tip). Not pushed. suraj-revamp NOT fast-forwarded. Worktree `/work/suraj/AI_Mesh_Firewall/.worktrees/gw27` kept.

## What landed (commits on top of suraj-revamp)
- `5d6d5aed` docs: add GW27 spec and design review
- `ec329951` docs: GW27 design revision pass 3 (org-limit fast path, retention host/sink/index, burst slack)
- `6dacdcf6` docs: GW27 design revision pass 4 (org refund wiring, audit controls delivery, retention keyset sweep)
- `423f9e78` docs: GW27 design revision pass 5 (no boot enumeration, MCP audit in scope, org refund condition)
- `4c5507fd` chore: GW27 task 0 baseline and pre-change unsupported snapshot
- `29177064` feat: GW27 task 1 org controls value types
- `b32c4301` feat: GW27 task 2 capacity allowlist and resources_org derivations
- `92f8642f` feat: GW27 task 3 controls in the signed plan body and compile sites
- `031fa0f7` feat: GW27 task 4 org-suppression, org-table and plan-schema metrics
- `0200b06f` style: wrap GW27 metrics docstrings
- `fa38b20e` feat: GW27 task 5 per-org rate, burst and TPM limiter
- `337690a2` feat: GW27 task 6 org gate, holds, response ceiling and edge wiring
- `1e959255` feat: GW27 task 7 org audit gating and compliance stamp
- `76b7b81a` feat: GW27 task 8 audit retention schema, sink stamp and sweeper
- `7953da74` feat: GW27 task 9 dispositions, controls builder, publish gates, serializer, ledger
- `a8ddcc25` chore: refresh GW14b console inventory (gateway_v2 now quotes the GW27 fields)
- `e4198e68` docs: GW27 offline gate evidence, verification and changelog pointers
- `3f885df0` docs: GW27 offline gate re-run report
- `0738813c` docs: GW27 review and gate artifacts

(HEAD also includes this report's own commit.) Spec files: `requirements.md`, `design.md`, `tasks.md`, `verification.md`, `offline-gate.md`, review/gate JSON in this directory.

## Offline gate results (verbatim from offline-gate.md)

# GW27 offline gate (re-run at HEAD e4198e68)

Env: host venv /tmp/gw27v (Python 3.12.3, gateway_v2[dev,serve,conformance] editable from this worktree). All commands run from `gateway_v2/`, mirroring `.github/workflows/gateway-v2.yml` `structural-gates`. Live Docker/staging gate NOT run, NOT claimed.

| Command | Result |
|---|---|
| `/tmp/gw27v/bin/python -m pytest -q -p no:cacheprovider` | 1 failed, 3208 passed, 107 skipped (616.99s) |
| `/tmp/gw27v/bin/ruff check gateway_v2 state_control lint tests` | All checks passed |
| `/tmp/gw27v/bin/mypy --strict` | 9 errors in 6 files (285 checked), ALL `import-not-found` for optional vector-DB client libs (weaviate, qdrant_client, pinecone, pymongo, elasticsearch, opensearchpy, chromadb) not installed in this venv; 0 errors in GW27 files |
| `/tmp/gw27v/bin/lint-imports` | Contracts: 3 kept, 0 broken |
| `python -m lint.check_sizes / check_http_outside_edge_resolve / check_no_module_mutable / check_capacity_literals / check_frozen_dataclasses / check_dispatch_provenance` for `gateway_v2` and `state_control` | all 12 exit 0 |
| `python -m lint.check_tenant_scale gateway_v2` | exit 1: 1 violation, `dispatch/vector_transports/pinecone_transport.py:140` (pre-existing) |
| `python -m lint.check_console_inventory` | exit 0 |
| `python -m pytest -q -p no:cacheprovider tests/gates/test_gw27_capacity_gate_allowlist.py` (D-13 two-file allowlist gate) | 4 passed |

## Pre-existing, unrelated to GW27 (not hidden)
- `tests/gates/test_check_tenant_scale.py::test_the_shipped_data_plane_is_clean` fails because of the tenant_scale violation above; the file is in the vector_transports tree, not a GW27 change, and the same failure is in the Task 0 baseline.
- mypy 9 errors: missing optional third-party packages in the venv (env), identical to the earlier run.
- Baseline had 5 errors in `tests/edge/test_edge_live_l16b.py` (live sockets); they did not recur.

## Not run
Control-tree Django tests, live Docker/staging gates (L27-1, L27-2), live Postgres arm, owner sign-offs D-8/D-12.

## Verdict
No GW27-caused failures. No code changes needed.

## Field dispositions
Totals: 15 built, 30 retired, 1 delegated (`org_token_budget` to GW36, the 46th entry in the committed pre-change snapshot).

### Implemented (15 built)

| # | Field | Published as | Enforced in | Mapped in |
|---|---|---|---|---|
| 1 | `rate_limit_enabled` | `controls.rate.enabled` | `admit/org_limits.py::OrgLimits.check` | `firewall_v2_controls.controls_for` |
| 2 | `requests_per_minute` | `controls.rate.requests_per_minute` | same (GCRA rate) | same |
| 3 | `burst_limit` | `controls.rate.burst` | same (GCRA burst) | same |
| 4 | `org_tpm_limit` | `controls.org_tpm_limit` (0 = off) | `OrgLimits.check` token window | same |
| 5 | `blocked_keywords` | one keyword rule through the GW26 policy-row path | existing resolver (`RuleKind.KEYWORDS`) | `firewall_v2_publish.keyword_row` (not in `firewall_v2_map.py`) |
| 6 | `max_response_tokens` | `controls.max_response_tokens` | `edge/openai_chat.py` clamp + `edge/upstream_body.py` | `controls_for` |
| 7 | `output_policy_enabled` | drops output-scope and rewrites both-scope GW26 policy rows to input scope when false (ledger `via` text and console note state that output halves were removed, naming the rewritten `both` to `input` rows) | resolver (rule absent) | `firewall_v2_publish.policy_drafts` |
| 8 | `output_policy_action` (non-rewrite) | fallback action per the section 10 table (own action `monitor`/empty) | resolver | `policy_drafts` |
| 9 | `output_incident_logging_enabled` | `controls.audit.incident_logging` | `edge/audit_emit.py` gating | `controls_for` |
| 10 | `audit_logging_enabled` | `controls.audit.enabled` | `edge/audit_emit.py` gating | `controls_for` |
| 11 | `retention_days` | `controls.audit.retention_days` and stamped on each audit record | `audit_control/retention.py` | `controls_for` |
| 12 | `compliance_frameworks` | `controls.compliance.frameworks` | stamped on every audit record; violations surfaced at publish | `controls_for` |
| 13 | `threat_intel_enabled` | threat rules present or absent | resolver | `firewall_v2_controls.threat_drafts` |

### Retired (30) and delegated (1)
Each is `kind: "retired"`, keeps its `off` tuple, and refuses any change to a value outside `off`. `owner` and `reason` are what the error message and the ledger print.

| Field | Owner | Reason |
|---|---|---|
| `output_hallucination_action` | GW33 | grounding/hallucination output action; `rewrite` also refused |
| `log_level` | GW27 | v2 observability is the typed GW14d exposition with bounded cardinality; no per-org log-detail knob exists to build. SOC2/HIPAA/PCI "log_level_minimum" requirements stay report-only |
| `model_isolation_enabled` | GW29 | isolation is plan state built by GW29 |
| `allowed_models`, `default_model` | GW28 | per-org model catalogue and default selection |
| `semantic_analysis_enabled`, `tier2_enabled` | GW10 | semantic detection is governed by the GW10 detector and policy set |
| `tier2_execution_mode` | GW27 | no v2 plan primitive for sync/async Tier-2; v2 detection runs in the resolver; not named for build |
| `tier2_stream_hold_enabled`, `tier2_stream_hold_timeout_ms` | GW13 | stream hold is the output streaming-mode surface |
| `mcp_tier2_enabled`, `mcp_policy_only_enforcement` | GW17 | MCP surface posture |
| `mcp_ext_scan_action` | GW17 | external MCP proxy posture (GW32 consumes it) |
| `tier2_strict` | GW27 | subsumed by per-rule `on_unavailable`, which `drafts_for` already emits from `tier2_fail_closed_enabled` |
| `factuality_check_enabled`, `hallucination_grounding_mode`, `hallucination_grounding_threshold`, `hallucination_grounding_model` | GW33 | grounding/hallucination output parity |
| `rag_enabled`, `vector_db_isolation`, `rag_max_documents`, `rag_relevance_threshold`, `rag_redaction_enabled`, `rag_tier2_enabled` | GW31 | the six RAG settings |

Note: `org_token_budget` is delegated to GW36 (signed `StateKind.BUDGET` record spent by the per-org `BudgetLease`), not retired.

## Design change log (settled field set unchanged throughout)
- Pass 2: org-token tolerance floor derived from max single-request cost; all org limits explicitly per gateway replica; per-surface wiring table (chat, responses, embeddings wired; moderations and MCP rate scope excluded with owners); `V2_PLAN_NEW_RULES_MODE` (default `monitor`) and `V2_PLAN_CONTROLS_ORGS` canary gates; retention sweeper inside `python -m audit_control`, `AMF_AUDIT_RETENTION_SWEEP=off|dry_run|on` default off; all three `compile_plan` sites forward controls.
- Pass 3: burst per-worker share changed from `ceil(burst/W)` to `ceil(burst/W + z*sqrt(burst*(1/W)*(1-1/W)))`; at W=1 unchanged, aggregate burst bound loosens to about `W * burst_worker` (Req 3.4 and AC-5 amended). Retention inputs split into contract-free `RetentionBounds`. `org_tpm_limit` enforcement independent of `rate_limit_enabled`.
- Pass 4: org-token refund bound at charge time via callable (internal wiring only). Audit controls reach the emitter through `RequestHolds.note_controls`; pre-plan refusals carry no stamp and are always emitted. Retention sweep is a keyset-paginated single pass with a per-org batch cap (large backlogs delete over several sweeps).
- Pass 5: boot-time controlled-org enumeration removed in favour of a runtime slot gauge (fail-closed table-full 429 is the sole guard). MCP audit gating and stamp brought in scope.
- Build-time resolutions (verification.md): `build_app(org_limits=None)` builds a real `OrgLimits`; `built_value_errors` lives in `firewall_v2_controls.py`; `publish_for_org` keeps `status: "published"` and reports `controls: "controls_withheld"`; keyword/threat drafts only when `threat_intel_enabled` is present and true; embeddings emit no audit records in this tree.

User-visible behaviour: retired fields refuse a changed value with a field-named error. While `V2_PLAN_CONTROLS_ENABLED` is false (default) or the org is not in `V2_PLAN_CONTROLS_ORGS`, changing a controls-backed field is refused naming plan schema 1 (D-10). Keyword and threat rows publish as monitor/flag by default.

## Remaining for the human
1. Live Docker/staging gates NOT run and NOT claimed: L27-1, L27-2, live Postgres EXPLAIN arm, live entry-size model. Control-tree Django tests beyond `test_firewall_v2_gw27` (12 passed at the earlier run) were not re-run at HEAD.
2. Merge decision: branch is ready to merge into `suraj-revamp` (no conflicts, no rebase needed). The one failing test (`test_check_tenant_scale`, Pinecone line 140) and the 9 mypy missing-SDK errors are pre-existing at baseline.
3. Confirm D-12 retention floors: 365 days for PCI-DSS and HIPAA (`RETENTION_FLOOR_DAYS` in `core/firewall_v2_controls.py`). 365 is capped by the existing serializer ceiling; HIPAA's real documentation period is longer. D-12 must be signed before setting `AMF_AUDIT_RETENTION_SWEEP=on`.
4. Also pending owner sign-off: D-8 (default-on blast radius before moving `V2_PLAN_NEW_RULES_MODE` from `monitor` to `enforce`).
