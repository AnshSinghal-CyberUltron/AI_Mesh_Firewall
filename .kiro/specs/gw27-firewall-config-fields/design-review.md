# GW27 Design Review (revision pass 5)

Reviewed: `design.md` and `requirements.md` against the code in `.worktrees/gw27/gateway_v2`. No build or test suites were run.

Verdict: **CHANGES_REQUESTED** (0 HIGH, 2 MEDIUM, 2 NIT).

## Pass-4 findings: verification

All six are resolved in the text.
- F1 (refund): bound at charge time via `org_charged(org_id, cost, refund)`, `release` signature unchanged. The four real callers (`routes.py:364`, `openai_responses.py:148`, `openai_misc.py:391`, `mcp.py:153`) are named and confirmed in code. `openai_chat.py` has no `release`.
- F2 (audit controls delivery): `RequestHolds.note_controls` plus a separate `stamp` mapping. Confirmed `emit_chat_audit(emit, holds, refuse_code, *, route, console, detail, observer)` takes only `holds`, and `_records` types `extra` as `Mapping[str, str]`. The design keeps `extra` and adds `stamp`, which is consistent. Plan resolution (phase 4) precedes admission, so `note_controls` can run before any later refusal.
- F3 (retention enumeration): keyset pagination with `org_id > after_org ORDER BY org_id LIMIT` and a per-org batch cap now terminates in `dry_run` and under persistent locks.
- F4, F5, F6: resolved (`DurableRecord`, wall-clock `time.time_ns` default, snapshot committed first in step 8).

## Findings

### 1. MEDIUM. Boot enumeration calls `PlanStore.known()`, which the tenant-scale AST gate forbids (section 5, "Fast path and boot enumeration")

The design has the boot wiring call `PlanStore.known()` to count controlled orgs for `OrgLimits.require_capacity`. It records only that `known()` exists (`plan/store.py:63`). It does not say that `known()` is documented "diagnostics and tests only. Never call this from a serving path or a periodic refresh". `lint/check_tenant_scale.py` rule 2 enforces this: `ESTATE_ENUMERATORS = {"known"}`, and any call to `known()` outside the file that defines it is a violation. That gate is one of the five AST gates the design requires to exit 0 over `gateway_v2`. As written, the card cannot pass its own offline gate. It also reintroduces the R2-02 estate-enumeration pattern. The boot-time `CapacityUnavailable` row in the Error Handling table depends on it.

Fix: remove the boot enumeration. Count incrementally instead.
- `OrgLimits` already knows the slot usage at runtime. Add a label-free gauge `org_limits_slots_used` and the existing `org_limits_table_full_total`, and alarm on slots_used near `slots`.
- Keep the runtime fail-closed refusal when the table is full.
- Delete `require_capacity(controlled_org_count)`, the "Controlled-org count exceeds slots at boot" row, and the `PlanStore.known()` reference.
- Optionally pre-count in `plan/delta.py` as each plan compiles (already per-tenant, O(changes)) and pass the count to a gauge. Do not add an allowlist entry for `known()`.

### 2. MEDIUM. MCP audit gating is excluded in the surface table but required in section 7 and the tests (sections 5 and 7)

The section 5 surface table lists MCP routes as "audit: owner GW17/GW32". Section 7 says `mcp.py:144` is a caller whose gating and stamp are verified. The Testing section requires "one case per caller (routes, responses, mcp)". Code confirms `mcp.py` builds its own `RequestHolds` in `_Ctx`, resolves a plan (`lookup_plan(..., Surface.MCP)` at `mcp.py:214`), spends the org budget, and calls `emit_chat_audit` in `finally` at line 144. So MCP can be gated, but the two sections disagree on whether it is in scope. If MCP is excluded, an operator who sets `audit_logging_enabled=false` still gets MCP audit records. That is a partly stored-and-ignored BUILT field, which the card forbids. The answer also decides whether `_Ctx` must call `holds.note_controls(plan.controls)` after `_tenant`.

Fix: choose one. Recommended: MCP audit gating and stamp are IN scope. Edit the table row to "wired (MCP resolves the plan at `mcp.py:214`; `_tenant` calls `ctx.holds.note_controls(plan.controls)`)". Keep org rate/burst/TPM as the named MCP exclusion (owner GW17/GW32) and say in the ledger `via` text that those three fields do not cover MCP. Add the MCP file to the implementation step 6 file list.

### 3. NIT. Org refund condition wording (section 5, "RequestHolds changes")

The text says the org refund runs "under the identical condition as the lease refund (`charged and not dispatched`)". The lease refund is `_spent and not _dispatched`. `_spent` is set only after the budget phase succeeds. The org charge happens before the budget phase, so a budget refusal leaves `_spent` False. The condition must be `_org_refund is not None and not _dispatched`, independent of `_spent`. The lifecycle test already expects a refund when the budget refuses. Write the condition out as an explicit expression.

### 4. NIT. `output_policy_enabled=false` and `scope == "both"` rewriting needs a ledger `via` note

Rewriting a `both` row to `input` is correct and tested. Add it to the ledger `via` text for field 7 so the console note shows an operator that output halves were removed.

## Verified Assumptions

- Lifecycle order in `openai_chat.py`: parse, identity, plan (phase 4), clamp, `holds.note_tenant`, admission (5), `_estimated_cost`, budget (6), input scan (7). Phase 5b fits after `holds.admitted`. `clamp_output_budget` currently uses `contract.default_response_token_ceiling()` and can take `plan.controls.max_response_tokens`.
- `RequestHolds` has slots, a `_released` latch and `release(admission, quota)`. The proposed additions fit.
- `AuditRecord.detail` is `Mapping[str, object]` and `outcome` carries the disposition or SHED/REJECTED, so `_is_mandatory` can read only the record.
- `amf_audit_record` has `recorded_at_ns bigint`, PK `(org_id, stream_id)`, and `RECORD_COLUMNS` lives in `audit_control/schema.py`.
- `posture.RATE_LIMITED` is mapped to 429 in `edge/errors.py:279`; `rate_limited_verdict` exists in `admit/grant.py`.

## Unverified / Wrong Assumptions

- Wrong: `PlanStore.known()` is usable from boot wiring (finding 1).
- Inconsistent: MCP audit scope (finding 2).
- Not verified: Postgres chooses the partial index for the batch query. This is the live-arm assertion.
- Not verified: `scripts/check_fleet_plan_schema.py` and the `V2_PLAN_*` settings are new work and do not exist yet.
