"""CHG-0084: LeakageDetector.track_cross_request did per-fragment `sadd` then a SEPARATE
`await expire` — two round-trips. A coroutine cancellation (client disconnect under load)
or a transient error between the last sadd and the expire ORPHANED the
`leakage:cross:{key}` Redis SET with NO TTL → unbounded Redis memory under soak (same
class as CHG-0062's non-atomic INCR+EXPIRE). It now uses one MULTI/EXEC (transaction=True)
that sets the members + window TTL atomically, so the key can never be left without a TTL.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))
_SHARED = _GW.parents[1] / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

import leakage_detector as LD  # noqa: E402


class _FakePipe:
    def __init__(self, store, transaction):
        self.store = store
        self.transaction = transaction
        self.ops: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def sadd(self, key, *members):
        self.ops.append(("sadd", key))
        self.store.setdefault(key, set()).update(members)

    def expire(self, key, ttl):
        self.ops.append(("expire", key))
        self.store[key + "__ttl"] = ttl

    async def execute(self):
        return [len(self.ops)]


class _FakeRedis:
    def __init__(self):
        self.store: dict = {}
        self.pipes: list[_FakePipe] = []

    def pipeline(self, transaction=False):
        p = _FakePipe(self.store, transaction)
        self.pipes.append(p)
        return p

    async def scard(self, key):
        return len(self.store.get(key, set()))


def _make_detector(redis):
    cls = next(
        getattr(LD, n) for n in dir(LD)
        if inspect.isclass(getattr(LD, n)) and hasattr(getattr(LD, n), "track_cross_request")
    )
    return cls(redis_client=redis, cross_request_window=300, cross_request_threshold=5)


@pytest.mark.asyncio
async def test_sadd_and_expire_are_one_atomic_transaction():
    r = _FakeRedis()
    det = _make_detector(r)
    await det.track_cross_request("email alice@corp.example and ssn 123-45-6789", "keyA")

    # exactly one pipeline, and it is a MULTI/EXEC transaction
    assert len(r.pipes) == 1
    assert r.pipes[0].transaction is True
    op_names = [o[0] for o in r.pipes[0].ops]
    assert "sadd" in op_names and "expire" in op_names, op_names
    # the set key ALWAYS carries a TTL (never orphaned)
    assert r.store.get("leakage:cross:keyA__ttl") == 300


@pytest.mark.asyncio
async def test_no_redis_is_safe():
    det = _make_detector(None)
    assert await det.track_cross_request("some text", "k") == 0.0


@pytest.mark.asyncio
async def test_no_fragments_no_pipeline():
    r = _FakeRedis()
    det = _make_detector(r)
    # benign text with no sensitive fragments → no Redis writes at all
    await det.track_cross_request("the weather is nice today", "keyB")
    assert len(r.pipes) == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
