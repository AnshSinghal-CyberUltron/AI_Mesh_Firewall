"""ResourceContract — the only module allowed to hold capacity-position literals."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

from gateway_v2.runtime.cgroup import DetectHooks, detect_with_hooks
from gateway_v2.runtime.errors import CapacityUnavailable, CapacityUnset
from gateway_v2.runtime.kinds import CapacityHint, HardwareSignals, PoolKind

# Deployment-declared defaults. Logged as defaults when env is unset.
_DEFAULT_TARGET_P99_MS = 20.0
_DEFAULT_UTILIZATION_CAP = 0.75
_DEFAULT_PER_WORKER_RSS = 400 * 1024 * 1024
_MS_PER_S = 1000.0


@dataclass(frozen=True, slots=True)
class ResourceContract:
    cpu_quota: float
    memory_limit: int
    fd_limit: int
    guard_capacity: CapacityHint | None
    target_p99_ms: float
    utilization_cap: float
    per_worker_rss: int
    worker_override: int | None
    cpu_source: str
    mem_source: str
    fd_source: str

    def detected_workers(self) -> int:
        cpu_w = math.floor(self.cpu_quota * self.utilization_cap)
        ram_w = math.floor(self.memory_limit * self.utilization_cap / self.per_worker_rss)
        n = min(cpu_w, ram_w)
        if n < 1:
            raise CapacityUnavailable(
                f"derived workers={n} (cpu={cpu_w} ram={ram_w}); refuse to start",
            )
        return n

    def workers(self) -> int:
        if self.worker_override is not None:
            if self.worker_override < 1:
                raise CapacityUnavailable("WEB_CONCURRENCY override below one")
            return self.worker_override
        return self.detected_workers()

    def binding_signal(self) -> str:
        cpu_w = math.floor(self.cpu_quota * self.utilization_cap)
        ram_w = math.floor(self.memory_limit * self.utilization_cap / self.per_worker_rss)
        if ram_w < cpu_w:
            return "ram"
        return "cpu"

    def offered_service_rate(self) -> float:
        return self.workers() / (self.target_p99_ms / _MS_PER_S)

    def queue_depth(self, service_rate: float) -> int:
        if service_rate <= 0:
            raise CapacityUnavailable("service_rate must be positive")
        slo = math.ceil(
            service_rate * (self.target_p99_ms / _MS_PER_S) / self.utilization_cap,
        )
        ram_slots = math.floor(self.memory_limit * self.utilization_cap / self.per_worker_rss)
        n = min(slo, ram_slots)
        if n < 1:
            raise CapacityUnavailable("queue_depth below minimum to serve")
        return n

    def connection_budget(self) -> int:
        usable = self.fd_limit - self.workers()
        n = math.floor(max(usable, 0) * self.utilization_cap)
        return n

    def pool_size(self, kind: PoolKind) -> int:
        if kind is PoolKind.GUARD:
            return self._guard_pool()
        w = self.workers()
        if kind is PoolKind.SCANNER:
            n = math.floor(self.cpu_quota * self.utilization_cap)
        elif kind is PoolKind.PROVIDER:
            n = w
        elif kind is PoolKind.VAULT:
            n = w
        elif kind is PoolKind.REDIS:
            n = self.connection_budget()
        else:
            raise CapacityUnavailable(f"unknown pool kind {kind}")
        if n < 1:
            raise CapacityUnavailable(f"{kind} pool below minimum to serve")
        return n

    def _guard_pool(self) -> int:
        if self.guard_capacity is None:
            raise CapacityUnset("guard-derived bound unset")
        n = math.floor(
            self.guard_capacity.tokens_per_second * (self.target_p99_ms / _MS_PER_S),
        )
        if n < 1:
            raise CapacityUnavailable("guard pool below minimum to serve")
        return n

    def stream_buffer_bytes(self, active_streams: int) -> int:
        streams = active_streams if active_streams > 0 else 1
        n = math.floor(self.memory_limit * self.utilization_cap / streams)
        if n < 1:
            raise CapacityUnavailable("stream buffer below minimum to serve")
        return n


def _env_map(env: dict[str, str] | None) -> dict[str, str]:
    if env is not None:
        return env
    return dict(os.environ)


def _parse_override(raw: str | None) -> int | None:
    if raw is None or not raw.strip():
        return None
    return int(raw.strip())


def from_signals(
    signals: HardwareSignals,
    *,
    target_p99_ms: float,
    utilization_cap: float,
    per_worker_rss: int,
    worker_override: int | None = None,
    guard_capacity: CapacityHint | None = None,
) -> ResourceContract:
    if target_p99_ms <= 0 or utilization_cap <= 0 or per_worker_rss < 1:
        raise CapacityUnavailable("deployment SLO / rss invalid")
    contract = ResourceContract(
        cpu_quota=signals.cpu_quota,
        memory_limit=signals.memory_limit,
        fd_limit=signals.fd_limit,
        guard_capacity=guard_capacity,
        target_p99_ms=target_p99_ms,
        utilization_cap=utilization_cap,
        per_worker_rss=per_worker_rss,
        worker_override=worker_override,
        cpu_source=signals.cpu_source,
        mem_source=signals.mem_source,
        fd_source=signals.fd_source,
    )
    contract.detected_workers()
    if worker_override is not None:
        contract.workers()
    return contract


def load_contract(
    *,
    env: dict[str, str] | None = None,
    guard_capacity: CapacityHint | None = None,
    hooks: DetectHooks | None = None,
) -> tuple[ResourceContract, tuple[str, ...]]:
    src = _env_map(env)
    p99_raw = src.get("AMF_TARGET_P99_MS")
    util_raw = src.get("AMF_UTILIZATION_CAP")
    rss_raw = src.get("AMF_PER_WORKER_RSS_MB")
    p99 = float(p99_raw) if p99_raw else _DEFAULT_TARGET_P99_MS
    util = float(util_raw) if util_raw else _DEFAULT_UTILIZATION_CAP
    rss = int(float(rss_raw) * 1024 * 1024) if rss_raw else _DEFAULT_PER_WORKER_RSS
    override = _parse_override(src.get("WEB_CONCURRENCY") or src.get("AMF_WEB_CONCURRENCY"))
    signals = detect_with_hooks(env=src, hooks=hooks)
    contract = from_signals(
        signals,
        target_p99_ms=p99,
        utilization_cap=util,
        per_worker_rss=rss,
        worker_override=override,
        guard_capacity=guard_capacity,
    )
    logs = _startup_logs(contract, p99_raw, util_raw, rss_raw)
    return contract, logs


def _startup_logs(
    contract: ResourceContract,
    p99_raw: str | None,
    util_raw: str | None,
    rss_raw: str | None,
) -> tuple[str, ...]:
    lines = [
        (
            f"cpu_quota={contract.cpu_quota} source={contract.cpu_source} "
            f"memory_limit={contract.memory_limit} source={contract.mem_source} "
            f"fd_limit={contract.fd_limit} source={contract.fd_source}"
        ),
        (
            f"binding={contract.binding_signal()} workers={contract.workers()} "
            f"detected_workers={contract.detected_workers()}"
        ),
        (
            f"target_p99_ms={contract.target_p99_ms} "
            f"utilization_cap={contract.utilization_cap} "
            f"per_worker_rss={contract.per_worker_rss} "
            f"declared={'env' if (p99_raw or util_raw or rss_raw) else 'defaults'}"
        ),
    ]
    if contract.worker_override is not None:
        lines.append(
            f"WEB_CONCURRENCY override={contract.worker_override} "
            f"detected={contract.detected_workers()} (deviation)",
        )
    return tuple(lines)


def snapshot(contract: ResourceContract, logs: tuple[str, ...]) -> dict[str, object]:
    pools: dict[str, int | None] = {}
    for kind in PoolKind:
        if kind is PoolKind.GUARD:
            try:
                pools[kind.value] = contract.pool_size(kind)
            except CapacityUnset:
                pools[kind.value] = None
            continue
        pools[kind.value] = contract.pool_size(kind)
    rate = contract.offered_service_rate()
    return {
        "cpu_quota": contract.cpu_quota,
        "cpu_source": contract.cpu_source,
        "memory_limit": contract.memory_limit,
        "mem_source": contract.mem_source,
        "fd_limit": contract.fd_limit,
        "fd_source": contract.fd_source,
        "workers": contract.workers(),
        "detected_workers": contract.detected_workers(),
        "worker_override": contract.worker_override,
        "binding": contract.binding_signal(),
        "queue_depth": contract.queue_depth(rate),
        "connection_budget": contract.connection_budget(),
        "stream_buffer_bytes": contract.stream_buffer_bytes(1),
        "pools": pools,
        "logs": list(logs),
    }
