"""Dummy pools that resize on SIGHUP without dropping in-flight leases."""

from __future__ import annotations

import os
import threading

from gateway_v2.runtime.cgroup import DetectHooks
from gateway_v2.runtime.errors import CapacityUnset
from gateway_v2.runtime.kinds import PoolKind
from gateway_v2.runtime.resources import ResourceContract, load_contract, snapshot


class DummyPool:
    """Counting pool. Shrink never cancels a held lease."""

    def __init__(self, size: int) -> None:
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self.size = size
        self.held = 0
        self.completed = 0
        self.dropped = 0

    def acquire(self) -> None:
        with self._cv:
            while self.held >= self.size:
                self._cv.wait()
            self.held += 1

    def release(self) -> None:
        with self._cv:
            if self.held < 1:
                return
            self.held -= 1
            self.completed += 1
            self._cv.notify_all()

    def resize(self, size: int) -> None:
        with self._cv:
            self.size = size
            self._cv.notify_all()


class GatewayRuntime:
    def __init__(
        self,
        contract: ResourceContract,
        logs: tuple[str, ...],
        *,
        hooks: DetectHooks | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.contract = contract
        self.logs = logs
        self.hooks = hooks
        self.env = env
        self.reload_count = 0
        self.pid = os.getpid()
        self.pools = self._build_pools(contract)

    def _build_pools(self, contract: ResourceContract) -> dict[PoolKind, DummyPool]:
        built: dict[PoolKind, DummyPool] = {}
        for kind in PoolKind:
            if kind is PoolKind.GUARD:
                continue
            built[kind] = DummyPool(contract.pool_size(kind))
        return built

    def apply(self, contract: ResourceContract, logs: tuple[str, ...]) -> None:
        self.contract = contract
        self.logs = logs
        self.reload_count += 1
        for kind, pool in self.pools.items():
            try:
                pool.resize(contract.pool_size(kind))
            except CapacityUnset:
                continue

    def reload(self) -> None:
        contract, logs = load_contract(env=self.env, hooks=self.hooks)
        self.apply(contract, logs)

    def in_flight(self) -> int:
        return sum(p.held for p in self.pools.values())

    def dropped(self) -> int:
        return sum(p.dropped for p in self.pools.values())

    def as_dict(self) -> dict[str, object]:
        body = snapshot(self.contract, self.logs)
        body["pid"] = self.pid
        body["reload_count"] = self.reload_count
        body["in_flight"] = self.in_flight()
        body["dropped"] = self.dropped()
        body["pool_sizes"] = {k.value: p.size for k, p in self.pools.items()}
        return body
