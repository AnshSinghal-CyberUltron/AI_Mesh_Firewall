"""DEV PROFILE admission + budget wiring and the per-worker app wrapper.

Wired ONLY when ``AMF_DEV_PROFILE=1`` (see :mod:`gateway_v2.edge.dev_profile`).

**Admission — the REAL GW19 controller, unwrapped.** :func:`build_dev_admission` builds and
returns a real :class:`~gateway_v2.admit.admission.AdmissionController` from
:func:`~gateway_v2.admit.quota.derive_bounds` with ``q_safe`` taken from the environment (never a
default). The controller structurally satisfies the ``AdmissionDecider`` surface the lifecycle
reads: ``admit`` and the slot-exact, idempotent ``complete``. The shipped GW15 lifecycle now
constructs a ``RequestHolds`` and calls ``complete(request_id)`` for every admitted request on
every exit (``finally``), so the dev profile needs NO per-request slot wrapper — there is ONE
completion path on both the default and the dev boot path.

**Budget — the REAL GW06 façade over Redis, or an explicitly-named unlimited stand-in.**
:class:`DevLeaseBudget` composes the shipped :class:`~gateway_v2.admit.quota.QuotaComponent`
(``LocalGCRA`` + ``BudgetLease`` over ``redis.asyncio``). It charges the REAL request cost: the
edge lifecycle passes the v1 token estimate (``estimate_request_tokens`` over the clamped request)
and the lease chunk is sized in tokens (``derive_lease_cap_tokens``), so the cost and the chunk are
the same unit (Group F, Property 12) and ``evaluate`` passes the cost straight through to the
composed :class:`QuotaComponent`. :class:`DevUnlimitedBudget` is the alternative when no store is
wanted; it admits everything and is logged as NOT enforced.

**The wrapper.** :class:`DevServedApp` wraps the shipped ``build_app`` callable ONLY to drive the
lease's startup/shutdown on the ASGI lifespan events (acquire / return-unspent). It forwards every
message unchanged otherwise; it has no admission role (the lifecycle completes each slot on exit).
"""

from __future__ import annotations

import asyncio
import os
import random
import socket
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from gateway_v2.admit.admission import AdmissionController
from gateway_v2.admit.codel import CoDelParams
from gateway_v2.admit.gcra import GcraParams, LocalGCRA
from gateway_v2.admit.grant import Admitted, BudgetVerdict
from gateway_v2.admit.lease import BudgetLease, LeaseConfig
from gateway_v2.admit.metrics import AdmissionMetrics, QuotaMetrics
from gateway_v2.admit.quota import (
    AdmissionBounds,
    QuotaComponent,
    derive_bounds,
    derive_lease_cap_tokens,
    derive_low_watermark,
)
from gateway_v2.admit.supervisor import WorkerSupervisor
from gateway_v2.edge.asgi_sse import ASGIReceive, ASGIScope, ASGISend
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.store_keys import KEYS

__all__ = (
    "DevLeaseBudget",
    "DevServedApp",
    "DevUnlimitedBudget",
    "LeaseSettings",
    "build_dev_admission",
)

#: The ASGI callable shape (scope, receive, send), matching ``edge/app.py::ASGIApplication``.
_Scope = ASGIScope
_Receive = ASGIReceive
_Send = ASGISend
_ASGIApp = Callable[[_Scope, _Receive, _Send], Awaitable[None]]


# --------------------------------------------------------------------------- #
# Admission.
# --------------------------------------------------------------------------- #


