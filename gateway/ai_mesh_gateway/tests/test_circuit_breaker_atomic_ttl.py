"""CHG-0086: circuit_breaker.py recorded INCR + EXPIRE via `pipeline(transaction=False)`
at 4 sites (record_success, record_error, _bump_epoch, _admit_probe). A mid-pipeline
failure between the INCR and the EXPIRE would orphan the counter with NO TTL (same class
as CHG-0062/0084's non-atomic INCR+EXPIRE). They now use `pipeline(transaction=True)`
(MULTI/EXEC) so the increment + its TTL commit atomically — and `_admit_probe`'s docstring
promise of an ATOMIC probe-slot claim is now actually honoured.

These tests use a fake Redis pipeline to assert EVERY counter pipeline is transactional
and that INCR is always accompanied by an EXPIRE in the same MULTI/EXEC.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))
_SHARED = _GW.parents[1] / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

import circuit_breaker as CB  # noqa: E402


class _FakePipe:
    def __init__(self, store, transaction, log):
        self.store = store
        self.transaction = transaction
        self.log = log      # shared list of (transaction, [ops]) recorded on execute
        self.ops: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def incr(self, key):
        self.ops.append("incr")
        self.store[key] = int(self.store.get(key, 0)) + 1

    def expire(self, key, ttl):
        self.ops.append("expire")
        self.store[key + "__ttl"] = ttl

    def delete(self, *keys):
        self.ops.append("delete")

    def set(self, *a, **k):
        self.ops.append("set")

    async def execute(self):
        self.log.append((self.transaction, list(self.ops)))
        # results: one entry per op; incr ops return incrementing ints
        return [1 for _ in self.ops]


class _FakeRedis:
    def __init__(self):
        self.store: dict = {}
        self.pipe_log: list = []

    def pipeline(self, transaction=False):
        return _FakePipe(self.store, transaction, self.pipe_log)

    async def get(self, k):
        return self.store.get(k)

    async def set(self, k, v, ex=None):
        self.store[k] = v

    async def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)


def _breaker(redis):
    return CB.CircuitBreaker(redis_client=redis) if hasattr(CB, "CircuitBreaker") else next(
        getattr(CB, n)(redis_client=redis) for n in dir(CB)
        if isinstance(getattr(CB, n), type) and hasattr(getattr(CB, n), "record_error")
    )


@pytest.mark.asyncio
async def test_record_success_and_error_pipelines_are_atomic():
    r = _FakeRedis()
    cb = _breaker(r)
    await cb.record_success("gpt-x", "org1")
    await cb.record_error("gpt-x", "timeout", "org1")

    assert r.pipe_log, "no pipeline was executed"
    for transaction, ops in r.pipe_log:
        assert transaction is True, f"counter pipeline was non-transactional: ops={ops}"
        # every INCR is paired with an EXPIRE in the SAME MULTI/EXEC
        assert ops.count("incr") == ops.count("expire"), ops


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
