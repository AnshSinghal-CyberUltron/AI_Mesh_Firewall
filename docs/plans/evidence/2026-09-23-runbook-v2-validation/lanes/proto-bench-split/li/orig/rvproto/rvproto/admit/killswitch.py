"""Kill-switch RAM snapshot refreshed in the background; fail-closed past the ceiling.

One pipelined background round trip per refresh carries the global switch, the auth epoch
(identity revocation), and the per-org / per-model switch hashes, so steady-state requests
perform zero shared-state reads for any of them. A flip takes effect on every worker within
one refresh period; past the staleness ceiling the snapshot is UNAVAILABLE (fail closed).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Callable

import redis.asyncio as aioredis

from rvproto.runtime.metrics import Registry
from rvproto.runtime.store import (
    K_AUTH_EPOCH,
    K_KILLSWITCH,
    K_KILLSWITCH_MODELS,
    K_KILLSWITCH_ORGS,
    StoreError,
)


def _on(v: bytes | None) -> bool:
    return v is not None and v.strip() == b"1"


class KillSwitch:
    def __init__(self, r: aioredis.Redis, metrics: Registry, *, refresh_ms: float,
                 stale_ms: float, on_epoch: Callable[[int], None]) -> None:
        self.r = r
        self.metrics = metrics
        self.refresh_s = refresh_ms / 1000.0
        self.stale_ns = int(stale_ms * 1e6)
        self.on_epoch = on_epoch
        self.engaged = False
        self.orgs: frozenset[str] = frozenset()
        self.models: frozenset[str] = frozenset()
        self.last_ok_ns = 0

    def age_s(self) -> float:
        return (time.perf_counter_ns() - self.last_ok_ns) / 1e9

    def state(self) -> str:
        """'ok' | 'engaged' | 'stale' — evaluated from RAM only."""
        if time.perf_counter_ns() - self.last_ok_ns > self.stale_ns:
            return "stale"
        return "engaged" if self.engaged else "ok"

    def org_killed(self, org: str) -> bool:
        return org in self.orgs

    def model_killed(self, model: str) -> bool:
        return model in self.models

    async def refresh_once(self) -> None:
        pipe = self.r.pipeline(transaction=False)
        pipe.mget(K_KILLSWITCH, K_AUTH_EPOCH)
        pipe.hgetall(K_KILLSWITCH_ORGS)
        pipe.hgetall(K_KILLSWITCH_MODELS)
        (ks, epoch), orgs, models = await pipe.execute()
        self.engaged = _on(ks)
        self.orgs = frozenset(k.decode() for k, v in orgs.items() if _on(v))
        self.models = frozenset(k.decode() for k, v in models.items() if _on(v))
        self.on_epoch(int(epoch) if epoch is not None else 0)
        self.last_ok_ns = time.perf_counter_ns()

    async def run(self) -> None:
        while True:
            try:
                await self.refresh_once()
                self.metrics.inc("background_round_trips")
            except StoreError as exc:
                self.metrics.inc("killswitch_refresh_errors")
                sys.stderr.write(json.dumps({"event": "killswitch_refresh_error", "t": time.time(),
                                             "error": repr(exc)}) + "\n")
            await asyncio.sleep(self.refresh_s)
