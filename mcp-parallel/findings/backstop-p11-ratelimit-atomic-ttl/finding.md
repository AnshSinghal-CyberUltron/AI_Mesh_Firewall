# BACKSTOP hardening — per-org burst/RPM counters set TTL atomically (CHG-0062)

- **Item:** G3 item 9 (gateway rate-limit) + item 11 (Redis correctness) / stress soak-safety.
- **Change-id:** CHG-0062 (2026-07-02)
- **Type:** Redis correctness / resource-leak (non-atomic INCR+EXPIRE), on the MCP path.

## Gap
`rate_limit_enforcement.py::enforce_org_burst_rpm` (the per-org burst req/s + RPM req/min limiter)
did, for BOTH counters:
```
current = await redis.incr(key)
if current == 1:
    await redis.expire(key, ttl)     # SEPARATE command, only on the first hit
```
INCR and EXPIRE are two separate awaited commands. If the request coroutine is CANCELLED (a client
disconnect — routine under load / streaming) or the process crashes BETWEEN them, the key is created
with NO TTL and orphaned forever. Because the TTL was set ONLY when `current == 1`, a cancelled FIRST
request means the key never gets a TTL at all — it persists permanently (the buckets are time-keyed
`burst:{sec}` / `{minute}`, so each orphan is dead weight that is never queried again but never
expires). Under the mandate's stress scenario (5k–10k concurrent tool calls, chaos, disconnects) this
is an unbounded Redis memory leak.

This is on the MCP path: `mcp_proxy._mcp_org_rate_limit_raw` (called on ALL THREE MCP entry points —
org_mcp_jsonrpc, org_mcp_tool_call, ext_mcp_proxy; CHG-0031/0032) → `_enforce_org_burst_rpm` →
`enforce_org_burst_rpm`.

INCONSISTENT with the sibling limiters, which were already atomic: the tool-call cap
(`mcp_proxy.py ~1066`, CHG-0048) uses `pipeline(transaction=True)` + `expire(nx=True)`, and
`rate_limiter.py` uses a Lua `eval` (INCR+EXPIRE in one script). The burst/RPM path was the omission.

## Fix — `gateway/ai_mesh_gateway/rate_limit_enforcement.py`
Both counters now run INCR + EXPIRE NX in ONE MULTI/EXEC transaction (matching the CHG-0048 idiom):
```
async with redis_client.pipeline(transaction=True) as pipe:
    pipe.incr(key)
    pipe.expire(key, ttl, nx=True)
    current = (await pipe.execute())[0]
```
- **Atomic:** no window between INCR and EXPIRE for a cancellation/crash to orphan the key.
- **Self-healing:** `EXPIRE NX` runs on EVERY request (not just `current == 1`), so it (re)sets the TTL
  whenever the key lacks one — a previously-orphaned no-TTL key is repaired on its next increment.
  NX means it does NOT slide the window forward on later hits in the same bucket (the count still
  increments; the TTL is only set when absent).
- Fail-OPEN preserved: the surrounding `try/except` still allows the request on any Redis error.

## Verification
- `cd gateway && ./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_rate_limit_atomic_ttl.py -q`
  → 5 passed: burst+RPM keys always get a TTL; a manually-orphaned no-TTL key is self-healed on the
  next call; EXPIRE NX does not reset the TTL on later increments (count still rises); the burst limit
  is still enforced (3rd call > limit 2 → 429); fail-open on a Redis error. (Tests pin `time.time` so
  all calls share one bucket, and assert INSIDE the frozen-clock context since freezing time also
  freezes fakeredis's expiry clock.)
- Existing `test_mcp_rate_limit.py` → 14 passed (no regression). Full sweep → 1228 passed, 0 failed.

## Sibling (documented follow-up, NOT changed here — keep this commit scoped to one item)
`leakage_detector.py:116-120` has the same class: a `sadd` loop then a SEPARATE `expire(redis_key,
window)`. Milder — the EXPIRE is unconditional (refreshes the sliding window every call), so a key is
only orphaned if cancelled between the last sadd and the expire (and would be re-TTL'd on the next
call anyway). Worth converting to an atomic pipeline in a future iteration for consistency.
