# GW35 evidence: console data, admin routes and Scan Detail truth

Local half only. The live Docker/staging gate (L35-2), the Playwright sweep (L35-1), the browser walk of every console page (L14b-3) and every owner signature are NOT run and NOT claimed here.

Branch `gw35-console-truth` (worktree `.worktrees/gw35`), based on `suraj-revamp` at `1634da0c`. Spec: `.kiro/specs/gw35-console-truth/`. Gate output with counts: `.agents/tasks/gw35/verification.md`.

## What changed

- **One console builder.** `edge/console_stages.py::build_stages(ChatFacts)` is a pure function of facts the request recorded on `RequestHolds`. It has no latency input and never infers `ran` from a neighbour. `edge/console_projection.py::console_from_holds(holds, refuse_code)` is the single entry; the `output_action=` and `provider_called=` parameters and `openai_nonstream._console_for` are gone. HTTP body, SSE `amf.trace` frame and audit record read the same facts, so they agree.
- **Auth and rate_limit are real.** `auth`: `allow` / `block (invalid_api_key)` / `error (shared_state_unavailable)`. `rate_limit`: `allow` / `block (overload_shed, rate_limited)` / `error (budget_unavailable)` / `skip`. Later stages are `skip, ran:false`. `plan_unavailable` is `input_scan: error, ran:false` (the one permitted `error` + `ran:false` pair, pinned by the validator).
- **`error` end to end.** `audit/console_schema.py` has `STAGE_ACTION_VOCAB = allow, flag, redact, block, error, skip`, severity `block > error > redact > flag > allow`, and `final_action()`. `domain/failure_class.py` is the one table that says whether a code is operational (`error`) or policy (`block`). An upstream failure is `model_output: error`, `output_guardrail: skip`, `final_action: error`. A policy cut stays `block`. A kill-switch cut (`killed` / `stream_killed`) records nothing, so its console and header are unchanged. `domain.decision.Disposition` is NOT extended.
- **Failed-credential audit.** A rejected credential writes one `admission` row under the reserved org `_unauthenticated` (outcome `rejected`, status 401 or 503, console `auth: block|error`). `detail.key_fingerprint` is the first 12 hex of the existing SHA-256 and only for a console-key-shaped token. The schema rejects any full 64-hex string. Writes are bounded by `UnauthAuditGate` (rate from `runtime/resources_audit.py`, counted drops, series `amf_audit_unauthenticated_total` and `_dropped_total`). The 401/503 is never delayed or changed by audit.
- **Source carrier.** Responses and the `amf.trace` frame carry `source: "gateway_v2"`. This is the one additive body key beyond `zeroshield` and `pipeline_trace`; the golden test allows exactly those three keys and the `x-zeroshield-action` header.
- **Control plane.** `v2_audit_adapter`: `error` ranks between `redact` and `block`; `rejected`/`shed` still rank below every console action; an action outside the vocabulary is `RejectedV2("action")`; `refused_at` and `key_fingerprint` survive `merge_row`. `gateway_v2` totals count flag/redact/block that were not an `auth`/`rate_limit` refusal, plus `refused_total` and `error_total`. `AMF_GATEWAY_BACKEND=v2` makes the breaker, bedrock-test, db-test and MCP discover/call/mirror proxies answer 410 `retired_on_gateway_v2` without contacting the gateway. The default `v1` is unchanged; any other value refuses to start.
- **Frontend.** `error` renders as "Upstream error" (not BLOCK, not "No threat detected"). A v2 body is shown as returned: no `kill_switch`/`policy`/`model_input` stage, no invented latency (the Duration reads "Not reported by gateway v2"). A streamed v2 refusal keeps the trace verdict instead of a blanket `error`. Six pages show a labelled "Retired on gateway v2" panel.
- **Lint.** Stage-action vocabulary parity between `console_schema.py` and `pipelineTrace.js`; `--contract-diff` page audit; ledger `signed` / `decision_ref`, "pending signature" reporting and `--require-signed`; ledger rows for `/v1/usage`, `/v1/observability`, `/v1/policy/check` (retire, owner `gw35`, unsigned); a drift test between the control retired table and the ledger. GW14b ledgers were regenerated last with `python -m lint.check_console_inventory --write`.

## Size-gate moves (the files were at the 800-line limit)

