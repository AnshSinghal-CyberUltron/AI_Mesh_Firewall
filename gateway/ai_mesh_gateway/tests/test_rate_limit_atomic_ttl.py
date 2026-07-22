"""CHG-0062: per-org burst/RPM counters must set their TTL atomically.

``enforce_org_burst_rpm`` did ``INCR`` then a SEPARATE ``if current == 1: EXPIRE``.
If the request coroutine was cancelled (client disconnect — routine under load) or
crashed between the two commands, the key was created with NO TTL and orphaned
forever → an unbounded Redis memory leak under soak/stress. The fix runs INCR +
EXPIRE NX in one MULTI/EXEC transaction (self-healing: EXPIRE NX on every request
(re)sets the TTL whenever the key lacks one), so no counter key is ever left
without a TTL.

NOTE: these tests pin ``time.time`` to a fixed value so all calls share one
burst/minute bucket. That also freezes fakeredis's internal expiry clock, so every
Redis assertion is made INSIDE the patched-clock context (checking a TTL under the
real clock would see the frozen-TTL key as already expired).
"""
import sys
from pathlib import Path
from types import SimpleNamespace

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))

import fakeredis.aioredis as fakeredis_aio  # noqa: E402
import pytest  # noqa: E402
from unittest.mock import patch  # noqa: E402

import rate_limit_enforcement as rle  # noqa: E402

_FIXED_TS = 1_780_000_000  # pinned so all calls share one burst/minute bucket
_BURST_KEY = f"ratelimit:demo:burst:{_FIXED_TS}"
_RPM_KEY = f"ratelimit:demo:{_FIXED_TS // 60}"


def _cfg(**over):
    return SimpleNamespace(get_config=lambda _slug: dict(over))


def _call(redis_client, *, burst_limit=150, rpm_limit=1000):
    return rle.enforce_org_burst_rpm(
        SimpleNamespace(org_slug="demo", prefix="k1"),
        redis_client=redis_client,
        config_sync=_cfg(burst_limit=burst_limit, requests_per_minute=rpm_limit),
        gateway_config={},
        metrics={},
        emit_telemetry=lambda **_k: None,
        event_type="mcp_blocked",
    )


@pytest.mark.asyncio
async def test_burst_and_rpm_keys_always_get_a_ttl():
    r = fakeredis_aio.FakeRedis()
    with patch.object(rle.time, "time", return_value=_FIXED_TS):
        resp = await _call(r)
        assert resp is None                       # under limit
        assert await r.ttl(_BURST_KEY) > 0        # TTL set atomically with the INCR
        assert await r.ttl(_RPM_KEY) > 0


@pytest.mark.asyncio
async def test_ttl_self_heals_an_orphaned_no_ttl_key():
    r = fakeredis_aio.FakeRedis()
    with patch.object(rle.time, "time", return_value=_FIXED_TS):
        # simulate the OLD bug's aftermath: a counter key with NO TTL
        await r.set(_BURST_KEY, 5)                # no EXPIRE -> orphaned forever
        assert await r.ttl(_BURST_KEY) == -1
        await _call(r)
        # EXPIRE NX on this request repaired the missing TTL
        assert await r.ttl(_BURST_KEY) > 0


@pytest.mark.asyncio
async def test_ttl_not_reset_on_subsequent_increments():
    # EXPIRE NX must NOT slide the window forward on later hits in the same bucket
    r = fakeredis_aio.FakeRedis()
    with patch.object(rle.time, "time", return_value=_FIXED_TS):
        await _call(r)
        await r.expire(_BURST_KEY, 1)            # pretend the window is nearly up
        await _call(r)                           # nx=True -> must NOT bump it back to 2
        assert await r.ttl(_BURST_KEY) <= 1
        assert int(await r.get(_BURST_KEY)) == 2  # but the count still incremented


@pytest.mark.asyncio
async def test_burst_limit_still_enforced():
    r = fakeredis_aio.FakeRedis()
    with patch.object(rle.time, "time", return_value=_FIXED_TS):
        r1 = await _call(r, burst_limit=2)       # count 1
        r2 = await _call(r, burst_limit=2)       # count 2
        r3 = await _call(r, burst_limit=2)       # count 3 > 2 -> block
    assert r1 is None and r2 is None
    assert r3 is not None and r3.status_code == 429


@pytest.mark.asyncio
async def test_fail_open_on_redis_error():
    class _Boom:
        def pipeline(self, *a, **k):
            raise RuntimeError("redis down")

    with patch.object(rle.time, "time", return_value=_FIXED_TS):
        resp = await _call(_Boom(), burst_limit=1)
    assert resp is None  # fail-open: a Redis blip must not mass-block traffic


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
