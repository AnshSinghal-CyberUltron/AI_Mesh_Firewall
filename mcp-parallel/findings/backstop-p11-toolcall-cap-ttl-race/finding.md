# BACKSTOP finding — MCP tool-call cap counter: non-atomic INCR+EXPIRE TTL race (CHG-0048)

- **Item:** G3 item 11 ("PostgreSQL + Redis schemas/usage/restart-safety") + item 9 (rate-limit).
- **Change-id:** CHG-0048 (2026-07-02)
- **Severity:** MEDIUM — Redis correctness / availability bug. A transient failure at
  one moment could PERMANENTLY cap a key's tool calls until manual Redis surgery.

## Title
The per-key tool-call cap counter set its 60s window TTL only on the FIRST increment
(`if count == 1: EXPIRE`), so a crash or dropped EXPIRE at that moment left
`mcp:toolcalls:<key>` with no TTL forever — the counter never reset and the key was
permanently rate-limited once it crossed `mcp_max_tool_calls`.

## Reproduction (code trace)
`_incr_tool_call_count` (`gateway/ai_mesh_gateway/mcp_proxy.py`), before:
```
count = await client.incr(rk)          # count -> 1
if count == 1:
    await client.expire(rk, 60)        # (A) if this is dropped/crashes here...
return int(count)
```
1. INCR makes count = 1.
2. If the process crashes, the connection drops, or EXPIRE (A) otherwise fails, the
   key now has NO TTL (`TTL` = -1).
3. Every subsequent call increments count to 2, 3, ... which is never `== 1`, so the
   EXPIRE branch never runs again — the key keeps NO TTL indefinitely.
4. The counter grows without bound and never resets. Once `count > mcp_max_tool_calls`
   the key is blocked (429) on EVERY tool call, forever, until someone manually
   deletes the Redis key.

## Expected
The window TTL is set together with the increment (atomically), and a missing TTL
self-heals; the fixed 60s window is preserved.

## Actual (before fix)
TTL set only on the count==1 path; a hiccup there = permanent TTL-less key = permanent
cap.

## Root cause
Non-atomic read-modify-write: two separate Redis round-trips (INCR then a conditional
EXPIRE) with the EXPIRE gated on a value that only occurs once.

## Fix
INCR + set-TTL-if-missing run ATOMICALLY in a MULTI/EXEC pipeline, with EXPIRE on
EVERY increment using `NX` (Redis 7+, deployed image is `redis:7.4-alpine`):
```
async with client.pipeline(transaction=True) as pipe:
    pipe.incr(rk)
    pipe.expire(rk, _MCP_TOOL_CALL_WINDOW_SEC, nx=True)
    res = await pipe.execute()
return int(res[0])
```
`NX` sets the TTL only when absent — so the FIXED window is preserved (an existing TTL
is never extended), and a lost TTL is HEALED on the very next call. Fail-open on any
Redis error is unchanged (the cap is a soft resource limit). Consistent with the
codebase's existing atomic-Redis idiom (`rate_limiter.py` uses `INCR`/`TTL<0`→`EXPIRE`).

## Verification
- New `gateway/ai_mesh_gateway/tests/test_mcp_tool_call_cap_ttl.py` uses a REAL (fake)
  Redis (`fakeredis.aioredis`) so the atomic behaviour is exercised, not mocked:
  first call sets a TTL; a second call does NOT extend it (fixed window); a key whose
  TTL was dropped (`persist`) is HEALED on the next increment; Redis errors fail open
  (→ 0); no key → 0. → **5 passed**.
- Existing cap tests (`test_e12_mcp_security.py`, `test_mcp_bare_proxy_scan.py`, which
  mock `_incr_tool_call_count` wholesale) → **37 passed** (unaffected).
- Broad sweep `ai_mesh_gateway/tests` → **1105 passed, 0 failed**.

## Residual
The per-ORG TPM/burst limiter (`main._enforce_org_tpm_rate_limit`) is shared core
infra (chat/embeddings/MCP), out of this MCP-specific change's scope; it already uses
Lua-atomic counters (`rate_limiter.py`). Item 11's live kill-Redis-mid-load drill
remains host-blocked (item 18 chaos).
