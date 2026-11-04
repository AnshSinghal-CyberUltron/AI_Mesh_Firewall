"""Start-up snapshot of a `ResourceContract` (split out of `runtime/resources.py`, GW28 D-15).

Literal-free: it only reads accessors, so it does not need the one module the capacity-literal gate
exempts. Moved here to keep `resources.py` under the module size cap.
"""

from __future__ import annotations

from gateway_v2.runtime.errors import CapacityUnset
from gateway_v2.runtime.kinds import PoolKind
from gateway_v2.runtime.resources import ResourceContract

__all__ = ("snapshot",)


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
        "inter_chunk_timeout_s": contract.inter_chunk_timeout_s(),
        "idle_timeout_s": contract.idle_timeout_s(),
        "write_timeout_s": contract.write_timeout_s(),
        "max_stream_duration_s": contract.max_stream_duration_s(),
        "max_cut_latency_s": contract.max_cut_latency_s(),
        "max_snapshot_age_s": contract.max_snapshot_age_s(),
        "cancellation_bound_s": contract.cancellation_bound_s(),
        "upstream_first_event_timeout_s": contract.upstream_first_event_timeout_s(),
        "upstream_gap_timeout_s": contract.upstream_gap_timeout_s(),
        "upstream_total_timeout_s": contract.upstream_total_timeout_s(),
        "routing_pre_content_budget_s": contract.routing_pre_content_budget_s(
            contract.routing_max_attempts()
        ),
        **contract.incident_snapshot(),
        "pools": pools,
        "logs": list(logs),
    }