def build_dev_admission(
    *,
    contract: ResourceContract,
    q_safe: float,
    audit_drain_rate_per_s: float,
    audit_bytes_per_record: int,
    drain_window_s: float,
) -> tuple[AdmissionController, AdmissionBounds]:
    """Build the real GW19 controller from ``derive_bounds`` (q_safe DECLARED via env).

    Returns the bare :class:`AdmissionController` — which structurally satisfies the lifecycle's
    ``AdmissionDecider`` surface (``admit`` + the slot-exact, idempotent ``complete``) — together
    with the ``derive_bounds`` result, so the caller can build the boot log without reading a
    bound off the admission object. No per-request slot wrapper: the GW15 lifecycle completes each
    admitted slot on every exit.

    ``derive_bounds`` raises ``CapacityUnset`` / ``CapacityUnavailable`` on a bad ``q_safe`` or an
    uncomputable bound; the caller maps that to refuse-to-start. CoDel parameters are the shipped
    owner-signed defaults (``CoDelParams()``); the shed jitter source is a fresh system-seeded
    ``random.Random`` per worker.
    """
    bounds = derive_bounds(
        contract,
        q_safe=q_safe,
        audit_drain_rate_per_s=audit_drain_rate_per_s,
        audit_bytes_per_record=audit_bytes_per_record,
    )
    controller = AdmissionController(
        bounds=bounds,
        params=CoDelParams(),
        rng=random.Random(),
        supervisor=WorkerSupervisor(),
        metrics=AdmissionMetrics(),
        drain_window_s=drain_window_s,
        clock=time.monotonic,
    )
    return controller, bounds


# --------------------------------------------------------------------------- #
# Budget.
# --------------------------------------------------------------------------- #


class DevUnlimitedBudget:
    """A ``BudgetDecider`` that admits every request. DEV PROFILE ONLY: budget NOT enforced.

    Selected only by an explicit ``AMF_DEV_BUDGET=unlimited``; the boot log names it loudly.
    """

    __slots__ = ("_org_id",)

    def __init__(self, *, org_id: str) -> None:
        self._org_id = org_id

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict | Admitted:
        del cost
        return Admitted(owner_id=self._org_id, request_id=request_id, admitted_at=0.0)

    def refund(self, cost: int, *, request_id: str) -> None:
        """No-op (``BudgetDecider`` protocol): the unlimited budget spent nothing to return."""
        del cost, request_id


@dataclass(frozen=True, slots=True)
class LeaseSettings:
    """The declared inputs of the dev GW06 lease (all from the environment, none defaulted)."""

    redis_url: str
    pool_tokens: int
    rate_per_s: float
    burst: int
    ttl_s: float


class DevLeaseBudget:
    """The REAL GW06 :class:`QuotaComponent` (GCRA + Redis ``BudgetLease``) for the dev org.

    ``evaluate`` charges the REAL request cost — the v1 token estimate the edge lifecycle passes —
    against the token-sized lease chunk (``derive_lease_cap_tokens``), so cost and chunk share the
    same unit (Group F). ``refund`` returns a spend that reached zero provider calls. The pool key
    is seeded with ``pool_tokens`` only if absent (``SET NX``), the generation key likewise with
    ``0``; the generation is read once at startup (there is no budget-config change feed in the
    dev profile).
    The lease is acquired on ASGI ``lifespan.startup`` and the unspent remainder returned on
    ``lifespan.shutdown``; refills run off the request path via the instance task set.
    """

    __slots__ = ("_client", "_generation", "_lease", "_org_id", "_quota", "_settings", "_tasks")

    def __init__(
        self,
        *,
        contract: ResourceContract,
        q_safe: float,
        settings: LeaseSettings,
        org_id: str,
        client: Any | None = None,
    ) -> None:
        chunk = derive_lease_cap_tokens(contract, q_safe=q_safe)
        if client is None:
            client = _redis_client(settings.redis_url, timeout_s=contract.cancellation_bound_s())
        self._client = client
        self._settings = settings
        self._org_id = org_id
        self._generation = 0
        self._tasks: set[asyncio.Future[None]] = set()
        metrics = QuotaMetrics()
        self._lease = BudgetLease(
            client,
            LeaseConfig(
                org=org_id,
                chunk=chunk,
                low_watermark=derive_low_watermark(chunk),
                ttl_s=settings.ttl_s,
            ),
            worker_id=f"{socket.gethostname()}:{os.getpid()}",
            spawn=self._spawn,
            metrics=metrics,
            overdraft_tokens=contract.max_request_tokens(),
        )
        self._quota = QuotaComponent(
            LocalGCRA(GcraParams(rate_per_s=settings.rate_per_s, burst=settings.burst)),
            self._lease,
            generation_source=self._current_generation,
            metrics=metrics,
        )

    @property
    def lease(self) -> BudgetLease:
        return self._lease

    def _current_generation(self) -> int:
        return self._generation

    def _spawn(self, work: Awaitable[None]) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def evaluate(self, cost: int, *, request_id: str) -> BudgetVerdict | Admitted:
        return await self._quota.evaluate(cost, request_id=request_id)

    def refund(self, cost: int, *, request_id: str) -> None:
        """Return a charged spend that reached zero provider calls (``BudgetDecider`` protocol).

        Delegates to the composed :meth:`QuotaComponent.refund`, which returns the spend to the
        Remaining_Lease iff it is the same Budget_Generation (a cross-generation refund is dropped
        at the lease, the conservative direction).
        """
        self._quota.refund(cost, request_id=request_id)

    async def startup(self) -> str:
        """Seed the pool (NX), read the generation, acquire the first chunk. Returns a log line."""
        remaining_key = KEYS.budget_remaining(self._org_id)
        await self._client.set(remaining_key, self._settings.pool_tokens, nx=True)
        await self._client.set(KEYS.budget_generation(self._org_id), 0, nx=True)
        self._generation = await self._lease.read_generation()
        acquired = await self._lease.acquire(self._generation)
        pool = await self._client.get(remaining_key)
        return (
            f"boot: DEV PROFILE budget lease acquire={'ok' if acquired else 'EMPTY POOL'} "
            f"held={self._lease.remaining} chunk={self._lease.config.chunk} "
            f"generation={self._generation} pool[{remaining_key}]={_as_text(pool)}"
        )

    async def shutdown(self) -> None:
        """Return the unspent lease and close the client."""
        try:
            await self._lease.return_unspent()
        finally:
            await self._client.aclose()


