# GW28 Design Review (pass 3, fresh read)

Verdict: CHANGES_REQUESTED (0 HIGH, 2 MEDIUM, 5 LOW)

## Verified assumptions (read from source)
- `DispatchRouter` is pure, `__slots__ = ()`, identity = org/epoch/sequence/content_hash; left unchanged.
- `OpenAIUpstreamProvider`: `abort()` no-op, `_drain` holds one frame back, role channel stripped in `_content`, `httpx.TimeoutException -> upstream_timeout`, 5xx other than 504 -> `upstream_error`, 429 -> `upstream_rate_limited`. D-5 code set matches.
- `routes.py::_next_attempt` has the `offered` one-shot latch; `openai_nonstream.py` has a single `deps.provider.open` site (L199). D-12 is true.
- `StreamTimeouts.next_event` wraps every await in `inter_chunk_timeout_s()`; `_last_write_s` is seeded at construction and `send()` cuts on idle. D-13/D-18 diagnosis is accurate.
- `resources.py` is exactly 800 lines; `snapshot()` (L766-800) is literal-free; importers are `pools.py` and `tests/runtime/test_lgw12_resources.py`. D-15 is feasible. `_stream_timeout_s` multiplies p99 and floors at one p99. Defaults 1500x = 30 s, 15000x = 300 s check out.
- `dev_profile._provider` builds the timeout from `max_stream_duration_s()` (the 300 s root cause).
- `upstream_timeout` is already mapped in `edge/errors.py` (504) and `edge/wire/sse.py`.
- No `ROUTING_*` codes exist yet in `domain/posture.py` (additive, as designed).
- Layering: new dispatch modules import only domain and dispatch siblings; bounds are injected. No violation found. No module-level mutable state or capacity literal outside `resources.py` is introduced by the design.

## Unverified / wrong assumptions
- The per-request trace record that `console_projection` reads was never read (design admits this, Open Items). See Finding 2.
- "Chat provider closes with the app": not true for a second client. See Finding 1.
- Equating `holds.request_id` with a per-request identity: it is a content hash, not unique. See Finding 2.

## Findings

### 1. MEDIUM - Second pooled client has no shutdown path and no connection budget (D-11, C6 dev_profile)
Design adds `chat_provider`, a second `OpenAIUpstreamProvider` with its own httpx pool. In the real code, shutdown closers are built in `dev_profile.py` (~L517) as `(provider.aclose,)` for `deps["provider"]` only. `RoutedProvider` has no `aclose`, and `build_routed_provider` (production) has no owner for client lifecycle. The chat pool would leak sockets at lifespan shutdown. Also `connections = contract.pool_size(PoolKind.PROVIDER)` is the signed provider socket budget; a second client with the same value doubles the fleet's upstream sockets/fds, and the design says nothing about how the budget is split.
Fix: (a) state that `chat_provider` is added to `closers` (and `build_routed_provider` returns or registers the clients to be closed on shutdown), with a test that both pools are closed on lifespan shutdown; (b) either share one pool and apply the 30 s bound only at the app-level `bounded_events` (transport timeout stays as is for both), or split `pool_size(PROVIDER)` explicitly between the two clients and assert the sum does not exceed it. The shared-pool option is simpler and removes the T-edge-7 need for separate clients, since the router bound already enforces 30 s on chat.

### 2. MEDIUM - Trace sink is worker-level and keyed by a non-unique id; Req 6 target unspecified (C4, C6 Trace, Open Items)
`RoutedProvider` takes one `sink: Callable[[RoutingDecision], None]` for the whole worker, and `RoutingDecision` identifies a request only by `request_key = holds.request_id`. `ChatSurface._request_id` is a pure function of (org, model, input channels), so two concurrent identical requests share the id. A sink that appends to a per-request record by that key will conflate traces, the same defect class as PIPELINE-0023 (client request id collision merging prompts). Separately, the destination record type is "not yet read", so Req 6.1/6.3 and acceptance criterion 7 are not implementable without guessing.
Fix: make the sink per request. Add a `RoutingContext.sink` (or a third `open_routed(request, ctx, sink)` argument) that `edge/chat_upstream.py` binds to the request's own trace record via `holds`; remove the worker-level sink from `RoutedProvider.__init__`. Keep `request_key` for ranking only. Read `console_projection` now and name the concrete record type and the field mapping table (RoutingDecision field -> model_routing stage key) in the design. Add a test: two concurrent identical requests produce disjoint decision lists.

### 3. LOW - A2 / Glossary "Candidate_Set derived from the pinned plan" is not met
D-1 says so honestly (boot-time, unsigned, org-keyed table; a plan epoch change does not touch it). Req 1.2 is met via the HRW key only. Fix: amend requirements A2 to record the deviation (and owner sign-off) so the requirement and design do not conflict.

### 4. LOW - Req 5.6 start-up logging is only wired for the dev profile
The "logged at start-up" bound set is added to the `snapshot()` dict and the dev boot log. Production goes through `build_routed_provider`. Fix: have that factory log the four bounds, and add a test asserting the line.

### 5. LOW - `total_s` equals the edge `max_stream_duration_s` (both 300 s)
On a slow trickle the router's `upstream_timeout` and the edge max-duration kill race, so the terminal code is nondeterministic. P11 does not cover total. Fix: set `total_s` slightly below, e.g. `max_stream_duration_s() - cancellation_bound_s()`, and extend P11 to assert `total < max_stream_duration`.

### 6. LOW - Residual provider-side generation after a timeout fallback is not recorded
Closing the socket after `upstream_timeout` does not guarantee the provider stopped generating; a fallback attempt can overlap billing. This satisfies Req 2.3 (released and aborted) but should be recorded as a residual in the design.

### 7. LOW - HRW cost on the request path
Up to 8 candidates x weight 16 = 128 sha256 calls per rank. Likely well under the 20 ms SLO, but unmeasured. Fix: add a micro-benchmark assertion for worst-case `rank` in P2, or compute one digest per candidate and derive vnodes cheaply.

## Requirement coverage
Deterministic routing (P1-P3), no double generation (P4, P8, D-12), no policy bypass (P6, T-edge-5, D-8 structural), stream integrity (P5), stall bound (P7, P11, D-13/D-14/D-18) are all concretely designed and have property tests. Req 6 is the only requirement not implementable as written (Finding 2).
