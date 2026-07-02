# P9 item #28 — concurrency correctness under burst load

**Harness:** `scripts/mcp_concurrency_live.py`
**Fleet:** 3 orgs × 5 Everything stdio MCPs = 15 (from `.mcp_scale_manifest.json`)
**Load:** CONCURRENCY=8 per MCP → 240 in-flight requests/round (8 echo + 8 get-sum × 15
servers), 6 rounds/run. Each server's stdio agent sees up to 16 concurrent JSON-RPC
calls at once, stressing its `_pending`-dict multiplexer.

## Dual-oracle design (why it detects mixed/dropped/swapped replies)

Every request carries a unique JSON-RPC `id` AND a unique nonce in the echo body.

- **id-match** (`resp.id == sent.id`): the transport/multiplexer delivered THIS
  response to THIS waiter — a swap fails here.
- **nonce-match** (echoed text contains OUR nonce): the payload is ours — a content
  swap fails here even if ids lined up.
- **get-sum(a,b)** arithmetic oracle: deterministic a+b per request.
- **cross-tenant**: an org's reply must never contain another org's nonce.
- **drop**: any non-200 / exception ⇒ counted (a missing reply is a finding).

PASS iff drops=0 ∧ id_mismatch=0 ∧ content_mismatch=0 ∧ arith_mismatch=0 ∧
cross_tenant=0 ∧ received==expected.

## Result — GREEN 3× consecutively

| Run | expected | received | drops | id_mm | content_mm | arith_mm | xtenant | p50 ms | p99 ms | verdict |
|----:|---------:|---------:|------:|------:|-----------:|---------:|--------:|-------:|-------:|:-------:|
| 1 | 1440 | 1440 | 0 | 0 | 0 | 0 | 0 | 4266.8 | 10136.4 | PASS |
| 2 | 1440 | 1440 | 0 | 0 | 0 | 0 | 0 | 3177.9 | 5716.3 | PASS |
| 3 | 1440 | 1440 | 0 | 0 | 0 | 0 | 0 | 3135.6 | 5543.4 | PASS |

4320 concurrent tool calls total, zero anomalies on every channel.

## Cold-start observation (belongs to item #29 / B3, not a concurrency defect)

The FIRST unwarmed run (no warmup phase) showed `content_mismatch` clustered in
round 0 (47) with a few in round 1, then **zero from round 2 on** — while
`id_mismatch` and `drops` stayed 0 throughout. Root cause: a `tools/call` that
lands before the server finishes `initialize` returns an id-matched HTTP-200 whose
content is not the echo result. That is a readiness/warmup property (item #29 load
+ B3 readiness poll), NOT a transport correctness bug: ids never mixed, nothing was
dropped, no cross-tenant. The harness now runs a bounded per-target warmup (one
echo, retried until it echoes) before the measured burst, so item #28 measures
steady-state multiplexing. The cold-start behaviour is carried forward as an
explicit item #29 assertion (sandbox reuse + no 503 storms under sustained load).

## Reusable pattern

To prove "no dropped/mixed responses" under concurrency, stamp each request with
TWO independent unique keys (protocol id + payload nonce) and assert BOTH
round-trip. id-match alone can't catch a server-side content swap; nonce-match
alone can't catch a transport waiter mix-up. Together they cover both layers.
