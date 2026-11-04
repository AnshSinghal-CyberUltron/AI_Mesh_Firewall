# GW35 console truth: stage outcomes, error action, failed-credential audit

GW35 replaces the hard-coded `auth`/`rate_limit` allows with a pure function (`console_stages.build_stages`) over lifecycle facts recorded on `RequestHolds`. Pre-scan refusals now carry a six-stage trace in the HTTP body or SSE `amf.trace` frame. `error` is added as a console-only word (not a `Disposition`) and driven by a single failure-class table in `domain`. Failed credentials write a rate-bounded admission record to a reserved `_unauthenticated` stream with a 12-hex fingerprint. The control adapter, frontend and console-inventory lint are updated to match.

Watch for: live gate (L35-1/L35-2) and owner signatures are correctly not claimed; `check_tenant_scale` pinecone failure is pre-existing with a recorded baseline (possible, not re-run).

**Verdict**: APPROVED

## High-level view

Stage truth comes from one pure builder fed by per-request facts, so HTTP body, SSE frame and audit record cannot disagree. `ran` comes from ledger facts, never latency. The schema validator now pins stage names, canonical order, skip/ran consistency, and `final_action` agreeing with the stages. The one allowed `error` + `ran:false` pair is `input_scan` for `plan_unavailable`.

Upstream and stream faults are classified in `domain/failure_class.py`; unknown codes fail closed to policy (`block`). `note_output_action` rejects `error`, so a fault cannot paint `output_guardrail`.

Failed credentials go to `_unauthenticated` via a fixed-window gate sized from `ResourceContract`; the capacity multiplier lives in `runtime/resources_audit.py` (the sibling pattern to `resources_vector.py`). The gate fails closed, drops are counted, and the response is never touched.

<details>
<summary>Issues (2)</summary>

1. **Unmeasured audit-rate multiplier** (non-blocking, confirmed) — `_UNAUTH_AUDIT_RATE_MULT = 2` is documented as an owner decision pending signed capacity; track it.
2. **Pre-existing gate failures** (non-blocking, likely) — `check_tenant_scale` pinecone finding and 9 mypy missing-vendor-SDK errors are recorded with a base-commit baseline; confirm on CI.

</details>

<details>
<summary>Details</summary>

### Stage derivation and the error word
`build_stages` is total over `ChatFacts` and holds no latency or stats input. `_auth`/`_rate_limit` give block/error/skip per codes in R1.2/R1.3, with `overload_shed` mapped to `rate_limit` (D-03). `stages_to_console` returns `None` for unknown refused codes rather than an allow trace, and the schema rejects `kill_switch`/`policy`/`model_input` by name. `final_action` is computed from stage severity with `block > error`, matching D-01 and the control `_RANK` change.

### Failed-credential audit
`emit_chat_audit` attributes to the resolved org or `_unauthenticated` for chat identity refusals only; MCP stays unaudited (a recorded gap). The fingerprint is `hashed_bearer_key(scope)[:12]`, and `_reject_evidence` rejects any non-12-hex `key_fingerprint` or a bare 64-hex string, so the full hash cannot be stored. Audit failure is swallowed in `emit_chat_audit`, so the 401 is unchanged.

### Frontend
`liveGateway.js` keeps the nine-stage template for v1 only. A v2 body goes through `normalizeGatewayV2Result`, which shows exactly the returned stages and takes the verdict from `final_action`.

### Evidence
`verification.md` records full offline pytest (3164 passed, 1 pre-existing failure), ruff, import-linter, all AST gates and the console inventory lint clean, and frontend 208 tests plus build. Properties ran 200 to 1000 iterations with negative controls and a golden capture of pre-change refusals. Not run and not claimed: Docker/staging gate, Playwright sweep, owner signatures. I did not re-run any suite, per instruction.

</details>

<details>
<summary>File map</summary>

Full diff: `git diff suraj-revamp...HEAD` in the worktree.
- `edge/console_stages.py`, `console_projection.py`: pure stage builder and facts copy.
- `domain/failure_class.py`: operational vs policy table.
- `audit/console_schema.py`, `audit/record.py`: `error`, stage validation, `output_failure`, `_unauthenticated`.
- `edge/unauth_audit.py`, `runtime/resources_audit.py`: bounded gate and derivation.
- `edge/request_holds.py`, `produced_holder.py`, `routes.py`, `openai_*.py`, `audit_emit.py`: facts, cut recording and wiring.
- `control/.../v2_audit_adapter.py`, `retired_routes.py`: error rank and 410 `retired_on_gateway_v2`.
- `frontend/...`: error rendering, v2 trace path, retired panels.
- `lint/*`, docs evidence: vocabulary parity, ledgers.

</details>
