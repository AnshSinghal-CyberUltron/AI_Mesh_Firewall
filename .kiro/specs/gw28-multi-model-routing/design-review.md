# Design Review — GW28 Multi-model routing (round 5)

Reviewed: `design.md` against `requirements.md` and the code in `.worktrees/gw28/gateway_v2/gateway_v2`.
Prior-round MEDIUMs (second pooled client, worker-level trace sink) are resolved. One new MEDIUM remains.

## Findings

### 1. MEDIUM — D-11 provider pool-share rule contradicts its own boot assertion and cannot be enforced as written
Where: D-11 "Production multi-client lifecycle and budget", C8 `provider_pool_share(n)`, test T-life-1.

Verified in source: `ResourceContract.pool_size(PoolKind.PROVIDER)` is `workers()` (`runtime/resources.py`
L295-296, `n = w`). So the budget is small: 1 on a 1-worker contract, a handful on small pods.

- `provider_pool_share(n) = max(1, pool_size(PROVIDER) // n)` floors every share at 1. Whenever
  `n > pool_size(PROVIDER)` (for example 3 providers on a 2-worker pod), the sum is `n`, which exceeds the budget.
  The design's own boot assertion `sum(shares) <= pool_size(PROVIDER)` then raises `RoutingConfigError`. The `max(1, …)`
  floor is therefore dead or contradictory, and the design never says which one wins.
- T-life-1 asserts `sum(provider_pool_share(n)) <= pool_size(PROVIDER)` "for n in 1..8". That is false for any test
  contract with fewer than 8 workers, so the test as specified fails.
- `build_routed_provider` receives already-built clients. `OpenAIUpstreamProvider` keeps `connections` inside the
  `httpx_client_factory` closure and exposes no attribute for it. "Requires each distinct client to have been built
  with `connections = share`" has no way to check what the client was actually built with. The assertion only
  compares computed numbers and enforces nothing about real pools.

Fix (pick and state one):
- Remove the `max(1, …)` floor. `provider_pool_share(n)` returns `pool_size(PROVIDER) // n` and raises
  `CapacityUnavailable` when the result is `< 1`. Document that a table with more distinct clients than
  `pool_size(PROVIDER)` refuses to boot, fail closed. Limit T-life-1's loop to `n <= pool_size(PROVIDER)` and add a
  negative case (`n > pool_size` raises).
- Make the budget checkable. Either have `build_routed_provider` construct the clients from a factory callable it
  calls with `connections=share`, or add a read-only `connections: int` property to `OpenAIUpstreamProvider`
  (additive, same one-line spirit as `per_request_safe`) and assert `sum(c.connections) <= pool_size(PROVIDER)`.

### 2. LOW — `total_s` is per attempt, so the "router wins the race" claim fails after a fallback
Where: C8 `upstream_total_timeout_s`, P7/P11, review round-4 F5 response.

`InFlightControl._deadline = clock() + max_stream_duration_s()` (`edge/cancel.py` L427) is measured from stream
start. The router's `total_s` is measured from each attempt's start. A second attempt starts up to about 30 s in, so
a slow trickle on attempt 2 reaches the edge's 300 s kill first and ends as `stream_killed`, not the router's
`upstream_timeout`. The terminal is still single and declared (fail closed), but the property text is wrong.
Fix: make the router's total deadline absolute from `open_routed` entry (one `start` shared across attempts), or
scope the P11 clause to the single-attempt case and state the fallback case ends in the edge max-duration code.

### 3. LOW — Zero-provider-call refusals inside `open_routed` do not refund the budget spend
Where: C6 `_stage` preflight vs `routes.py::_attempt` / `openai_nonstream._collect_output_channels`.

Both chat sites call `holds.mark_dispatched()` before the open. `routing_all_unavailable`,
`routing_snapshot_unavailable`, `routing_context_missing` and `routing_internal_error` pre-open are produced inside
`open_routed` with zero provider calls, so the spend stands. Preflight refusals are refunded, so the two groups are
inconsistent, and the unrefunded group is the one that fires during a provider outage. Fix: state the behaviour
explicitly, or have `open_routed` expose "no candidate opened" so the edge can release the dispatch mark.

### 4. LOW — Mid-stream gap tightening also applies to streaming
Where: D-9.

D-9 frames 0.8 s as new strictness for non-streaming only. On streaming the shipped bound was 1 s, so routed
streaming also drops 1 s to 0.8 s. Fix: say so in D-9 and the PR-description notice.

### 5. NIT — Candidate ids surface in the client-visible response body
Where: C6 Trace.

`apply_projection` merges `pipeline_trace` (including `deciding_rule` with `sel`/`from`) into the JSON body returned to
the client. State that candidate ids are org-owned and safe to expose, or hash them.

## Verified Assumptions
- `resources.py` is exactly 800 lines; `snapshot()` is literal-free and has exactly two importers (`runtime/pools.py`, `tests/runtime/test_lgw12_resources.py`); `check_sizes` caps modules at 800.
- Derived values check out: `cancellation_bound_s` is 3×p99 (60 ms), `inter_chunk` 1 s, `max_stream_duration` 300 s, so total 299.94 s and pre-content budget 60.18 s for n=2.
- `_next_attempt` is a one-shot via the `offered` latch; chat open sites are exactly `routes.py:691` and `openai_nonstream.py:199`; misc/embeddings opens separately.
- `StreamTimeouts.next_event` wraps every `__anext__`, and `_last_write_s` is seeded at construction, so the idle-timer hazard (D-18) is real and the fix is coherent.
- `console_schema._STAGE_KEYS` is closed to `name, action, ran, deciding_rule`; `chat_console` has the six fixed stages.
- All retryable and non-retryable `upstream_*` codes exist in `openai_upstream.py` and the `edge/errors.py` and `wire/sse.py` maps.
- `OpenAIUpstreamProvider.abort()` is a no-op and `open()` returns the async iterator synchronously.

## Unverified / Wrong Assumptions
- Wrong: `sum(provider_pool_share(n)) <= pool_size(PROVIDER)` for n in 1..8 (Finding 1).
- Wrong: router `upstream_timeout` always wins over the edge max-duration cut (Finding 2, fallback case).
- Unverified: that `asyncio.wait_for` cancellation of an `httpx` stream read never returns a half-open socket to the pool. Design correctly defers live confirmation; the in-process transport test is only a proxy.
- Unverified: where `RoutingTrace` is handed to `console_from_holds` on the streaming trace frame. The design names the call chain but I did not trace the trace-frame emission path.

## Verdict
1 MEDIUM, 3 LOW, 1 NIT → **CHANGES_REQUESTED**.
