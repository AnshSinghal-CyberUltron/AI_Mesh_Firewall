"""`AMF_PROFILE=production`: the boot that reads real signed state (GW36 S3; finished in S5).

What S3 makes real, on top of the honest fail-closed table `serve.build_boot_dependencies`
builds: the state runtime (one per worker, run in the ASGI lifespan), identity resolved from
signed KEY records by the key's sha256, per-org plans from signed PLAN records, readiness gated
on the stamp and on every kind being caught up, and a GW14d Publisher whose state series are
live. Everything else stays on its shipped fail-closed default and the boot log says so, by name:
provider, admission and quota (S4), detectors and catalog (S5), kill-switch enforcement (GW29).

Selection is explicit. Unset or empty keeps today's table; any other value refuses to start,
as does `production` together with `AMF_DEV_PROFILE=1`. Read inside the per-worker factory path,
so the parent's refuse-to-start probe raises before any worker is spawned.
"""

from __future__ import annotations

import time
from collections.abc import Mapping

from gateway_v2.admit.metrics import AdmissionMetrics, QuotaMetrics
from gateway_v2.audit.metrics import AuditMetrics
from gateway_v2.edge.dev_profile import dev_profile_active
from gateway_v2.edge.event_log import StructuredEventLog
from gateway_v2.edge.publisher import Publisher
from gateway_v2.edge.state_runtime import StateRuntime, StateRuntimeConfig
from gateway_v2.runtime.holdback_metrics import HoldbackMetrics
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.stream_metrics import PerRequestExports
from state_control.metrics import RehydrationMetrics

PROFILE_ENV = "AMF_PROFILE"
PRODUCTION = "production"

#: The boot-log lines S3 replaces, keyed by the dependency they describe.
_REPLACED = ("identity", "snapshot_source", "metrics_registry")


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


def apply_production(
    deps: dict[str, object],
    logs: tuple[str, ...],
    *,
    contract: ResourceContract,
    env: Mapping[str, str],
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Overlay the S3 production pieces on the fail-closed table. Raises on missing config."""
    runtime = StateRuntime(StateRuntimeConfig.from_env(env))
    clock = time.monotonic
    exports, holdback = PerRequestExports(), HoldbackMetrics()
    publisher = Publisher.from_contract(
        admission=AdmissionMetrics(),
        quota=QuotaMetrics(),
        audit=_NoAudit(),
        rehydration=RehydrationMetrics(),
        state=_LiveState(runtime),
        stream=exports,
        holdback=holdback,
        event_log=StructuredEventLog.from_contract(contract, clock=clock),
        contract=contract,
        clock=clock,
    )
    deps.update(
        identity=runtime.identity_resolver(),
        snapshot_source=runtime.snapshot_source,
        readiness=runtime.ready,
        metrics_registry=publisher,
        observer=publisher,
        exports=exports,
        holdback_metrics=holdback,
        state_runtime=runtime,
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
        f"boot: metrics_registry -> GW14d Publisher ({publisher.SERIES_COUNT} series; state "
        "live; admission/quota/audit zero until S4/S5)",
        "boot: kill switch -> snapshot kept current, NOT enforced on the request path (GW29)",
        *kept,
    )
