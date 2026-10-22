"""DEV PROFILE admission + budget wiring and the per-worker app wrapper.

Wired ONLY when ``AMF_DEV_PROFILE=1`` (see :mod:`gateway_v2.edge.dev_profile`).

**Admission — the REAL GW19 controller.** :func:`build_dev_admission` builds a real
:class:`~gateway_v2.admit.admission.AdmissionController` from
:func:`~gateway_v2.admit.quota.derive_bounds` with ``q_safe`` taken from the environment (never a
default). The shipped GW15 lifecycle calls ``admit`` but NEVER ``complete`` (FOUND, NOT FIXED in
the lifecycle), so a bare controller would fill its bounded request queue after
``request_depth`` requests and shed every request thereafter. :class:`DevCompletingAdmission`
records the ids admitted inside one ASGI request (a per-instance ``ContextVar``) and
:class:`DevServedApp` calls ``controller.complete`` for each when that ASGI request returns —
so the queue bound is a real in-flight bound, released when the response finishes.

**Budget — the REAL GW06 façade over Redis, or an explicitly-named unlimited stand-in.**
:class:`DevLeaseBudget` composes the shipped :class:`~gateway_v2.admit.quota.QuotaComponent`
(``LocalGCRA`` + ``BudgetLease`` over ``redis.asyncio``). One honest adaptation is required and
labelled: GW06 sizes its Lease_Chunk in REQUESTS (``ceil(q_safe * target_p99)``), while the GW15
lifecycle passes a CHARACTER-count cost, so any prompt longer than the chunk could never be
admitted. The dev adapter therefore spends ONE unit per request. :class:`DevUnlimitedBudget` is
the alternative when no store is wanted; it admits everything and is logged as NOT enforced.

**The wrapper.** :class:`DevServedApp` wraps the shipped ``build_app`` callable: it drives the
lease's startup/shutdown on the ASGI lifespan events (acquire / return-unspent) and releases
admission slots per request. It forwards every message unchanged otherwise.
"""

from __future__ import annotations

import asyncio
import os
import random
import socket
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from gateway_v2.admit.admission import AdmissionController
from gateway_v2.admit.codel import CoDelParams
from gateway_v2.admit.gcra import GcraParams, LocalGCRA
from gateway_v2.admit.grant import Admitted, BudgetVerdict, ShedVerdict
from gateway_v2.admit.lease import BudgetLease, LeaseConfig
from gateway_v2.admit.metrics import AdmissionMetrics, QuotaMetrics
from gateway_v2.admit.quota import (
    AdmissionBounds,
    QuotaComponent,
    derive_bounds,
    derive_lease_chunk,
    derive_low_watermark,
)
from gateway_v2.admit.supervisor import WorkerSupervisor
from gateway_v2.edge.asgi_sse import ASGIReceive, ASGIScope, ASGISend
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.store_keys import KEYS

__all__ = (
    "DevCompletingAdmission",
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

#: The cost a request spends against the GW06 lease in the dev profile: ONE unit per request
#: (see the module docstring — GW06 sizes chunks in requests). A unit, not a capacity size.
_DEV_COST_PER_REQUEST = 1


# --------------------------------------------------------------------------- #
# Admission.
# --------------------------------------------------------------------------- #


class DevCompletingAdmission:
    """The real :class:`AdmissionController` + per-ASGI-request slot release (see module doc).

    ``admit`` delegates to the controller unchanged; an ``Admitted`` id is recorded in the
    current ASGI request's bucket so :class:`DevServedApp` can ``complete`` it when the response
    finishes. An admit outside any wrapped request (no bucket) is completed immediately, so a slot
    can never leak.
    """

    __slots__ = ("_admitted", "_bounds", "_controller")

    def __init__(self, controller: AdmissionController, bounds: AdmissionBounds) -> None:
        self._controller = controller
        self._bounds = bounds
        self._admitted: ContextVar[list[str] | None] = ContextVar(
            "amf_dev_admitted_ids", default=None
        )

    @property
    def controller(self) -> AdmissionController:
        return self._controller

    @property
    def bounds(self) -> AdmissionBounds:
        """The ``derive_bounds`` result the controller was built from (for the boot log)."""
        return self._bounds

    async def admit(
        self, owner_id: str, request_id: str, enqueued_at: float
    ) -> Admitted | ShedVerdict:
        verdict = await self._controller.admit(owner_id, request_id, enqueued_at)
        if isinstance(verdict, Admitted):
            bucket = self._admitted.get()
            if bucket is None:
                self._controller.complete(request_id)
            else:
                bucket.append(request_id)
        return verdict

    def begin(self) -> Token[list[str] | None]:
        """Open a bucket for one ASGI request."""
        return self._admitted.set([])

    def end(self, token: Token[list[str] | None]) -> None:
        """Complete every id admitted during the ASGI request, then close its bucket."""
        bucket = self._admitted.get() or []
        for request_id in bucket:
            self._controller.complete(request_id)
        self._admitted.reset(token)


def build_dev_admission(
    *,
    contract: ResourceContract,
    q_safe: float,
    audit_drain_rate_per_s: float,
    audit_bytes_per_record: int,
    drain_window_s: float,
) -> DevCompletingAdmission:
    """Build the real GW19 controller from ``derive_bounds`` (q_safe DECLARED via env).

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
    return DevCompletingAdmission(controller, bounds)


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


@dataclass(frozen=True, slots=True)
class LeaseSettings:
    """The declared inputs of the dev GW06 lease (all from the environment, none defaulted)."""

    redis_url: str
    pool_requests: int
    rate_per_s: float
    burst: int
    ttl_s: float


class DevLeaseBudget:
    """The REAL GW06 :class:`QuotaComponent` (GCRA + Redis ``BudgetLease``) for the dev org.

    ``evaluate`` spends ONE unit per request (module docstring). The pool key is seeded with
    ``pool_requests`` only if absent (``SET NX``), the generation key likewise with ``0``; the
    generation is read once at startup (there is no budget-config change feed in the dev profile).
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
        chunk = derive_lease_chunk(contract, q_safe=q_safe)
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
        del cost  # GW06 chunk is sized in requests; see the module docstring.
        return await self._quota.evaluate(_DEV_COST_PER_REQUEST, request_id=request_id)

    async def startup(self) -> str:
        """Seed the pool (NX), read the generation, acquire the first chunk. Returns a log line."""
        remaining_key = KEYS.budget_remaining(self._org_id)
        await self._client.set(remaining_key, self._settings.pool_requests, nx=True)
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
    """Wrap the shipped ASGI app: lifespan-driven lease startup/shutdown + admission release."""

    __slots__ = ("_admission", "_budget", "_inner")

    def __init__(
        self,
        inner: _ASGIApp,
        *,
        admission: DevCompletingAdmission | None,
        budget: DevLeaseBudget | None,
    ) -> None:
        self._inner = inner
        self._admission = admission
        self._budget = budget

    async def __call__(self, scope: _Scope, receive: _Receive, send: _Send) -> None:
        if scope.get("type") == "lifespan":
            await self._inner(scope, self._lifespan_receive(receive), send)
            return
        if self._admission is None:
            await self._inner(scope, receive, send)
            return
        token = self._admission.begin()
        try:
            await self._inner(scope, receive, send)
        finally:
            self._admission.end(token)

    def _lifespan_receive(self, receive: _Receive) -> _Receive:
        budget = self._budget

        async def _receive() -> Mapping[str, object]:
            message = await receive()
            if budget is None:
                return message
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