def _as_text(value: object) -> str:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return str(value)


def _redis_client(url: str, *, timeout_s: float) -> Any:
    """A ``redis.asyncio`` client with bounded socket timeouts (contract-derived, no literal)."""
    import redis.asyncio as redis_asyncio

    return redis_asyncio.Redis.from_url(
        url, socket_timeout=timeout_s, socket_connect_timeout=timeout_s
    )


# --------------------------------------------------------------------------- #
# The per-worker served app.
# --------------------------------------------------------------------------- #


class DevServedApp:
    """Wrap the shipped ASGI app ONLY to drive lifespan-event lease startup/shutdown.

    It has no admission role: the GW15 lifecycle completes each admitted slot on exit, so the
    dev profile carries no per-request slot logic. A normal request is forwarded unchanged; only
    the ASGI ``lifespan`` channel is intercepted to acquire the lease at startup and return the
    unspent remainder at shutdown.
    """

    __slots__ = ("_budget", "_inner")

    def __init__(self, inner: _ASGIApp, *, budget: DevLeaseBudget) -> None:
        self._inner = inner
        self._budget = budget

    async def __call__(self, scope: _Scope, receive: _Receive, send: _Send) -> None:
        if scope.get("type") == "lifespan":
            await self._inner(scope, self._lifespan_receive(receive), send)
            return
        await self._inner(scope, receive, send)

    def _lifespan_receive(self, receive: _Receive) -> _Receive:
        budget = self._budget

        async def _receive() -> Mapping[str, object]:
            message = await receive()
            kind = message.get("type")
            if kind == "lifespan.startup":
                await _budget_startup(budget)
            elif kind == "lifespan.shutdown":
                await _budget_shutdown(budget)
            return message

        return _receive


async def _budget_startup(budget: DevLeaseBudget) -> None:
    """Acquire the first chunk; a store failure leaves the lease EMPTY (budget refuses)."""
    try:
        line = await budget.startup()
    except Exception as exc:  # noqa: BLE001 — fail closed: an empty lease refuses, never admits.
        line = (
            "boot: DEV PROFILE budget lease startup FAILED "
            f"({type(exc).__name__}: {exc}); requests refuse budget_unavailable until a refill "
            "succeeds"
        )
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


async def _budget_shutdown(budget: DevLeaseBudget) -> None:
    try:
        await budget.shutdown()
    except Exception as exc:  # noqa: BLE001 — shutdown is best-effort; the TTL reclaims the chunk.
        sys.stdout.write(f"dev: budget lease return_unspent failed ({type(exc).__name__})\n")
        sys.stdout.flush()
