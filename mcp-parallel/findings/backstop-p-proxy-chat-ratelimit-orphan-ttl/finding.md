# CHG-0132 — proxy_chat rate-limit leaked no-TTL Redis keys (CHG-0062 fix missed the chat hot path)

**Change-id:** CHG-0132
**Date:** 2026-07-03
**Severity:** MEDIUM (Redis correctness + resource exhaustion under soak/stress). The chat-completions hot path (`proxy_chat`) orphaned a no-TTL burst counter key on every request coroutine cancellation — and the burst key is a new key every second, so orphans accumulated ~1/second → unbounded Redis memory growth. Directly relevant to soak/resource-bomb items (16/17): a Redis OOM eventually breaks rate limiting for all orgs.
**Area:** HARDEN THE ARCHITECTURE — PostgreSQL + Redis correctness; gateway rate-limit correctness (item 9/11).
**Files:** `gateway/ai_mesh_gateway/main.py` (`proxy_chat` inline burst + RPM counters); `gateway/ai_mesh_gateway/tests/test_proxy_chat_ratelimit_atomic_ttl.py` (new, +2 regression guards).
**Whose work it touches:** the owning-session gateway chat hot path; parity with CHG-0062 (rate_limit_enforcement.py).

## Root cause

CHG-0062 fixed an orphaned-no-TTL-key leak in the per-org burst/RPM counters by running `INCR` + `EXPIRE NX`
atomically in one MULTI/EXEC transaction — but only in `rate_limit_enforcement.py` (and the
`_enforce_org_burst_rpm` shim the embeddings/RAG endpoints call). The **chat hot path** (`proxy_chat`) kept an
**inline copy** of the counter logic that still used the old non-atomic idiom:

```python
current_burst = await REDIS_CLIENT.incr(burst_key)
if current_burst == 1:
    await REDIS_CLIENT.expire(burst_key, 2)     # separate command
```

If the request coroutine is **cancelled** (client disconnect — routine under load) or crashes **between** the
`INCR` and the `EXPIRE`, the key is created with **no TTL and orphaned forever**. `burst_key =
ratelimit:{org}:burst:{second}` is a **new key every second**, so under soak/stress a steady stream of orphaned
no-TTL keys accumulates → unbounded Redis memory growth (eventual OOM → rate limiting fails for everyone). Ironic
detail: `_enforce_org_burst_rpm`'s docstring calls the chat path the *reference* implementation, yet the chat
path was the one still carrying the bug the shim had already fixed.

## The fix (CHG-0132)

Both the burst and RPM counters in `proxy_chat` now use the atomic pattern, identical to CHG-0062:

```python
async with REDIS_CLIENT.pipeline(transaction=True) as pipe:
    pipe.incr(burst_key)
    pipe.expire(burst_key, 2, nx=True)          # (120 for RPM)
    current_burst = (await pipe.execute())[0]
```

`EXPIRE NX` on every request self-heals a key that somehow lacks a TTL, and the TTL can never be dropped by a
cancellation between two awaits (there is only one `await` — the pipeline execute). Behavior is otherwise
unchanged (same keys, limits, telemetry, fail-open). `REDIS_CLIENT` is a `redis.asyncio` client (same type used
by `rate_limit_enforcement.py`), so `pipeline(transaction=True)` + `expire(nx=True)` are supported.

## Verification

```
cd gateway
./.venv/bin/python -m pytest ai_mesh_gateway/tests/test_proxy_chat_ratelimit_atomic_ttl.py ai_mesh_gateway/tests/test_rate_limit_atomic_ttl.py -q   # 7 passed
./.venv/bin/python -m pytest ai_mesh_gateway/tests -q                                                                                             # 1824 passed, 0 failed
```

New `test_proxy_chat_ratelimit_atomic_ttl.py` guards the source: the non-atomic `if current_burst == 1:` /
`if current_rpm == 1:` idiom must not reappear in main.py, and the atomic `expire(..., nx=True)` must be present
for both counters. The runtime correctness of the identical atomic pattern (TTL always set, self-healing on an
orphaned key, EXPIRE NX doesn't slide the window, limit still enforced, fail-open on Redis error) is proven by
`test_rate_limit_atomic_ttl.py` (5 passed) for `rate_limit_enforcement.py`.

## Scope / honesty note

Behavior-preserving fix (only the TTL atomicity changes). The inline block is a source-level duplicate of the
tested `_enforce_org_burst_rpm` shim; the cleanest ROOT-CAUSE fix is to have `proxy_chat` call the shim (dedup),
but that would change the inline telemetry emission and is deferred as a follow-up to keep this change minimal on
the hot path. The regression guard is source-level because the inline logic is not an independently callable
function. Does not change the host-blocked live-stress status (items 14–19). Partial coverage is not completion.
