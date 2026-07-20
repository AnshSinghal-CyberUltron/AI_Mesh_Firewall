# P9.28 — Concurrency: parallel tool calls across all 15 MCPs (isolation + no drop/mix)

**Item:** P9 #28 — "Concurrency: fire parallel tool calls across all 15 (Everything
`echo`/`get-sum` deterministic) — assert correct isolation + no dropped/mixed responses."

**Verdict:** GREEN 3× consecutively. **Zero** dropped, id-mismatched, mixed, or
cross-target responses across the storm (1620 stormed calls over the 3 graded runs).

## What the harness now does (P9.28 phase)

`scripts/mcp_multi_org_harness.py` gained a **high-concurrency storm phase** (Phase 3.5):

- For each of `CONCURRENCY_ROUNDS` (default 3), fire `CONCURRENCY_DEPTH` (default 12)
  calls **per target** for **all 15 (org×server) targets at once** — i.e. `15 × 12 = 180`
  in-flight simultaneously per round, 540 per run — via one wide thread pool
  (`CONCURRENCY_WORKERS`, default 240) so the broker/sandbox **stdio id-demux** is the
  thing under test, not the client.
- Each call interleaves `echo` (a **globally-unique canary** embedding
  `conc~<org>~<server>~r<rnd>~s<seq>~<uuid8>`) and `get-sum` (**org+server-salted**
  operands so a cross-target mix yields a wrong sum string).
- Every response is classified precisely:
  | class | meaning | #28 gate? |
  |-------|---------|-----------|
  | `dropped` | no JSON-RPC response / null id | **FAIL** |
  | `id_mismatch` | response carried a *different* call's id (demux mix) | **FAIL** |
  | `mixed` | a *result* payload that isn't this call's | **FAIL** |
  | `cross_target` | a result carrying another `(org,server)`'s canary | **FAIL** |
  | `errored` | well-formed JSON-RPC **error**, correct id, no result | reliability → #29 |
  | `passed` | expected payload + correct id | ok |

  The #28 gate is zero-tolerance on `dropped/id_mismatch/mixed/cross_target`.
  `errored_under_load` is **not** a drop or a mix (correct id, self-consistent error
  body) — it is a saturation reliability signal handed to item #29. Set
  `CONCURRENCY_STRICT_ERRORS=1` to also gate on it (the #29 load gate).

## Graded gate — GREEN 3× (default depth 12, 180-wide)

```
run1: passed=540 errored=0 dropped=0 id_mismatch=0 mixed=0 cross_target=0 gate_violations=0  GREEN
run2: passed=540 errored=0 dropped=0 id_mismatch=0 mixed=0 cross_target=0 gate_violations=0  GREEN
run3: passed=540 errored=0 dropped=0 id_mismatch=0 mixed=0 cross_target=0 gate_violations=0  GREEN
```
Plus cross-tenant negative matrix 6/6 rejected (403) and capability probe 15/15 each run.
Reports: `concurrency_report_run{1,2,3}.json`.

Re-run gate:
```
for i in 1 2 3; do REPORT_PATH=mcp-parallel/findings/p9-28/concurrency_report_run$i.json \
  python3 scripts/mcp_multi_org_harness.py | grep -E 'concurrency integrity:|HARNESS:'; done
```

## Detector is proven to FIRE (test-the-test / negative control)

A detector that never trips is worthless. Constructing synthetic `CallRecord`s and
running the exact classification predicate confirms it flags every violation class and
keeps `errored` distinct from `mixed`:
```
NEGATIVE-CONTROL PASS: detector fires for dropped/id_mismatch/cross_target/mixed;
errored kept distinct; clean=passed
```

## Depth sweep — where the (non-#28) saturation boundary is

| depth | concurrent | passed | errored | dropped | id_mismatch | mixed | cross_target |
|------:|-----------:|-------:|--------:|--------:|------------:|------:|-------------:|
| 4  |  60 | 180/180 | 0 | 0 | 0 | 0 | 0 |
| 6  |  90 | 270/270 | 0 | 0 | 0 | 0 | 0 |
| 8  | 120 | 360/360 | 0 | 0 | 0 | 0 | 0 |
| 12 | 180 | ~538/540| 0–2 | 0 | 0 | 0 | 0 |
| 16 | 240 | 458/480 | 22 | 0 | 0 | 0 | 0 |  (`saturation_depth16_report.json`)

**Key result:** demux + tenant/server isolation are perfect at *every* depth. The only
degradation under extreme aggregate concurrency is application-level **errors**, never a
drop, mix, or cross-tenant leak.

## Root cause of `errored_under_load` (handed to item #29 — NOT Cursor-owned)

Under ~180–240 simultaneous calls, a fraction return HTTP 200 with a JSON-RPC error
(correct id, no result):
- `{"code":-32000,"message":"Internal server error."}`
- `{"code":-32000,"message":"Tool is not registered for this server"}`

Trace:
- `"Tool is not registered for this server"` originates in **control**
  (`control/ai_mesh_control/mcp_connector/views.py:1076`, reason `tool_not_registered`,
  HTTP 403).
- `"Internal server error."` is control's DRF 500 handler
  (`control/ai_mesh_control/main_app/exception_handlers.py`).
- The **gateway** proxies `tools/call` to that control backend and forwards the backend
  error verbatim into a `-32000` JSON-RPC error
  (`gateway/ai_mesh_gateway/mcp_proxy.py:2540`).

So the saturation errors are a **control (Django) + gateway load ceiling** — the tool
call reaches control's policy/authz layer, which intermittently 500s / transiently sees
the tool as unregistered under connection/worker pressure. This is **item #29**
(load / sustained calls / no 503 storms / resource limits) and lives in **control +
gateway** (Claude/control-owned per `docs/mcp/PARALLEL_CLAIMS.md`). No Cursor-owned
component (broker `docker_manager`, `sandbox-image/agent`) is implicated: a single
server at 60-deep is 100% clean; only aggregate cross-org load through the control
policy plane degrades.

## Ownership / gate notes

- `docker_manager` was **not** touched this iteration → broker test gate not required.
- Changed (Cursor-owned): `scripts/mcp_multi_org_harness.py`, this findings dir, claims,
  `mcp_progress.md`, scratchpad.

## iter22 unblock check — still BLOCKED

`broker_send_rpc` still absent from `gateway/**`; broker exposes only
`/{org_slug}/stdio/rpc` (`services/mcp-broker/src/sandbox/routes.py:212`), **no**
transport-agnostic `/{org}/rpc`. P4.13 / P6.18 (all-transport-through-sandbox wiring)
remain blocked on the Claude session. (stdio concurrency — this item — is fully wired
and passing; the block is only http/sse/ws transports.)
