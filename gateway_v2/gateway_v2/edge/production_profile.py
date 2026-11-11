"""`AMF_PROFILE=production`: the boot that reads real signed state (GW36 S3; finished in S5).

What S3 makes real, on top of the honest fail-closed table `serve.build_boot_dependencies`
builds: the state runtime (one per worker, run in the ASGI lifespan), identity resolved from
signed KEY records by the key's sha256, per-org plans from signed PLAN records, readiness gated
on the stamp and on every kind being caught up, and a GW14d Publisher whose state series are
live. Everything else stays on its shipped fail-closed default and the boot log says so, by name:
provider, admission and quota (S4), detectors and catalog (S5). The kill switch is enforced
(GW29): the real incident gate, per-stream control source, revocation ledger and notifier pump
are built here, and the boot refuses to start if an incident knob or catalog input is invalid.

Selection is explicit. Unset or empty keeps today's table; any other value refuses to start,
as does `production` together with `AMF_DEV_PROFILE=1`. Read inside the per-worker factory path,
so the parent's refuse-to-start probe raises before any worker is spawned.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import socket
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from gateway_v2.admit.admission import AdmissionController
from gateway_v2.admit.budget_state import BudgetRecord
from gateway_v2.admit.codel import CoDelParams
from gateway_v2.admit.gcra import GcraParams, LocalGCRA
from gateway_v2.admit.gcra_cache import GcraCache
from gateway_v2.admit.lease import BudgetLease, LeaseConfig
from gateway_v2.admit.metrics import AdmissionMetrics, QuotaMetrics
from gateway_v2.admit.org_limits import OrgLimits
from gateway_v2.admit.quota import (
    AdmissionBounds,
    QuotaComponent,
    derive_bounds,
    derive_lease_cap_tokens,
    derive_low_watermark,
)
from gateway_v2.admit.quota_registry import QuotaRegistry
from gateway_v2.admit.supervisor import WorkerSupervisor
from gateway_v2.audit.metrics import AuditMetrics
from gateway_v2.domain.incident import KS_SCOPE_GENERATION
from gateway_v2.edge.dev_profile import dev_profile_active
from gateway_v2.edge.event_log import StructuredEventLog
from gateway_v2.edge.incident_catalog import CatalogConfig, CatalogError, load_catalog_config
from gateway_v2.edge.incident_publish import IncidentPublication
from gateway_v2.edge.incident_wiring import IncidentWiring, build_incident
from gateway_v2.edge.inflight_source import KillSwitchControlFactory
from gateway_v2.edge.publisher import Publisher
from gateway_v2.edge.quota_gate import QuotaGate
from gateway_v2.edge.routing_boot import RoutedBundle as RoutedBundle
from gateway_v2.edge.routing_boot import build_routed_provider as build_routed_provider
from gateway_v2.edge.state_runtime import StateRuntime, StateRuntimeConfig
from gateway_v2.runtime.errors import CapacityUnavailable, CapacityUnset
from gateway_v2.runtime.holdback_metrics import HoldbackMetrics
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.serving_config import load_serving_config
from gateway_v2.runtime.store_keys import StoreKeys
from gateway_v2.runtime.stream_metrics import PerRequestExports
from state_control.metrics import RehydrationMetrics

LOG = logging.getLogger("amf.gateway.production")
PROFILE_ENV = "AMF_PROFILE"
PRODUCTION = "production"

#: The DECLARED guard-service rate S4 reads and passes straight to `derive_bounds`;
#: GW20 measures it.
Q_SAFE_ENV = "AMF_Q_SAFE"

#: The optional per-worker count override used to split each principal's sustained rate across
#: workers (Req 3.4). When unset the one worker-count authority, `ResourceContract.workers()`
#: (which itself honours `WEB_CONCURRENCY`/`AMF_WEB_CONCURRENCY`), is used — there is no second
#: source of truth. An explicit `AMF_WORKERS` lets an operator declare the real process count when
#: it differs from what the contract detects; a present-but-invalid value refuses to start.
WORKERS_ENV = "AMF_WORKERS"

#: The optional lease reclaim TTL (seconds) for a worker's outstanding budget chunk. A TIME (the
#: store-clock window after which a crashed worker's chunk is reclaimed), not a capacity position —
#: it is not derived from CPU/RAM/FD — so it is allowed outside `resources.py`, exactly like
#: `serving_config`'s drain seconds. OPTIONAL with a shipped DECLARED default so `AMF_Q_SAFE` stays
#: the only new required production input; a present-but-malformed or non-positive value refuses.
LEASE_TTL_ENV = "AMF_LEASE_TTL_S"

#: The shipped DECLARED lease-reclaim TTL used when `AMF_LEASE_TTL_S` is unset. One minute of
#: store-clock runway to return a crashed worker's chunk to the pool — a reclaim window, not a
#: capacity size.
_DECLARED_LEASE_TTL_S = 60.0

#: The DECLARED audit sizing S4 reads and passes to `derive_bounds` for `audit_queue_depth`, until
#: `AuditSink.calibrate` is wired in S5 (Req 2.7/2.8). These are the same measured inputs the dev
#: profile reads from its `AMF_DEV_AUDIT_*` vars — NOT a capacity position (they feed
#: `contract.audit_queue_depth`, which is the only holder of the audit queue's size literal). To
#: keep `AMF_Q_SAFE` the ONLY new required production input (small blast radius), the audit inputs
#: are OPTIONAL: when unset they fall back to the shipped DECLARED defaults below, matching the dev
#: profile's "declared via env" contract with a dev-grade default rather than a refuse-to-start.
AUDIT_DRAIN_RATE_ENV = "AMF_AUDIT_DRAIN_RATE_PER_S"
AUDIT_BYTES_PER_RECORD_ENV = "AMF_AUDIT_BYTES_PER_RECORD"

#: Shipped DECLARED audit-sizing defaults used when the env vars and `AuditSink.calibrate` are both
#: absent. A drain rate (records/s) and an average record size (bytes) — measurement inputs, not
#: capacity positions. R2-05 measured ~2.7 KB per audit record on the shared store, so the declared
#: byte size is grounded in that measurement; the drain rate is a conservative one-record-per-ms
#: steady-state until GW14c/GW20 publish a measured value.
_DECLARED_AUDIT_DRAIN_RATE_PER_S = 1000.0
_DECLARED_AUDIT_BYTES_PER_RECORD = 2700

#: The boot-log lines the production boot replaces, keyed by the dependency they describe. S3 added
#: identity/snapshot_source/metrics_registry; S4 adds `admission` and `quota` so the fail-closed
#: `boot: admission -> fail_closed_admission ...` and `boot: quota -> fail_closed_budget ...` lines
#: `serve.build_boot_dependencies` seeds are DROPPED and replaced by the real wired lines below,
#: rather than duplicated.
_REPLACED = ("identity", "snapshot_source", "metrics_registry", "admission", "quota")


class ProfileError(ValueError):
    """`AMF_PROFILE` is set to something this build cannot serve. Refuse to start."""


def production_profile_active(env: Mapping[str, str]) -> bool:
    raw = (env.get(PROFILE_ENV) or "").strip()
    if not raw:
        return False
    if raw != PRODUCTION:
        raise ProfileError(f"{PROFILE_ENV}={raw!r} is not recognised; use {PRODUCTION!r} or unset")
    if dev_profile_active(env):
        raise ProfileError(f"{PROFILE_ENV}={PRODUCTION} and AMF_DEV_PROFILE=1 are exclusive")
    return True


def _resolve_q_safe(env: Mapping[str, str]) -> float:
    """The DECLARED guard-service rate passed straight to `derive_bounds`; refuse to start if unset.

    No default and no literal: `q_safe` is an injected measurement (GW20 owns it), so the boot
    refuses rather than guessing. Blank/whitespace -> missing; non-decimal or `<= 0` -> rejected,
    reproducing the offending value.
    """
    raw = (env.get(Q_SAFE_ENV) or "").strip()
    if not raw:
        raise ProfileError(
            f"{Q_SAFE_ENV} is required (the DECLARED guard service rate; GW20 measures it)"
        )
    try:
        q_safe = float(raw)
    except ValueError as exc:
        raise ProfileError(f"{Q_SAFE_ENV}={raw!r} is not a decimal number") from exc
    if q_safe <= 0.0:
        raise ProfileError(f"{Q_SAFE_ENV}={raw!r} must be strictly greater than 0")
    return q_safe


def _resolve_audit_sizing(env: Mapping[str, str]) -> tuple[float, int]:
    """The audit drain rate + bytes-per-record `derive_bounds` sizes the audit queue from.

    Req 2.7/2.8: when `AuditSink.calibrate` is wired its measured result feeds `derive_bounds`;
    until then (S5 wires the real sink — `_NoAudit` is the placeholder here) the boot reads the
    DECLARED `AMF_AUDIT_DRAIN_RATE_PER_S` / `AMF_AUDIT_BYTES_PER_RECORD` from the environment, as
    the dev profile does. Unlike the dev profile, these are OPTIONAL in production so `AMF_Q_SAFE`
    stays the only new required input: an unset or blank var falls back to the shipped DECLARED
    default. A present-but-malformed or non-positive value still refuses — a declared value that
    cannot be parsed is a configuration error, not a reason to silently use the default.
    """
    drain_rate = _declared_positive_float(
        env, AUDIT_DRAIN_RATE_ENV, _DECLARED_AUDIT_DRAIN_RATE_PER_S
    )
    bytes_per_record = _declared_positive_int(
        env, AUDIT_BYTES_PER_RECORD_ENV, _DECLARED_AUDIT_BYTES_PER_RECORD
    )
    return drain_rate, bytes_per_record


def _declared_positive_float(env: Mapping[str, str], name: str, default: float) -> float:
    """Parse a DECLARED positive float from `env[name]`; blank/absent -> `default`; else refuse."""
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ProfileError(f"{name}={raw!r} is not a decimal number") from exc
    if value <= 0.0:
        raise ProfileError(f"{name}={raw!r} must be strictly greater than 0")
    return value


def _declared_positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    """Parse a DECLARED positive int from `env[name]`; blank/absent -> `default`; else refuse."""
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ProfileError(f"{name}={raw!r} is not an integer") from exc
    if value < 1:
        raise ProfileError(f"{name}={raw!r} must be >= 1")
    return value


def _resolve_worker_count(env: Mapping[str, str], contract: ResourceContract) -> int:
    """The per-worker rate divisor for the GCRA cache (Req 3.4): `AMF_WORKERS` or the contract.

    `ResourceContract.workers()` is the one worker-count authority (it folds in the
    `WEB_CONCURRENCY`/`AMF_WEB_CONCURRENCY` override), so an unset `AMF_WORKERS` uses it rather than
    inventing a second source of truth. An explicit `AMF_WORKERS` lets an operator declare the real
    deployed process count when it differs from what the box detects; a present-but-invalid value
    refuses to start (a declared count that cannot be parsed is a configuration error, not a reason
    to silently fall back). The contract path propagates `CapacityUnavailable` when the box cannot
    derive a worker (refuse-to-start, consistent with the admission bounds).
    """
    raw = (env.get(WORKERS_ENV) or "").strip()
    if not raw:
        return contract.workers()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ProfileError(f"{WORKERS_ENV}={raw!r} is not an integer") from exc
    if value < 1:
        raise ProfileError(f"{WORKERS_ENV}={raw!r} must be >= 1")
    return value


def _resolve_lease_ttl_s(env: Mapping[str, str]) -> float:
    """The budget-lease reclaim TTL (seconds); `AMF_LEASE_TTL_S` or the shipped DECLARED default."""
    return _declared_positive_float(env, LEASE_TTL_ENV, _DECLARED_LEASE_TTL_S)


def _off_path_spawn() -> Callable[[Awaitable[None]], None]:
    """An off-request-path scheduler for the lease's Async_Refill, with a strong task reference.

    `BudgetLease.try_spend` is synchronous and schedules a refill via this `spawn`; the refill must
    run OFF the request path (Req 4.2) and must not be garbage-collected while in flight, so each
    coroutine is wrapped in a task kept in an instance-local set until it completes (the dev
    profile's `_spawn` idiom). Returns a closure over that set — no module-level mutable state.
    """
    tasks: set[asyncio.Future[None]] = set()

    def spawn(work: Awaitable[None]) -> None:
        task = asyncio.ensure_future(work)
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    return spawn


def _budget_component_factory(
    *,
    contract: ResourceContract,
    q_safe: float,
    client: Any,
    keys: StoreKeys,
    lease_ttl_s: float,
    metrics: QuotaMetrics,
    clock: Callable[[], float],
) -> Callable[[str, BudgetRecord], QuotaComponent]:
    """Build the `QuotaRegistry.make_component` factory: `(org, BudgetRecord) -> QuotaComponent`.

    The registry mints at most one component per org on first use; this factory is what it calls.
    Everything the lease needs that the registry does not know is closed over here (design §4):

    * the token-sized Lease_Chunk (`derive_lease_cap_tokens`) and its Low_Watermark
      (`derive_low_watermark`), derived ONCE from the contract + `q_safe` — identical for every org,
      so a worker never holds more outstanding budget than the contract's in-flight depth allows and
      a single max-size request's token cost never exceeds one chunk (Group F);
    * the one bounded store client the state runtime's readers share (no second connection) and the
      `StoreKeys` layout the `{rv2}:budget:{org}:*` live-counter family is built from;
    * a stable per-worker id (`host:pid`) so the per-worker lease record and the fair-share ZSET are
      keyed per worker and a crashed worker's chunk is TTL-reclaimed;
    * an off-path `spawn` for the Async_Refill, and the shared `QuotaMetrics` producer.

    Each built component's per-org `BudgetLease` carries a bounded overdraft of one max-size request
    (`contract.max_request_tokens()`), matching the dev lease, so a single in-flight request whose
    token cost was not yet reflected in the held chunk is still admitted within the bounded floor.

    The component's internal `LocalGCRA` is NOT the per-key rate limiter — that is the composed
    gate's `GcraCache` (step 1). A per-org component sees only its org and its record, so it has no
    per-key `(rate_per_s, burst)` to enforce; it is built permissive against the guard service rate
    (`rate_per_s = q_safe`, both injected/contract-derived — no literal, `q_safe > 0` guaranteed by
    `_resolve_q_safe`) with a burst of one chunk, so admission (already bounded by `q_safe`) and the
    budget lease remain the binding gates and the internal GCRA never rejects a request admission
    already let through. The generation source returns the served record's generation captured at
    build time; a generation change rebuilds the whole component through the registry's re-acquire
    hook, so the captured value never drifts from the lease's.

    No capacity literal appears here (chunk/watermark/overdraft are all contract calls); the factory
    holds only its closed-over collaborators — no module-level mutable state.
    """
    chunk = derive_lease_cap_tokens(contract, q_safe=q_safe)
    low_watermark = derive_low_watermark(chunk)
    overdraft_tokens = contract.max_request_tokens()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    spawn = _off_path_spawn()

    def make_component(org: str, record: BudgetRecord) -> QuotaComponent:
        generation = record.generation
        lease = BudgetLease(
            client,
            LeaseConfig(
                org=org,
                chunk=chunk,
                low_watermark=low_watermark,
                ttl_s=lease_ttl_s,
            ),
            worker_id=worker_id,
            clock=clock,
            spawn=spawn,
            metrics=metrics,
            keys=keys,
            overdraft_tokens=overdraft_tokens,
        )
        gcra = LocalGCRA(GcraParams(rate_per_s=q_safe, burst=chunk), clock=clock)
        return QuotaComponent(
            gcra,
            lease,
            generation_source=lambda: generation,
            metrics=metrics,
        )

    return make_component


class _LiveState:
    """The state producer read from the runtime on every scrape, not frozen at boot (N14)."""

    __slots__ = ("_runtime",)

    def __init__(self, runtime: StateRuntime) -> None:
        self._runtime = runtime

    def series(self) -> Mapping[str, float]:
        return self._runtime.metrics.snapshot().series()


class _NoAudit:
    """No audit sink is wired before S5: its series read as zero traffic, never as healthy."""

    __slots__ = ()

    def series(self) -> Mapping[str, float]:
        return AuditMetrics().series()


def _build_admission(
    contract: ResourceContract,
    env: Mapping[str, str],
    clock: Callable[[], float],
    *,
    q_safe: float,
) -> tuple[AdmissionController, AdmissionMetrics, AdmissionBounds]:
    """Build the REAL GW19 admission controller + its shared metrics + derived bounds (Req 2.1).

    Extracted from :func:`apply_production` so that function stays under the function-size gate;
    behaviour-preserving (the returned controller, metrics, and bounds are byte-identical to the
    inlined construction). Every bound is a `ResourceContract` call via `derive_bounds`; `q_safe`
    is the injected DECLARED measurement and the audit sizing is DECLARED via env (Req 2.7/2.8,
    `AuditSink.calibrate` lands in S5). `derive_bounds` raises `CapacityUnset` /
    `CapacityUnavailable` on an unset q_safe or an uncomputable bound; both propagate OUT of
    `apply_production` as refuse-to-start (Req 1.6/2.3) with NO fallback to `fail_closed_admission`.
    CoDel params are the shipped owner-signed defaults; the shed-jitter source is a fresh
    system-seeded RNG per worker; the drain window is the env-loaded `RV_DRAIN_S` the SIGTERM drain
    already uses (no literal). The `AdmissionMetrics` is shared by the controller (producer) and the
    Publisher so the admit/shed counts and queue depths surface live on `/metrics`.
    """
    audit_drain_rate_per_s, audit_bytes_per_record = _resolve_audit_sizing(env)
    bounds = derive_bounds(
        contract,
        q_safe=q_safe,
        audit_drain_rate_per_s=audit_drain_rate_per_s,
        audit_bytes_per_record=audit_bytes_per_record,
    )
    serving_cfg, _serving_logs = load_serving_config(env)
    admission_metrics = AdmissionMetrics()
    admission = AdmissionController(
        bounds=bounds,
        params=CoDelParams(),
        rng=random.Random(),
        supervisor=WorkerSupervisor(),
        metrics=admission_metrics,
        drain_window_s=serving_cfg.drain_s,
        clock=clock,
    )
    return admission, admission_metrics, bounds


def _wire_quota_gate(
    runtime: StateRuntime,
    *,
    contract: ResourceContract,
    q_safe: float,
    env: Mapping[str, str],
    clock: Callable[[], float],
) -> tuple[QuotaGate, QuotaMetrics, int, int, int]:
    """Compose the quota gate, replacing `fail_closed_budget`, and close its two hooks (Req 4.1).

    Extracted from :func:`apply_production` so that function stays under the function-size gate;
    behaviour-preserving. Two per-worker objects back the gate: a per-principal `GcraCache` (the
    per-key burst/rate, split across workers) and a per-org `QuotaRegistry` over the served
    `BudgetSnapshot` (the per-org token lease). The gate composes them GCRA-then-budget behind the
    one `evaluate`/`refund` surface the lifecycle couples to. Every number is contract-derived: the
    lease chunk/watermark from `derive_lease_cap_tokens`/`derive_low_watermark`, the worker-count
    divisor from the contract (or the declared `AMF_WORKERS`); no capacity literal appears here.

    Closes the two construction-order hooks now that both request-path objects exist: a KEY delta
    invalidates the GCRA cache alongside the identity cache (Req 3.3), and a BUDGET generation
    change marks that org's lease for re-acquisition (Req 7.3). The appliers map was already
    complete at `StateRuntime.__init__` (Req 7.1); these setters only point its indirections at the
    live collaborators. Returns the composed gate + its shared metrics + the `worker_count`,
    `lease_chunk`, and `lease_watermark` the budget boot-log line reports (derived ONCE here, the
    same pure function of the contract plus `q_safe` the component factory uses -- no second source
    of truth, no capacity literal).
    """
    worker_count = _resolve_worker_count(env, contract)
    gcra_cache = GcraCache(worker_count=worker_count, clock=clock)
    quota_metrics = QuotaMetrics()
    lease_chunk = derive_lease_cap_tokens(contract, q_safe=q_safe)
    lease_watermark = derive_low_watermark(lease_chunk)
    make_component = _budget_component_factory(
        contract=contract,
        q_safe=q_safe,
        client=runtime.store_client,
        keys=runtime.store_keys,
        lease_ttl_s=_resolve_lease_ttl_s(env),
        metrics=quota_metrics,
        clock=clock,
    )
    registry = QuotaRegistry(runtime.budget_snapshot, make_component=make_component)
    quota_gate = QuotaGate(gcra_cache, registry)
    runtime.set_gcra_invalidate(gcra_cache.invalidate)
    runtime.set_budget_reacquire_hook(registry.on_generation_change)
    return quota_gate, quota_metrics, worker_count, lease_chunk, lease_watermark


def _wire_incident(
    contract: ResourceContract, env: Mapping[str, str], clock: Callable[[], float],
) -> tuple[CatalogConfig, Callable[[StateRuntime], IncidentWiring]]:
    """Load the catalog inputs, then return the builder that wires the incident components.

    The catalog (so the alias table) must exist BEFORE the `StateRuntime`: the kill-switch parser
    takes the aliases at construction. Any invalid catalog input or underivable incident knob is a
    `ProfileError` (refuse to start), never a silent fail-closed default.
    """
    try:
        config = load_catalog_config(env)
    except CatalogError as exc:
        raise ProfileError(str(exc)) from exc
    def build(runtime: StateRuntime) -> IncidentWiring:
        try:
            wiring = build_incident(
                runtime, contract, env=env, clock=clock, wall=time.time,
                rng=random.Random(), config=config,
            )
        except (CapacityUnset, CapacityUnavailable) as exc:
            raise ProfileError(f"incident knob cannot be derived: {exc}") from exc
        # AC12: production never runs on a dev/fail-closed control source.
        if not isinstance(wiring.control_factory, KillSwitchControlFactory):
            raise ProfileError("production needs the real kill-switch control factory")
        runtime.set_revocation_observer(wiring.ledger.observe)
        LOG.info(
            "incident wiring: scope generation %d, catalog digest %s",
            KS_SCOPE_GENERATION, config.digest or "none",
        )
        return wiring
    return config, build


def _signed_state_readiness(
    runtime: StateRuntime, wiring: IncidentWiring,
) -> Callable[[], tuple[bool, str | None]]:
    """`/readyz`: signed state first, then the incident wiring (built, catalog digest present)."""
    def probe() -> tuple[bool, str | None]:
        ready, reason = runtime.ready()
        return (ready, reason) if not ready else wiring.ready()
    return probe


def apply_production(
    deps: dict[str, object],
    logs: tuple[str, ...],
    *,
    contract: ResourceContract,
    env: Mapping[str, str],
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Overlay the S3 production pieces on the fail-closed table. Raises on missing config."""
    # Refuse to start before any component is built when the DECLARED guard rate is absent or
    # non-positive (Req 1). The resolved rate is passed straight to `derive_bounds` below.
    q_safe = _resolve_q_safe(env)
    clock = time.monotonic
    catalog_config, build_wiring = _wire_incident(contract, env, clock)
    runtime = StateRuntime(StateRuntimeConfig.from_env(env), aliases=catalog_config.aliases)
    wiring = build_wiring(runtime)
    admission, admission_metrics, bounds = _build_admission(
        contract, env, clock, q_safe=q_safe
    )
    quota_gate, quota_metrics, worker_count, lease_chunk, lease_watermark = _wire_quota_gate(
        runtime, contract=contract, q_safe=q_safe, env=env, clock=clock
    )

    exports, holdback = PerRequestExports(), HoldbackMetrics()
    publisher = Publisher.from_contract(
        admission=admission_metrics,
        quota=quota_metrics,
        audit=_NoAudit(),
        rehydration=RehydrationMetrics(),
        state=_LiveState(runtime),
        stream=exports,
        holdback=holdback,
        event_log=StructuredEventLog.from_contract(contract, clock=clock),
        contract=contract,
        clock=clock,
        incident=IncidentPublication(
            metrics=wiring.metrics, risk=wiring.risk, contract=contract,
            refresh=wiring.refresh_gauges,
        ),
    )
    deps.update(
        identity=runtime.identity_resolver(),
        snapshot_source=runtime.snapshot_source,
        readiness=_signed_state_readiness(runtime, wiring),
        metrics_registry=publisher,
        observer=publisher,
        exports=exports,
        holdback_metrics=holdback,
        state_runtime=runtime,
        # Overlay the real controller + composed quota gate onto the `fail_closed_admission` /
        # `fail_closed_budget` defaults `serve.build_boot_dependencies` seeded (Req 2.1, 4.1,
        # 5.1, 5.6). Neither fail-closed default remains in `deps` after this.
        admission=admission,
        quota=quota_gate,
        org_limits=OrgLimits(contract, metrics=admission_metrics),
        # GW29: the real incident gate and per-stream control source replace the fail-closed
        # defaults (which refuse every request); the pump and ticker run in the lifespan.
        incident_gate=wiring.gate,
        incident_recheck=wiring.gate.recheck,
        control_factory=wiring.control_factory,
        audit=wiring.audit,
        lifespan_hooks=(wiring,),
    )
    kept = tuple(
        line for line in logs if not any(line.startswith(f"boot: {name} ->") for name in _REPLACED)
    )
    return deps, (
        "boot: PRODUCTION PROFILE (AMF_PROFILE=production) -- GW36 S3: signed state, identity "
        "and plans are real; every other line below is still its fail-closed default (S4/S5)",
        "boot: state -> signed-state runtime over AMF_STATE_VALKEY_URL (namespace "
        f"{runtime.namespace}, fresh {runtime.fresh_ms} ms, refresh {runtime.refresh_ms} ms)",
        "boot: identity -> sha256(bearer) over signed KEY records (401 invalid_api_key for a "
        "missing/unknown/revoked key, 503 shared_state_unavailable when unverified)",
        "boot: snapshot_source -> per-org plans from signed PLAN records",
        f"boot: metrics_registry -> GW14d Publisher ({publisher.SERIES_COUNT} series; state and "
        "admission/quota live; audit zero until S5)",
        f"boot: admission -> AdmissionController (q_safe DECLARED={q_safe:g}, GW20 owns the "
        f"measured value; request_depth={bounds.request_depth} concurrency={bounds.concurrency} "
        f"audit_depth={bounds.audit_depth})",
        f"boot: rate -> per-principal LocalGCRA, per-worker rate = rate_per_s / {worker_count} "
        "(rate_per_s 0 = no per-key request limit, DECLARED)",
        "boot: org_limits -> per-org rate / burst / TPM from the signed plan controls "
        "(per gateway replica, GW27; no store I/O on the request path)",
        f"boot: budget -> per-org BudgetLease registry over signed BUDGET records "
        f"(chunk={lease_chunk} tokens, watermark={lease_watermark}); budget_unavailable on empty "
        "pool, never shared_state_unavailable",
        wiring.status_line(),
        *kept,
    )
