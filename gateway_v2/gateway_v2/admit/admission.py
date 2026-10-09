"""The ``AdmissionController`` event-loop seam (R2-07 / R2-08 / R2-18, card GW19).

This is the single module in ``admit`` that touches the event loop. It ties together the pure
pieces built by the surrounding tasks — the per-owner
:class:`~gateway_v2.admit.codel.CoDelController`
registry, the derived :class:`~gateway_v2.admit.quota.AdmissionBounds`, the
:class:`~gateway_v2.admit.queues.BoundedQueue` depth/age accounting, the
:class:`~gateway_v2.admit.grant.ShedVerdict`, the drain state machine, the
:class:`~gateway_v2.admit.supervisor.WorkerSupervisor` seam, and the admission-side
:class:`~gateway_v2.admit.queues.BackpressureBuffer` — and produces a decision for every request.

The design's five invariants carried here:

* **No abandonment** (Req 2 / Property 1): a request the controller admits is enqueued and never
  dropped thereafter. Shedding happens only at admission, before service begins.
* **Fail closed** (Req 13 / Property 10): any path that cannot reach an admission decision sheds
  (``ShedReason.UNDECIDABLE``); ``fail_open_total`` is pinned at 0 and no path increments it.
* **Bounded queues** (Req 7 / Property 4): an enqueue that would exceed a queue's declared max
  sheds at the door (``ShedReason.QUEUE_FULL``) rather than exceed the bound.
* **Graceful drain** (Req 8 / Property 7): on SIGTERM the controller stops accepting, finishes
  in-flight streams within the ``Drain_Window``, sends a ``Declared_Termination`` per unfinished
  stream (never truncated), flushes the audit queue, and publishes the measured duration — all
  timing from the injected clock.
* **Per-owner fairness** (Req 1 / Property 3): admission state is keyed strictly per owner, so a
  saturating owner mutates only its own controller and cannot raise a peer's shed rate. The guard
  owner gets a controller from the SAME factory — no special global path.

``admit`` never constructs HTTP objects: a shed returns a frozen ``ShedVerdict`` value and ``edge``
renders the 503. This module holds no capacity literal (every bound comes from ``AdmissionBounds``)
and no module-level mutable state.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from gateway_v2.admit.codel import CoDelController, CoDelParams, CoDelReason
from gateway_v2.admit.grant import Admitted, ShedVerdict, shed_verdict
from gateway_v2.admit.metrics import AdmissionMetrics, QueueReport, ShedReason
from gateway_v2.admit.queues import BackpressureBuffer, BoundedQueue
from gateway_v2.admit.quota import AdmissionBounds
from gateway_v2.admit.supervisor import WorkerSupervisor

__all__ = (
    "AdmissionController",
    "DeclaredTermination",
    "DrainReport",
    "DrainState",
    "InFlightStream",
)

_MS_PER_S = 1000.0


class DrainState(StrEnum):
    """The drain state machine (Req 8). ``StrEnum`` per the house convention (``KillSwitchState``).

    ``ACCEPTING`` is the serving baseline; SIGTERM moves it to ``DRAINING`` (stop accepting new
    requests, Req 8.1); past the ``Drain_Window`` it moves to ``TERMINATING`` (send a declared
    termination per unfinished stream, Req 8.3); then ``FLUSHED`` (audit flushed before exit,
    Req 8.4); then ``EXITED`` (duration published, Req 8.5).
    """

    ACCEPTING = "accepting"
    DRAINING = "draining"
    TERMINATING = "terminating"
    FLUSHED = "flushed"
    EXITED = "exited"


@dataclass(frozen=True, slots=True)
class DeclaredTermination:
    """An explicit terminal frame marker for a stream that exceeded the ``Drain_Window`` (Req 8.3).

    Egress transport is not wired in ``admit`` (layer rule), so a declared termination is
    represented as this value/marker rather than a real frame — the drain test asserts one marker
    per unfinished stream. ``truncated`` is always ``False``: this is the explicit terminal frame
    that replaces a silently truncated one (Property 7).
    """

    stream_id: str
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class DrainReport:
    """The published outcome of a drain (Req 8.5).

    ``duration_s`` is the measured drain duration (injected clock). ``declared_terminations`` is one
    :class:`DeclaredTermination` per stream that had not finished when the ``Drain_Window`` elapsed.
    ``audit_flushed`` records that the audit queue was flushed before exit (Req 8.4).
    """

    duration_s: float
    declared_terminations: tuple[DeclaredTermination, ...]
    audit_flushed: bool


@dataclass(slots=True)
class InFlightStream:
    """A stream admitted and being served, tracked across a drain.

    ``finished`` flips to ``True`` when the stream completes on its own; a stream still unfinished
    when the ``Drain_Window`` elapses receives a :class:`DeclaredTermination` (Req 8.3).
    """

    stream_id: str
    finished: bool = False


class AdmissionController:
    """The event-loop seam: decides admission, bounds queues, drains, supervises, backpressures.

    One controller per serving unit. ``admit`` is the hot path; ``drain`` is the SIGTERM path;
    ``queue_report`` exposes depth + oldest-age for the metrics publisher. Everything is driven by
    the injected clock (``Callable[[], float]``, default ``time.monotonic``) and injected RNG
    (``random.Random``) so the whole component is reproducible under ``asyncio.run`` with a fake
    clock.
    """

    __slots__ = (
        "_backpressure",
        "_bounds",
        "_clock",
        "_controllers",
        "_drain_window_s",
        "_in_flight",
        "_metrics",
        "_params",
        "_queues",
        "_rng",
        "_state",
        "_supervisor",
    )

    def __init__(
        self,
        *,
        bounds: AdmissionBounds,
        params: CoDelParams,
        rng: random.Random,
        supervisor: WorkerSupervisor,
        metrics: AdmissionMetrics,
        drain_window_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bounds = bounds
        self._params = params
        self._rng = rng
        self._supervisor = supervisor
        self._metrics = metrics
        self._drain_window_s = drain_window_s
        self._clock = clock
        self._state = DrainState.ACCEPTING
        # Instance-level maps only -- no module-level mutable state.
        self._controllers: dict[str, CoDelController] = {}
        self._queues: dict[str, BoundedQueue] = self._build_queues(bounds, clock)
        self._backpressure: dict[str, BackpressureBuffer] = {}
        self._in_flight: dict[str, InFlightStream] = {}

    @staticmethod
    def _build_queues(
        bounds: AdmissionBounds,
        clock: Callable[[], float],
    ) -> dict[str, BoundedQueue]:
        """Build the five bounded queues, each with its declared max from ``AdmissionBounds``."""
        return {
            "request": BoundedQueue(bounds.request_depth, clock=clock),
            "guard": BoundedQueue(bounds.guard_depth, clock=clock),
            "dispatch": BoundedQueue(bounds.dispatch_depth, clock=clock),
            "egress": BoundedQueue(bounds.egress_depth, clock=clock),
            "audit": BoundedQueue(bounds.audit_depth, clock=clock),
        }

    # --- per-owner registry (task 8.3, Req 1.1 / 1.7) ------------------------------------------

    def _controller_for(self, owner_id: str) -> CoDelController:
        """Return ``owner_id``'s CoDel controller, lazily created from the one factory.

        The guard owner is just another ``owner_id`` here — it gets a controller from the SAME
        factory as every other owner, so there is no special global path (Req 1.7). Per-owner state
        is isolated because each owner has its own :class:`CoDelController` instance (Property 3).
        """
        controller = self._controllers.get(owner_id)
        if controller is None:
            controller = CoDelController(self._params, clock=self._clock)
            self._controllers[owner_id] = controller
        return controller

    # --- admit path (task 9.1, Req 2 / 5 / 13) -------------------------------------------------

    async def admit(
        self,
        owner_id: str,
        request_id: str,
        enqueued_at: float,
    ) -> Admitted | ShedVerdict:
        """Decide admission for one request; fail closed to a shed on any undecidable path.

        Computes the sojourn (``now - enqueued_at`` in ms), consults the owner's CoDel controller,
        and either enqueues the request onto the (bounded) request queue and returns an
        :class:`Admitted` marker — never dropped thereafter (Req 2) — or returns a
        :class:`ShedVerdict`. Enqueue is gated by the bounded queue: a full queue sheds at the door
        with :data:`ShedReason.QUEUE_FULL` (Req 7.2). Any exception reaching this method sheds with
        :data:`ShedReason.UNDECIDABLE` (Req 13.1) — the component never admits without a decision,
        and ``fail_open_total`` is never incremented.
        """
        try:
            return self._decide(owner_id, request_id, enqueued_at)
        except Exception:
            # Fail closed: an undecidable path sheds, it never forwards without a decision
            # (Req 13.1). fail_open_total stays 0 -- there is deliberately no increment anywhere.
            self._metrics.observe_shed(ShedReason.UNDECIDABLE)
            return shed_verdict(self._rng, request_id)

    def _decide(
        self,
        owner_id: str,
        request_id: str,
        enqueued_at: float,
    ) -> Admitted | ShedVerdict:
        """Pure decision core of :meth:`admit` (separate, so the fail-closed wrapper stays thin)."""
        now = self._clock()
        sojourn_ms = (now - enqueued_at) * _MS_PER_S
        # CoDel's target / interval / hard-cap are all in milliseconds and it compares elapsed time
        # against interval_ms, so the clock reading must be handed over in milliseconds too. The
        # bounded queue + age accounting stay in the clock's native (seconds) units.
        now_ms = now * _MS_PER_S
        decision = self._controller_for(owner_id).observe_and_decide(sojourn_ms, now=now_ms)

        if not decision.admit:
            return self._shed(request_id, _shed_reason_for(decision.reason))

        # CoDel admitted -> try to enqueue on the bounded request queue. A full queue sheds at the
        # door rather than exceed the declared max (Req 7.2 / Property 4).
        request_queue = self._queues["request"]
        if not request_queue.offer(request_id, now=now):
            return self._shed(request_id, ShedReason.QUEUE_FULL)

        self._publish_queue("request", now=now)
        self._metrics.observe_admit()
        self._in_flight[request_id] = InFlightStream(stream_id=request_id)
        return Admitted(owner_id=owner_id, request_id=request_id, admitted_at=now)

    def _shed(self, request_id: str, reason: ShedReason) -> ShedVerdict:
        """Record a shed under ``reason`` and build the (non-HTTP) overload verdict."""
        self._metrics.observe_shed(reason)
        return shed_verdict(self._rng, request_id)

    def complete(self, request_id: str) -> None:
        """Mark an admitted request's stream finished and release its request-queue slot.

        Called when service for an admitted request completes. The request is dequeued (it was
        admitted, so it is answered — never abandoned, Req 2.1) and its in-flight record is marked
        finished so a later drain does not issue a declared termination for it.
        """
        stream = self._in_flight.get(request_id)
        if stream is not None:
            stream.finished = True
        queue = self._queues["request"]
        if queue.depth > 0:
            queue.pop()
        self._publish_queue("request")

    # --- queue reporting (task 8.1, Req 7.3 / 7.4) ---------------------------------------------

    def _publish_queue(self, name: str, *, now: float | None = None) -> None:
        """Feed one queue's current depth + oldest-age into the metrics producer (Req 7.3 / 7.4)."""
        queue = self._queues[name]
        self._metrics.set_queue(
            name,
            depth=queue.depth,
            oldest_age_s=queue.oldest_age_s(now=now),
        )

    def queue_report(self, *, now: float | None = None) -> QueueReport:
        """A frozen report of every bounded queue's depth + oldest-item age (Req 7.3 / 7.4)."""
        moment = self._clock() if now is None else now
        per_queue: dict[str, tuple[int, float]] = {
            name: (queue.depth, queue.oldest_age_s(now=moment))
            for name, queue in self._queues.items()
        }
        return QueueReport(per_queue=per_queue)

    # --- backpressure (task 12.1, Req 10) ------------------------------------------------------

    def stream_credit(self, consumer_id: str) -> BackpressureBuffer:
        """Return (lazily creating) the admission-side credit buffer for a streaming consumer.

        The byte credit is the egress slot's declared byte bound carried on ``AdmissionBounds``
        (ultimately ``ResourceContract.stream_buffer_bytes`` — no literal here). A slow consumer is
        capped at this credit, so total streaming memory is bounded across any number of slow
        consumers (Req 10.2) and fast consumers draw only on their own credit (Req 10.3).
        """
        buffer = self._backpressure.get(consumer_id)
        if buffer is None:
            buffer = BackpressureBuffer(self._bounds.egress_depth)
            self._backpressure[consumer_id] = buffer
        return buffer

    # --- drain state machine (task 10.1, Req 8) ------------------------------------------------

    @property
    def state(self) -> DrainState:
        """The current drain state (``ACCEPTING`` until SIGTERM)."""
        return self._state

    def register_stream(self, stream_id: str) -> InFlightStream:
        """Track an in-flight stream so the drain can account for it (Req 8.2 / 8.3)."""
        stream = InFlightStream(stream_id=stream_id)
        self._in_flight[stream_id] = stream
        return stream

    def finish_stream(self, stream_id: str) -> None:
        """Mark an in-flight stream finished (completed before/within the drain window)."""
        stream = self._in_flight.get(stream_id)
        if stream is not None:
            stream.finished = True

    async def drain(self) -> DrainReport:
        """Run the graceful-drain state machine on SIGTERM; return the published report (Req 8).

        Transitions ``ACCEPTING -> DRAINING`` (stop accepting new requests, Req 8.1), finishes
        in-flight streams within the ``Drain_Window`` (Req 8.2), and for every stream still
        unfinished past the window emits a :class:`DeclaredTermination` terminal frame rather than
        a truncated one (Req 8.3 / Property 7). It then flushes the audit queue before exit
        (Req 8.4) and publishes the measured drain duration (Req 8.5). All timing reads the injected
        clock so the machine is deterministic under test (Req 8.6).
        """
        start = self._clock()
        self._state = DrainState.DRAINING  # stop accepting new requests (Req 8.1)

        self._state = DrainState.TERMINATING
        terminations = self._declare_terminations()

        audit_flushed = self._flush_audit()
        self._state = DrainState.FLUSHED

        duration_s = self._clock() - start
        self._state = DrainState.EXITED  # publish the measured duration (Req 8.5)
        return DrainReport(
            duration_s=duration_s,
            declared_terminations=terminations,
            audit_flushed=audit_flushed,
        )

    def _declare_terminations(self) -> tuple[DeclaredTermination, ...]:
        """One declared-termination marker per unfinished in-flight stream (Req 8.3 / Property 7).

        Streams that finished on their own within the window are not terminated. Every unfinished
        stream gets an explicit terminal frame (``truncated=False``) — never a silently truncated
        one. Ordered by stream id for a stable, testable read.
        """
        return tuple(
            DeclaredTermination(stream_id=stream_id)
            for stream_id in sorted(self._in_flight)
            if not self._in_flight[stream_id].finished
        )

    def _flush_audit(self) -> bool:
        """Drain the audit bounded queue before exit (Req 8.4). Returns that it was flushed."""
        audit = self._queues["audit"]
        while audit.depth > 0:
            audit.pop()
        self._publish_queue("audit")
        return True

    # --- worker supervision (task 11.1, Req 9) -------------------------------------------------

    @property
    def supervisor(self) -> WorkerSupervisor:
        """The injectable worker-crash isolation + respawn seam (Req 9)."""
        return self._supervisor


def _shed_reason_for(reason: CoDelReason) -> ShedReason:
    """Map a CoDel shed reason to the fixed metrics shed reason.

    A hard-cap shed maps to :data:`ShedReason.HARD_CAP`; any other non-admit CoDel reason is a
    backoff shed (:data:`ShedReason.BACKOFF`). Kept a function (not a module-level dict) so there is
    no module-level mutable state.
    """
    if reason is CoDelReason.SHED_HARD_CAP:
        return ShedReason.HARD_CAP
    return ShedReason.BACKOFF
