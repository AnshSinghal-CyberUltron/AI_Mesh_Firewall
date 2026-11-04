# GW27 Design Review (revision pass 6)

Reviewed `design.md` and `requirements.md` against `.worktrees/gw27/gateway_v2/gateway_v2`. No build or test suites were run.

Verdict: **APPROVED** (0 HIGH, 0 MEDIUM, 1 NIT).

## Pass-5 findings: verification

- F1 (MEDIUM, boot enumeration via `PlanStore.known()`): resolved. Section 5 "Fast path and capacity observation" now states that `known()` is never called, with no allowlist entry added. I confirmed `lint/check_tenant_scale.py:58` has `ESTATE_ENUMERATORS = frozenset({"known"})`, and that the only `.known()` text under `gateway_v2` is a docstring in that gate. `require_capacity` and the boot row are gone from the Error Handling table. A runtime gauge `org_limits_slots_used` plus `org_limits_table_full_total` replaces them. The design also updates the fixed `SERIES_COUNT` and `tests/admit/test_metrics_series_count.py`, and both exist (`admit/metrics.py` has `SERIES_COUNT` constants).
- F2 (MEDIUM, MCP audit scope): resolved. The surface table row, section 7, the audit test (routes, responses, mcp) and implementation step 6 now agree that MCP audit gating and stamping are in scope. MCP org rate/burst/TPM stays a named exclusion with owners GW17/GW32. In code, `edge/mcp.py` `_tenant` (line 204) calls `lookup_plan(..., Surface.MCP)` at line 214 and `ctx.holds.note_tenant` at line 213. `_Ctx` owns a `RequestHolds` (line 73). `emit_chat_audit` runs at line 144 and `release` at line 153. So `note_controls` after the plan lookup is feasible.
- F3 (NIT, org refund condition): resolved. The condition is written as `_org_refund is not None and not _dispatched`.
- F4 (NIT, ledger note for the `both` to `input` rewrite): resolved. The field 7 row and section 10 state it.

## Findings

### 1. NIT. Remaining wording check on the gauge's home (section 5)

The text says the new gauges are added to "the audit/admit producer". In code, `SERIES_COUNT` constants live per producer in `admit/metrics.py` (line 140 and line 262). Name the one producer that owns `org_limits_*` so the series-count test updates one constant. This does not block implementation, because the test fails loudly if the count is wrong.

## Verified Assumptions

- `ESTATE_ENUMERATORS` rule exists and `known()` is not used by serving code.
- `edge/mcp.py` resolves the plan, builds its own `RequestHolds`, emits audit in `finally` and releases holds, as the design states.
- `test_metrics_series_count.py` exists under `tests/admit`.
- The earlier verified items (lifecycle order, `RequestHolds` slots and release latch, `AuditRecord` shape, `posture.RATE_LIMITED` mapping) were verified in pass 5 and the design text for them is unchanged.

## Unverified / Wrong Assumptions

- Not verified: Postgres chooses the partial index for the retention batch query (a live-arm assertion).
- Not verified: `scripts/check_fleet_plan_schema.py` and the `V2_PLAN_*` settings are new work and do not exist yet.
- No wrong assumptions found in this pass.
