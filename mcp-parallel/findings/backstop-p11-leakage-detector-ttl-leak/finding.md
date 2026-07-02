# CHG-0084 — LeakageDetector.track_cross_request: non-atomic SADD+EXPIRE → orphan Redis key (TTL leak under soak)

**Change-id:** CHG-0084
**Date:** 2026-07-02
**Severity:** MEDIUM (Redis memory-exhaustion / DoS under soak; same class as CHG-0062)
**Area:** HARDEN THE ARCHITECTURE — PostgreSQL + Redis correctness (item 11) / soak no-exhaustion (item 16)
**Files:** `gateway/ai_mesh_gateway/leakage_detector.py` (+ `tests/test_leakage_detector_ttl_atomic.py`)
**Whose work it touches:** the cross-request leakage detector's Redis state.

## How it was found (a Redis-correctness sweep, applying the CHG-0062 lens)

After CHG-0062 fixed one non-atomic INCR+EXPIRE, swept the gateway's Redis write ops for the same
orphan-key pattern (a mutation followed by a SEPARATE `expire`).

## Gap

`LeakageDetector.track_cross_request` tracks cross-request sensitive-fragment hashes in a Redis SET
(`leakage:cross:{key_hash}`) with a window TTL. It did:
```python
for fragment in fragments:
    await self._redis.sadd(redis_key, frag_hash)   # per-fragment sadd (N round-trips)
await self._redis.expire(redis_key, self._cross_request_window)  # SEPARATE expire
```
The `sadd`(s) and the `expire` were separate awaited round-trips. A coroutine cancellation (client
disconnect under load — routine at scale) or a transient Redis error between the last `sadd` and the
`expire` leaves the SET populated but with **NO TTL** → orphaned forever. Under soak (item 16), such
orphaned per-key sets accumulate → **unbounded Redis memory growth → exhaustion**. Same class as the
CHG-0062 rate-limit atomicity bug.

## Fix

One `MULTI/EXEC` (`pipeline(transaction=True)`) sets all fragment members + the window TTL **atomically**,
so the key can never be left without a TTL (and it is now 1 round-trip, not N+1). Sliding-window semantics
preserved (EXPIRE is re-set each call; no `nx`). Fail-safe unchanged (the method already returns 0.0 when
`_redis is None` or there are no fragments).

## Behaviour after fix (verified)

- `track_cross_request` issues exactly ONE `transaction=True` pipeline containing the SADD + EXPIRE; the
  set key ALWAYS carries a TTL (never orphaned).
- No-redis path → 0.0 (safe); benign text with no fragments → no Redis writes at all.

## Verify

```
cd gateway
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_leakage_detector_ttl_atomic.py -q   # 3 passed
.venv/bin/python -m pytest ai_mesh_gateway/tests/test_e11_retrieved_redaction.py -q       # 9 passed (existing)
.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                        # 1421 passed, 0 failed
```
Broker regression: `services/mcp-broker && .venv/bin/python -m pytest tests -q -k "not websocket"` → 108 passed.

## Residual / follow-ups
- `circuit_breaker.py` uses `pipeline(transaction=False)` for INCR+EXPIRE — batched in one network write
  (both commands sent together), so the orphan window is far smaller than separate awaited calls; lower
  priority, but a future pass could make those `transaction=True` for full MULTI/EXEC atomicity.
- Other MCP Redis keys (`mcp:oauth:token:*`, `mcp:toolcalls:*`, `ratelimit:*`) already carry TTLs
  (verified in CHG-0023/0062).