`egress/stream.py` stayed at 800 (`_terminate` edit is net 0). `runtime/resources.py` was not touched; the rate derivation is in `runtime/resources_audit.py`. `edge/routes.py` 799 -> 783 (`_ProducedModeHolder` moved to `edge/produced_holder.py`). `edge/app.py` 800 -> 760 (three default helpers moved to `edge/app_defaults.py`, behaviour unchanged). `dev_profile.build_dev_dependencies` and `build_app` stay within the 120-line function limit.

## Owner decisions still open (not made here)

- `_UNAUTH_AUDIT_RATE_MULT = 2` in `runtime/resources_audit.py` is an unmeasured starting value; it waits on the signed capacity.
- The three D-11 ledger rows and the corrected dropped-stage wording (`kill_switch`, `policy`, `model_input`) are "proposed, awaiting owner signature". `python -m lint.admin_ledger --require-signed` exits 1 until signed.
- Dev attribution of the new stream needs `AMF_V2_AUDIT_ORG_MAP=dev-org=zeroshield,_unauthenticated=zeroshield` in the control environment. Unmapped, the drain skips the stream (unchanged behaviour).

## Known gaps (recorded, not fixed)

- Parse and pre-check refusals keep no trace (D-06). An unauthenticated MCP call is not audited (D-31). `IdentityCache` cannot name the org of a revoked key, so every failed credential goes to `_unauthenticated` (D-19).
- A provider raise that is not `UpstreamFailed` still reads `scan_failure` and `output_guardrail: block` (D-18).
- A pass-through stream that had already recorded its synthetic output ALLOW and then fails operationally shows `output_guardrail: allow, ran` beside `model_output: error` (the guard did run on earlier bytes); the strict pipeline shows `skip`.
- An input `redact` followed by an upstream `error` is final `error` and is counted in neither `redacted` nor `total_threats` (review R5-4).
- A streamed identity refusal stays plain HTTP (N11). A stream refused before the provider opens (`stream_buffer_unavailable`) records the cause for the audit row, but its SSE response carries no trace frame.
- `page-audit.md` lists 276 frontend reads of v1 fields in files with no v2 guard. Only the Scan Detail and Attack Simulator paths were reviewed. The module pages (RAG, routing audit, output governance, module charts) are unclassified and belong to the human page walk.
- The Responses API translation does not carry the console object on its own response shape (unchanged); chat refusals on `/v1/responses` do.
- `state_control/valkey.py:100` (`check_tenant_scale`) and the `dispatch/vector_transports/pinecone_transport.py:140` finding are pre-existing and untouched.

## Enumeration audit (requirement 3.6: where the control plane enumerates actions)

| Site | What it does | v2 effect |
|---|---|---|
| `policy/security_views.py` `SocKpisView` `gateway_v2` block | v2 totals | Rewritten (D-21): `total_threats`, `blocked`, `redacted`, `refused_total`, `error_total` |
| `policy/analytics_sql.py::soc_kpis_from_events` | block/redact/else rank | v1 only; v2 rows are `exclude_shadow`-ed before it |
| `policy/security_views.py` threat-feed `action_counts` | groups by raw action | v2-visible: an `error` key appears; the feed lists `error` rows |
| `policy/security_views.py` module KPIs (`is_blocked/is_redacted/is_flagged`) | named actions only | an `error` row falls in none of them, so it is never counted as a block |
| `core/dashboard_views.py` L103-104, L247-248, L387 | `filter(action=BLOCK/REDACT)` | counts block/redact only; `error` is not a block. The `total` there already includes v2 rows (unchanged) |
| `core/tasks.py::_build_enforcement_metadata` (v1 drain) | silent defaults | untouched |

## Page-level audit

`page-audit.md` (generated). Inventory regeneration diff (reviewed): `fields.json` adds `liveGateway.js` and `pipelineTrace.js` as readers of `final_action`/`source` and similar; `endpoints.json` adds the `v2_audit` consumer block (console rank incl. `error`, the `rejected`/`shed` outcomes, reserved org `_unauthenticated`); `routes.json` adds the three D-11 rows. `firewallconfig.json` is unchanged. Before this card the inventory was clean at `1634da0c` (verified on a detached worktree), so every drift line was caused by this card's edits.
