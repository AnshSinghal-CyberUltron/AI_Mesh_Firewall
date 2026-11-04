"""GW29 notifier pump and ticker (design 5.6, 5.9). Nothing here can change a request outcome.

* :class:`NotifierPump`. ``emit(event)`` is one lock plus one bounded ``deque`` append and never
  awaits, so the request path pays O(1) (AC28). A full queue drops the OLDEST event and counts it.
  :class:`RerouteLimiter` suppresses repeated ``REROUTE`` events for one ``(model, fallback)`` pair
  inside the contract window; ``ISOLATE``, ``RECOVER`` and ``AUTO_ISOLATE_SKIPPED`` are never
  suppressed. A single drain task calls ``notifier.notify`` under ``asyncio.wait_for`` with the
  contract timeout. A timeout or any exception is counted and logged by class name only, and is NOT
  retried: the notifier contract is at-least-once at the sink, best-effort here.
* :class:`IncidentTicker`. One task per worker, sleeping ``risk_bucket_s()`` through an injected
  ``sleep`` and calling ``isolator.sweep()``. A failed sweep is counted and the loop continues.
  Enforcement does not depend on the ticker, only RECOVER events and probation do.
* :class:`LoggingNotifier` is the shipped default: one structured log line per event, no content.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Mapping
from types import MappingProxyType
from typing import Protocol

from gateway_v2.admit.incident_metrics import IncidentMetrics
from gateway_v2.audit.record import AuditRecord, incident_change
from gateway_v2.domain.incident import IncidentEvent, IncidentEventKind, Notifier

__all__ = ("IncidentTicker", "LoggingNotifier", "NotifierPump", "RerouteLimiter", "Sweeper")

LOG = logging.getLogger("amf.gateway.incident")

_NS_PER_S = 1_000_000_000
_GLOBAL_ORG = "global"
_CAUSE_BY_KIND: Mapping[IncidentEventKind, str] = MappingProxyType({
    IncidentEventKind.ISOLATE: "isolate",
    IncidentEventKind.RECOVER: "recover",
    IncidentEventKind.AUTO_ISOLATE_SKIPPED: "auto_skipped",
})


class LoggingNotifier:
    """One structured log line per event: kind, scope, model, fallback, reason, origin."""

    __slots__ = ()

    async def notify(self, event: IncidentEvent) -> None:
        LOG.info(
            "incident event kind=%s scope=%s model=%s fallback=%s reason=%s origin=%s",
            event.kind.value, event.scope, event.model, event.fallback or "-",
            event.reason, event.origin.value,
        )


class RerouteLimiter:
    """At most one ``REROUTE`` event per ``(model, fallback)`` per window; bounded by ``slots``."""

    __slots__ = ("_clock", "_lock", "_seen", "_slots", "_window_s")

    def __init__(self, *, window_s: float, slots: int, clock: Callable[[], float]) -> None:
        if window_s <= 0 or slots < 1:
            raise ValueError("a reroute limiter needs a positive window and at least one slot")
        self._window_s = window_s
        self._slots = slots
        self._clock = clock
        self._lock = threading.Lock()
        self._seen: OrderedDict[tuple[str, str | None], float] = OrderedDict()

    def allow(self, event: IncidentEvent) -> bool:
        if event.kind is not IncidentEventKind.REROUTE:
            return True
        key = (event.model, event.fallback)
        now = self._clock()
        with self._lock:
            last = self._seen.get(key)
            if last is not None and now - last < self._window_s:
                return False
            self._seen.pop(key, None)
            self._seen[key] = now
            while len(self._seen) > self._slots:
                self._seen.popitem(last=False)
            return True


class NotifierPump:
    """Bounded, lossy event queue with one background drain task."""

    def __init__(
        self,
        *,
        slots: int,
        notifier: Notifier,
        timeout_s: float,
        limiter: RerouteLimiter,
        metrics: IncidentMetrics,
        wall: Callable[[], float],
        audit: Callable[[AuditRecord], bool] | None = None,
    ) -> None:
        if slots < 1 or timeout_s <= 0:
            raise ValueError("a notifier pump needs positive slots and timeout")
        self._notifier = notifier
        self._timeout_s = timeout_s
        self._limiter = limiter
        self._metrics = metrics
        self._wall = wall
        self._audit = audit
        self._lock = threading.Lock()
        self._queue: deque[IncidentEvent] = deque(maxlen=slots)
        self._slots = slots
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None

    # --- request path ---------------------------------------------------------------------------
    def emit(self, event: IncidentEvent) -> None:
        """O(1), synchronous, never raises and never awaits."""
        try:
            if not self._limiter.allow(event):
                return
            with self._lock:
                if len(self._queue) >= self._slots:
                    self._metrics.observe_event_dropped()
                self._queue.append(event)
            self._signal()
        except Exception:  # noqa: BLE001 - an event that cannot be queued is counted, not raised.
            self._metrics.observe_event_dropped()

    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    def _signal(self) -> None:
        loop, wake = self._loop, self._wake
        if loop is None or wake is None:
            return
        with contextlib.suppress(RuntimeError):  # the loop is closing: the event stays queued
            loop.call_soon_threadsafe(wake.set)

    # --- drain ----------------------------------------------------------------------------------
    def _take(self) -> list[IncidentEvent]:
        with self._lock:
            events = list(self._queue)
            self._queue.clear()
            return events

    async def drain_once(self) -> int:
        """Deliver everything queued now. Returns how many events were attempted."""
        events = self._take()
        for event in events:
            self._record(event)
            await self._deliver(event)
        return len(events)

    async def _deliver(self, event: IncidentEvent) -> None:
        try:
            await asyncio.wait_for(self._notifier.notify(event), timeout=self._timeout_s)
        except Exception as exc:  # noqa: BLE001 - TimeoutError included: counted, never retried
            self._metrics.observe_notifier_failure()
            LOG.warning("incident notifier failed: %s", type(exc).__name__)

    def _record(self, event: IncidentEvent) -> None:
        """One audit record per isolation-change event (5.11); a failed emit changes nothing."""
        cause = _CAUSE_BY_KIND.get(event.kind)
        if cause is None or self._audit is None:
            return
        try:
            self._audit(incident_change(
                org_id=_GLOBAL_ORG, cause=cause, model=event.model,
                recorded_at_ns=int(self._wall() * _NS_PER_S),
                fallback=event.fallback, reason=event.reason,
            ))
        except Exception as exc:  # noqa: BLE001 - audit failure never changes the outcome
            LOG.warning("incident audit failed: %s", type(exc).__name__)

    async def _run(self, wake: asyncio.Event) -> None:
        while True:
            await wake.wait()
            wake.clear()
            await self.drain_once()

    def start(self) -> None:
        """Start the drain task on the running loop. Idempotent."""
        if self._task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._run(self._wake), name="amf-incident-pump")
        if self.pending():
            self._wake.set()

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


class Sweeper(Protocol):
    """What the ticker drives: the auto-isolator."""

    def sweep(self) -> None: ...


class IncidentTicker:
    """Calls ``sweep`` every period through an injected ``sleep``."""

    def __init__(
        self,
        *,
        sweeper: Sweeper,
        period_s: float,
        sleep: Callable[[float], Awaitable[None]],
        metrics: IncidentMetrics,
    ) -> None:
        if period_s <= 0:
            raise ValueError("a ticker needs a positive period")
        self._sweeper = sweeper
        self._period_s = period_s
        self._sleep = sleep
        self._metrics = metrics
        self._task: asyncio.Task[None] | None = None

    def tick(self) -> None:
        """One sweep. A failure is counted and logged by class name; the next tick still runs."""
        try:
            self._sweeper.sweep()
        except Exception as exc:  # noqa: BLE001 - the loop must survive a bad sweep
            self._metrics.observe_ticker_failure()
            LOG.warning("incident ticker sweep failed: %s", type(exc).__name__)

    async def run(self) -> None:
        while True:
            await self._sleep(self._period_s)
            self.tick()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run(), name="amf-incident-ticker")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
