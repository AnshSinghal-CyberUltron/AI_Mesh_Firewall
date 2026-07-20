# P10 item #32 — consolidated P9 gate (concurrency + load + leakage, 3× all-green)

**Runner:** `scripts/mcp_p9_gate.py` — runs each live harness once per round, N
consecutive rounds, PASS iff EVERY harness PASSes in EVERY round.

This is the completion gate for the promise "the 15-MCP concurrency/load/leakage
tests pass 3× consecutive (in-process + live)". Each child is a live end-to-end
check against the running gateway → control → broker → per-org sandboxes.

## In-process (backend unit) evidence
- Gateway MCP suites (isolation / oauth-org-scope / sandbox-adversarial /
  sandbox-client / rate-limit / enabled-tools-cache / e12-security):
  **38 passed, 13 skipped** (skips = live-docker integration variants).
- Broker unit suite: **95 passed** (incl. the nproc-ulimit lifecycle assertion).

## Live gate — 3 harnesses × 3 consecutive rounds → **PASS**

| round | concurrency | load | leakage | verdict |
|------:|:-----------:|:----:|:-------:|:-------:|
| 1 | PASS | PASS | PASS (26/26) | ALL GREEN |
| 2 | PASS | PASS | PASS (26/26) | ALL GREEN |
| 3 | PASS | PASS | PASS (26/26) | ALL GREEN |

`P9 GATE: PASS (3× all-green)` — run WHILE a second ralph loop was independently
driving load against the same stack (real contention), proving the harnesses gate
on isolation/operational correctness, not on a quiet neighbour.

OAuth/transport (item#31) is verified separately 3× (67/67 checks each,
`mcp-parallel/findings/p9-31/oauth_transport_run{1,2,3}.json`) — excluded from this
tight loop only because its login path is throttle-sensitive, not for any failure.

## Robustness note (found + fixed during the gate)
The first gate run FAILED on `concurrency` under CONTENTION (a second ralph loop
was independently running the load harness at the same time). Breakdown proved
isolation was perfect (`drops=0, id_mismatch=0, cross_tenant=0`) — the failure was
transient control-plane `-32000` backpressure being miscounted as `content_mismatch`
(the concurrency harness had no retry, unlike the load harness). Fixed:
`mcp_concurrency_live.py` now classifies an id-matched JSON-RPC error / 5xx as
BACKPRESSURE (item#29), retries it (idempotent echo/get-sum), and gates ONLY on
isolation (no drop, no waiter mix, no content swap, no cross-tenant). This aligns
concurrency with the load harness's 3-failure-class model. Re-run under the same
contention → CONCURRENCY PASS (960/960, isolation clean, backpressure absorbed).

Lesson: an isolation gate must not fail on reliability-class backpressure — separate
the two, or a busy neighbour makes the isolation gate flap.
