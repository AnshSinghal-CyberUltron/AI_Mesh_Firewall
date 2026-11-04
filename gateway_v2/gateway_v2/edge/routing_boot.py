"""Boot-time construction of the GW28 routed provider (dev profile + production factory).

Every bound is read from ``ResourceContract`` (no capacity literal here). ``RoutedBundle`` carries
the routed provider plus the ``aclose`` of every FACTORY-BUILT client only: clients the caller
already owns (``owned_elsewhere``) are never sized and never closed here, so a shared instance is
never closed twice. ``build_routed_provider`` is the factory GW36 S4/S5 will call; until then the
production profile keeps its fail-closed default provider.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from gateway_v2.dispatch.model_routing import ModelRouter
from gateway_v2.dispatch.openai_upstream import OpenAIUpstreamProvider
from gateway_v2.dispatch.provider import ProviderClient
from gateway_v2.dispatch.routed_provider import RoutedProvider
from gateway_v2.dispatch.routing_types import (
    Candidate,
    CandidateSet,
    RoutingConfigError,
    RoutingSnapshot,
    RoutingTable,
)
from gateway_v2.dispatch.stall_guard import StallBounds
from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.kinds import PoolKind
from gateway_v2.runtime.resources import ResourceContract

__all__ = (
    "RoutedBundle",
    "bounds_line",
    "build_dev_routing",
    "build_routed_provider",
    "stall_bounds",
)

_DEV_CANDIDATE_ID = "dev"


@dataclass(frozen=True, slots=True)
class RoutedBundle:
    """The routed provider plus the closers of the clients this factory built."""

    routed: RoutedProvider
    closers: tuple[Callable[[], Awaitable[None]], ...]


def stall_bounds(contract: ResourceContract) -> StallBounds:
    """The per-attempt stall bounds, all derived from the contract."""
    return StallBounds(
        first_event_s=contract.upstream_first_event_timeout_s(),
        gap_s=contract.upstream_gap_timeout_s(),
        total_s=contract.upstream_total_timeout_s(),
        teardown_s=contract.cancellation_bound_s(),
    )


def bounds_line(contract: ResourceContract) -> str:
    """The start-up record of the four effective upstream bounds (Req 5.6)."""
    return (
        f"routing bounds first_event_s={contract.upstream_first_event_timeout_s()} "
        f"gap_s={contract.upstream_gap_timeout_s()} "
        f"total_s={contract.upstream_total_timeout_s()} "
        f"pre_content_budget_s="
        f"{contract.routing_pre_content_budget_s(contract.routing_max_attempts())}"
    )


def _healthy() -> RoutingSnapshot:
    """Default snapshot: nothing unavailable (health is injected; there is no probe)."""
    return RoutingSnapshot()


def build_dev_routing(
    contract: ResourceContract, provider: OpenAIUpstreamProvider
) -> RoutedProvider:
    """Dev profile: ONE passthrough candidate in an explicit ``fallback_set`` whose client IS
    the profile's single plain provider (one shared pool, no second client; D-11)."""
    ident_len = contract.routing_max_identifier_len()
    candidate = Candidate(
        candidate_id=_DEV_CANDIDATE_ID,
        destination="dev-upstream",
        upstream_model="",
        served_models=frozenset(),
        weight=1,
        provider_key=_DEV_CANDIDATE_ID,
        passthrough=True,
    )
    fallback = CandidateSet.build(
        (candidate,),
        max_candidates=contract.routing_max_candidates(),
        max_weight=contract.routing_max_weight(),
        max_ident_len=ident_len,
        allow_passthrough=True,
    )
    return RoutedProvider(
        router=ModelRouter(),
        table=RoutingTable.build({}, fallback_set=fallback),
        clients={_DEV_CANDIDATE_ID: provider},
        bounds=stall_bounds(contract),
        max_attempts=contract.routing_max_attempts(),
        max_ident_len=ident_len,
        snapshot_source=_healthy,
    )


def build_routed_provider(
    contract: ResourceContract,
    table: RoutingTable,
    client_factory: Callable[[str, int], OpenAIUpstreamProvider],
    snapshot_source: Callable[[], RoutingSnapshot],
    *,
    log: Callable[[str], None],
    owned_elsewhere: Mapping[str, ProviderClient] | None = None,
) -> RoutedBundle:
    """Build the routed provider for ``table``; every distinct non-owned provider key gets ONE
    factory-built client sized ``provider_pool_share(n)``. Refuses to boot when the budget
    cannot be split or the created clients exceed ``pool_size(PROVIDER)``."""
    owned: Mapping[str, ProviderClient] = owned_elsewhere or {}
    candidates = table.all_candidates()
    keys = sorted({c.provider_key for c in candidates} - set(owned))
    built: dict[str, OpenAIUpstreamProvider] = {}
    if keys:
        try:
            share = contract.provider_pool_share(len(keys))
        except CapacityUnavailable as exc:
            raise RoutingConfigError("provider pool cannot be split across clients") from exc
        for key in keys:
            built[key] = client_factory(key, share)
        if sum(c.connections for c in built.values()) > contract.pool_size(PoolKind.PROVIDER):
            raise RoutingConfigError("created clients exceed the provider pool budget")
    clients: dict[str, ProviderClient] = {}
    for c in candidates:
        clients[c.candidate_id] = (
            owned[c.provider_key] if c.provider_key in owned else built[c.provider_key]
        )
    routed = RoutedProvider(
        router=ModelRouter(),
        table=table,
        clients=clients,
        bounds=stall_bounds(contract),
        max_attempts=contract.routing_max_attempts(),
        max_ident_len=contract.routing_max_identifier_len(),
        snapshot_source=snapshot_source,
    )
    log(bounds_line(contract))
    return RoutedBundle(routed=routed, closers=tuple(b.aclose for b in built.values()))
