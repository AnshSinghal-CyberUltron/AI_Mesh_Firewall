# Task 1C result — flush trigger fixed; 31.9× less guard CPU

**Tag `[M]`** · Docker `aimeshperf`, 45 policies, block posture, Tier-2 OFF, streaming, 100 tok/s,
gateway 4 CPU / `WEB_CONCURRENCY=4`

## The change

`secure_streaming.py` — the buffer-limit flush trigger now counts **chunks appended since the last
flush** instead of total queue depth:

```python
-  if len(self._chunk_queue) >= self._max_buffer_chunks:
+  if self._chunks_since_flush >= self._max_buffer_chunks:
```

plus `self._chunks_since_flush += 1` on append and `= 0` on flush. Three edits.

It changes **when** a flush fires, never **what** is scanned — so lookahead retention, the secret
anchor, and the open-media holdback are untouched.

## Result

| tokens | guard_accum before | after | reduction |
|---:|---:|---:|---:|
| 100 | 112.5 ms | **6.70 ms** | 16.8× |
| 200 | 454.6 ms | **18.10 ms** | 25.1× |
| 300 | 793.2 ms | **24.90 ms** | **31.9×** |

The curve is now **linear** (6.7 / 18.1 / 24.9 against 1 / 2 / 3 tokens) instead of growing with
flush count. At 300 tokens the gateway reclaims **768 ms of CPU per request**.

## What it did NOT fix — stated plainly

| tokens | ADDED WALL CLOCK before | after |
|---:|---:|---:|
| 100 | 150.51 ms | 119.70 ms |
| 200 | 150.82 ms | 142.68 ms |
| 300 | 152.38 ms | 144.95 ms |

**Added latency barely moved.** That is expected and was predicted: guard work ran *concurrently* with
generation, so removing it frees CPU without shortening the wall clock the caller experiences.

**This is a throughput fix, not a latency fix.** It raises the RPS ceiling substantially — 768 ms of
CPU per request is ~7.7% of a 4-core budget at 100 tok/s — but the ~120–145 ms of added streaming
latency has a different cause that is now the dominant remaining target.

## Verification

| check | result |
|---|---|
| New tests (`test_stream_flush_per_token.py`) | 3/3 pass |
| Streaming safety suites (`e14_streaming_split`, `secure_streaming`, `stream_failclosed_parity`) | 38/38 pass |
| Wider `-k "stream or flush or e13 or e14 or guard"` selection | **0 new failures** |
| Pre-existing failures in that selection | 64 before → 62 after; the 2-test delta is this task's own new tests flipping red→green |

The 62 remaining failures are `test_pii_and_secrets_are_masked_before_upstream` and kin — PII and
secrets not being masked. They predate this change and belong to the `policy-driven-detection` WIP's
fail-toward-no-detection state, not to the flush trigger.

### The unit test that pinned it

```
same 1,290 bytes cost 152 guard passes as 6-byte chunks but only 1 as 120-byte chunks
```

Identical content, identical scanning required, 152× the guard passes purely because the provider
chose smaller deltas. That test now passes and guards the invariant.

## Next target

Added streaming latency is **~120–145 ms** and is no longer guard-dominated. Non-streaming sits at
**15.2 ms p50 / 20.5 p90**. The streaming figure is still ~7× the 20 ms goal, so the next task is to
find where that time goes — it is not the output guard, not Tier-1 (0.10 ms) and not the policy
engine (0.20 ms).
