"""Every capacity bound in rvproto, derived from the REAL GW03 ResourceContract.

gateway_v2.runtime is imported read-only from the repo (or a vendored copy on VMs).
No numeric literal appears in a capacity position anywhere in rvproto; this module
maps each bound onto a contract method and documents the mapping.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from gateway_v2.runtime.kinds import CapacityHint, PoolKind
from gateway_v2.runtime.resources import ResourceContract, load_contract, snapshot

from rvproto.runtime.config import Settings

__all__ = ("Bounds", "CapacityHint", "ResourceContract", "derive", "load", "with_guard")


@dataclass(frozen=True, slots=True)
class Bounds:
    workers: int  # contract.workers()
    provider_connections: int  # contract.connection_budget(): per-process fd-derived
    redis_connections: int  # contract.pool_size(REDIS)
    listen_backlog: int  # contract.connection_budget()
    limit_concurrency: int  # contract.connection_budget()
    lease_chunk_tokens: int  # queue_depth(offered_service_rate()) x max request tokens
    lease_chunk_source: str  # "contract" | "declared-override (deviation)"
    max_request_tokens: int
    audit_queue: int | None  # queue_depth(measured audit drain rate); set after calibration
    guard_queue_tokens: int | None  # pool_size(GUARD) from the measured CapacityHint


def load(env: dict[str, str] | None = None) -> tuple[ResourceContract, tuple[str, ...]]:
    return load_contract(env=env)


def with_guard(contract: ResourceContract, tokens_per_s: float) -> ResourceContract:
    return dataclasses.replace(contract, guard_capacity=CapacityHint(tokens_per_second=tokens_per_s))


def max_request_tokens(s: Settings) -> int:
    return s.max_windows * (s.window_tokens - 2) + s.default_output_tokens


def derive(
    contract: ResourceContract,
    s: Settings,
    *,
    audit_rate: float | None = None,
) -> Bounds:
    budget = contract.connection_budget()
    mrt = max_request_tokens(s)
    lease = contract.queue_depth(contract.offered_service_rate()) * mrt
    lease_source = "contract"
    if s.lease_chunk_tokens is not None:  # bench knob (LGW06-3); logged, never silent
        lease, lease_source = s.lease_chunk_tokens, f"declared-override (deviation; contract={lease})"
    guard_q: int | None = None
    if contract.guard_capacity is not None:
        guard_q = contract.pool_size(PoolKind.GUARD)
    return Bounds(
        workers=contract.workers(),
        provider_connections=budget,
        redis_connections=contract.pool_size(PoolKind.REDIS),
        listen_backlog=budget,
        limit_concurrency=budget,
        lease_chunk_tokens=lease,
        lease_chunk_source=lease_source,
        max_request_tokens=mrt,
        audit_queue=contract.queue_depth(audit_rate) if audit_rate else None,
        guard_queue_tokens=guard_q,
    )


def stream_ceiling(contract: ResourceContract, active_streams: int) -> int:
    return contract.stream_buffer_bytes(active_streams)


def describe(contract: ResourceContract, logs: tuple[str, ...], bounds: Bounds) -> dict[str, object]:
    snap = snapshot(contract, logs)
    snap["rvproto_bounds"] = dataclasses.asdict(bounds)
    snap["guard_capacity"] = (
        None if contract.guard_capacity is None else contract.guard_capacity.tokens_per_second
    )
    return snap
