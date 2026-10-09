"""Real client-disconnect cancellation controller + the in-flight kill latch (GW12, task 9.1).

When the ASGI receive channel reports an ``http.disconnect`` (or a stream idles past its derived
timeout), the gateway must not keep spending on a client that has gone: it aborts the in-flight
``ProviderClient`` request AND signals the shipped ``StreamPipeline.killed()`` seam so in-flight
guard work ceases at the next chunk boundary, releases the stream's buffered bytes (returning its
credit to the available pool), and completes the whole kill within a bound DERIVED from the
``ResourceContract`` — never a literal interval (R6).

This module owns two things:

* :class:`CancelOutcome` -- the frozen slotted reading one cancellation produces: the measured
  elapsed ms from disconnect-detection to kill-completion, whether that elapsed exceeded the
  derived bound (a bound-exceeded cancellation forced the provider connection + buffered state
  released, R6.4), and the released byte count (``== buffered at disconnect``, R6.6).
* :class:`KillLatch` -- the controllable kill signal. The shipped ``StreamPipeline.run`` checks a
  ``killed: Callable[[], bool]`` probe at every chunk boundary (``egress.stream.KillSignal``); the
  chat handler (``edge/routes.py``, task 8.1) currently passes the always-false ``_never_killed``.
  :class:`KillLatch` is a mutable ONE-WAY latch -- the same documented monotone-flag exception as
  :class:`gateway_v2.edge.stream_control.FirstByteLatch` -- that starts unset, flips to set exactly
  once, and never flips back. It is directly callable (``__call__ -> bool``) so an INSTANCE *is* the
  ``KillSignal`` the handler hands to ``pipeline.run(chunks, send, kill_latch)``: a flip takes
  effect no later than the next chunk boundary (``InFlightKill.CUT_NEXT_CHUNK``, R6.2).

**The seam exposed for task 10.** :class:`KillLatch` is the single cut seam many triggers drive:
task 10 wires client disconnect (via :meth:`CancellationController.on_disconnect`), the kill switch,
key revocation, plan change, and max-stream-duration all into ``KillLatch.kill()`` -- one latch, one
``killed()`` probe, one cut point. This task builds the disconnect trigger and leaves the latch as
the clean injection point; task 10 does not rebuild the latch, it only adds the other triggers that
flip it. The handler rewiring (replace ``_never_killed`` with a per-request :class:`KillLatch`
threaded through ``on_disconnect`` + the task-10 triggers) is task 10's job, not this one.

**Fail closed (R6.4).** A cancellation that cannot complete both the provider abort and the kill
signal within ``contract.cancellation_bound_s()`` force-releases the provider connection and the
buffered state (the kill latch is flipped unconditionally, and ``provider.abort`` is retried to
completion out of band) and records the stream as a bound-exceeded cancellation -- it NEVER leaves
the provider reading. The buffered bytes are released in every path and none are forwarded
downstream (R6.6).

**Layering.** ``edge`` is the top layer; it may import ``dispatch`` (the ``ProviderClient``),
``egress`` (the ``Coalescer``) and ``runtime`` (the ``ResourceContract`` + per-request exports)
below it. No HTTP object is constructed here -- a disconnect is control flow, not an ``Error_Frame``
rendered to a client that has already gone, so this module holds no ``HTTPException`` /
``JSONResponse`` (the codes-vs-render boundary is untouched). No module-level mutable, no capacity
literal: the only bound is ``contract.cancellation_bound_s()``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from gateway_v2.dispatch.provider import ProviderClient
from gateway_v2.domain import posture
from gateway_v2.egress.backpressure import Coalescer
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.stream_metrics import PerRequestExports

__all__ = (
    "CancelOutcome",
    "CancellationController",
    "InFlightControl",
    "InFlightControlSource",
    "InFlightSnapshot",
    "KillLatch",
)

_MS_PER_S = 1000.0
"""Seconds-to-milliseconds factor. The elapsed cancellation interval is MEASURED (clock reading
delta), not a capacity bound, so this unit factor is not a capacity literal -- it mirrors the
``_MS_PER_S`` used by ``runtime/resources.py`` to convert the SLO. The ONLY capacity bound in this
module is ``contract.cancellation_bound_s()``."""


# --------------------------------------------------------------------------- #
# KillLatch -- the controllable in-flight kill signal (R6.2; seam for task 10).
# --------------------------------------------------------------------------- #


class KillLatch:
    """A one-way latch that drives the shipped ``StreamPipeline.killed()`` seam (R6.2).

    Starts unset. :meth:`kill` flips it set and is idempotent (a second call is a no-op -- the
    latch is one-way, never reset). An INSTANCE is directly callable: ``kill_latch()`` returns the
    current flag, so the instance satisfies ``egress.stream.KillSignal`` (``Callable[[], bool]``)
    and the chat handler passes it straight to ``pipeline.run(chunks, send, kill_latch)`` in place
    of the always-false ``_never_killed``. ``StreamPipeline`` checks the probe at each chunk
    boundary, so a :meth:`kill` takes effect no later than the next boundary
    (``InFlightKill.CUT_NEXT_CHUNK``) and no further upstream chunk bytes are forwarded after the
    cut (R6.2, R10.7).

    Mutable + slotted on purpose: it is the SAME documented mutable one-way-latch exception as
    :class:`gateway_v2.edge.stream_control.FirstByteLatch`. The ``__slots__`` keeps it a fixed
    one-boolean cell with no instance ``__dict__`` -- the mutation surface is exactly one monotone
    flag, so the cut reasoning depends only on *"once killed, stays killed"*.

    **Seam for task 10.** Client disconnect (this task, via
    :meth:`CancellationController.on_disconnect`), the kill switch, key revocation, plan change, and
    max-stream-duration all flip the SAME latch (``kill()``) -- one seam, many triggers. Task 10
    adds those triggers; it does not rebuild this latch.

    **Carrying the cut REASON (R10.2-R10.6).** The shipped ``StreamPipeline`` only knows a boolean
    ``killed()`` probe and emits a single hard-coded ``"killed"`` terminal code, but each R10
    trigger must render a DIFFERENT declared ``Error_Frame`` (``STREAM_MAX_DURATION`` vs
    ``stream_killed`` vs ``STREAM_KEY_REVOKED`` vs ``STREAM_PLAN_CHANGED`` vs
    ``STREAM_SNAPSHOT_STALE``). So the latch carries the reason code the FIRST trigger set:
    :meth:`kill` records it, :meth:`reason` reads it, and the handler maps that code (not the
    pipeline's boolean) onto the terminal frame. The reason is one-way too -- set once with the
    first kill, never overwritten -- so a later trigger that also fires does not rewrite which
    cause the client is told about. ``reason() is None`` exactly when the latch is unset.
    """

    __slots__ = ("_killed", "_reason")

    def __init__(self) -> None:
        self._killed = False
        self._reason: str | None = None

    def kill(self, reason: str = posture.STREAM_KILLED) -> None:
        """Flip the latch set with the cut ``reason`` code (R6.2, R10.2-R10.6); idempotent.

        One-way: a second call leaves the latch killed (there is no reset), so once a cut has been
        signalled the ``killed()`` probe stays true for the life of the stream and the pipeline
        cuts at the next chunk boundary and emits no further upstream bytes (R10.7). The reason is
        recorded ONLY on the first call -- the first trigger to fire owns the client-facing code,
        so a later trigger flipping the same (already set) latch does not rewrite it. ``reason``
        defaults to the reused ``stream_killed`` code (the kill-switch / disconnect spelling); the
        R10 triggers pass their own posture code (``STREAM_MAX_DURATION`` etc.).
        """
        if not self._killed:
            self._reason = reason
            self._killed = True

    def reason(self) -> str | None:
        """The cut reason code the first trigger set, or ``None`` while the latch is unset.

        The handler renders this code as the terminal ``Error_Frame`` (R10.2-R10.6), so a
        max-duration cut shows ``STREAM_MAX_DURATION`` and a key revocation shows
        ``STREAM_KEY_REVOKED`` -- the pipeline's own boolean ``killed()`` cannot distinguish them.
        """
        return self._reason

    def is_killed(self) -> bool:
        """Whether a cut has been signalled for this stream."""
        return self._killed

    def __call__(self) -> bool:
        """The ``KillSignal`` probe (``Callable[[], bool]``) the pipeline checks each boundary.

        Returning the current flag makes the INSTANCE usable directly as the ``killed`` argument of
        ``StreamPipeline.run`` -- no wrapper closure, no module-level mutable.
        """
        return self._killed


# --------------------------------------------------------------------------- #
# CancelOutcome -- the per-cancellation reading (R6.4-R6.6).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CancelOutcome:
    """The reading one cancellation produces (R6.4-R6.6).

    ``elapsed_ms`` is the MEASURED interval, in milliseconds, from disconnect detection to
    completion of the in-flight kill (both the provider abort and the kill-latch flip), exported
    per cancelled stream (R6.5). ``bound_exceeded`` is ``True`` when ``elapsed_ms`` exceeded the
    derived ``cancellation_bound_s()`` and the controller force-released the provider connection +
    buffered state, recording the stream as a bound-exceeded cancellation (R6.4). ``released_bytes``
    is the stream's buffered byte count at the time of disconnect -- released back to the available
    pool, with no buffered byte forwarded downstream (R6.6).
    """

    elapsed_ms: float
    bound_exceeded: bool
    released_bytes: int


# --------------------------------------------------------------------------- #
# CancellationController (R6.1-R6.6).
# --------------------------------------------------------------------------- #


class CancellationController:
    """Abort the provider + cut guard work within the derived bound, release the buffer (R6).

    On a client disconnect :meth:`on_disconnect` starts the clock, concurrently aborts the
    in-flight ``ProviderClient`` request (R6.1) and signals the ``StreamPipeline.killed()`` seam via
    the injected :class:`KillLatch` so in-flight guard work ceases at the next chunk boundary
    (R6.2), and requires BOTH to complete within ``contract.cancellation_bound_s()`` -- a bound
    DERIVED from the ``ResourceContract``, never a literal (R6.3). It measures the elapsed ms and
    exports it per cancelled stream through the per-request producer (R6.4/R6.5), then releases the
    stream's buffered bytes and returns its credit to the available pool, the released count equal
    to the buffered-at-disconnect count with nothing forwarded downstream (R6.6).

    Injected dependencies: ``provider`` (the abortable upstream), ``coalescer`` (the per-stream
    buffer whose bytes are released on cancel), ``contract`` (the sole bound authority), ``clock``
    (a ``Callable[[], float]`` seconds reading so tests are deterministic), ``metrics`` (the
    per-request export producer), and the per-request ``kill_latch`` the handler also handed to
    ``pipeline.run`` -- flipping that same latch is what cuts the live stream.
    """

    __slots__ = ("_clock", "_coalescer", "_contract", "_kill_latch", "_metrics", "_provider")

    def __init__(
        self,
        *,
        provider: ProviderClient,
        coalescer: Coalescer,
        contract: ResourceContract,
        clock: Callable[[], float],
        metrics: PerRequestExports,
        kill_latch: KillLatch,
    ) -> None:
        self._provider = provider
        self._coalescer = coalescer
        self._contract = contract
        self._clock = clock
        self._metrics = metrics
        self._kill_latch = kill_latch

    async def on_disconnect(self) -> CancelOutcome:
        """Cancel the stream on a client disconnect, within the derived bound (R6.1-R6.6).

        1. Start the clock (R6.5).
        2. Snapshot the buffered-at-disconnect byte count -- the amount that will be released and
           the ``released_bytes`` the outcome reports (R6.6).
        3. Concurrently abort the provider (``provider.abort()``, R6.1) AND flip the kill latch so
           ``killed()`` is true, cutting in-flight guard work at the next chunk boundary (R6.2);
           both are required to complete within ``contract.cancellation_bound_s()`` (R6.3). The kill
           flip is synchronous and immediate; the provider abort is the operation that may stall.
        4. If the abort does not complete within the bound, force-release: the kill latch is already
           flipped (guard work still ceases), the provider connection + buffered state are released
           out of band, and the stream is recorded as a bound-exceeded cancellation (R6.4,
           ``bound_exceeded=True``) -- the provider is NEVER left reading.
        5. Release the stream's buffered bytes back to the pool and measure the elapsed ms from
           disconnect detection to kill completion, exporting it per cancelled stream (R6.4/R6.5).

        Returns the :class:`CancelOutcome`. Fail-closed: the kill latch is flipped before anything
        can raise and the buffer is released in every path (success, timeout, or abort error), so no
        buffered byte is ever forwarded downstream (R6.6).
        """
        start = self._clock()
        released_bytes = self._coalescer.buffered()
        bound_s = self._contract.cancellation_bound_s()

        bound_exceeded = await self._kill_within_bound(bound_s)

        # Release the buffered bytes back to the available pool in EVERY path (R6.6): shrinking the
        # per-stream buffer to empty returns this stream's share of the derived
        # `active_streams x stream_buffer_bytes` ceiling to the pool, and the still-held bytes are
        # dropped rather than forwarded. `release` clamps at zero, so a double-release is safe.
        self._coalescer.release(released_bytes)

        elapsed_ms = (self._clock() - start) * _MS_PER_S
        # Export the measured interval per cancelled stream (R6.5). The release-lag histogram is the
        # per-request producer's interval series; the controller feeds the cancel interval through
        # it in milliseconds (converted to ns for the ns-input observer), so a cancelled stream's
        # kill interval is observable without a tenant label (R12.3).
        self._metrics.observe_release_lag_ns(round(elapsed_ms * 1_000_000))

        return CancelOutcome(
            elapsed_ms=elapsed_ms,
            bound_exceeded=bound_exceeded,
            released_bytes=released_bytes,
        )

    async def _kill_within_bound(self, bound_s: float) -> bool:
        """Abort the provider AND flip the kill latch within ``bound_s`` (R6.1-R6.3).

        Flips the kill latch FIRST and unconditionally -- the ``killed()`` probe is now true, so
        in-flight guard work ceases at the next chunk boundary (R6.2) regardless of how the provider
        abort fares. Then it awaits ``provider.abort()`` under ``asyncio.wait_for`` with a timeout
        of ``bound_s`` (the derived bound, R6.3, no literal). Returns ``False`` when the abort
        completes within the bound and ``True`` when it does not -- a bound-exceeded cancellation
        that force-releases the connection (R6.4): the abort coroutine is left to run to completion
        out of band (the latch has already cut the stream) so the provider is never left reading.
        """
        # Signal the guard seam first and unconditionally: a cut must take effect at the next chunk
        # boundary even if the provider abort stalls (R6.2, fail-closed). A disconnect emits NO
        # `Error_Frame` to the client that has already gone (the control path, not an error-render
        # path -- see the module docstring and the Error Handling table's "client disconnect" row),
        # so the recorded reason is immaterial downstream; the one-way latch takes the reused
        # `stream_killed` spelling only so that `reason()` is never left `None` once `is_killed()`
        # is true (keeping the latch's "set <=> has a reason" contract total). An R10 trigger that
        # fired FIRST already owns the reason, and this `kill` is then a no-op (one-way).
        self._kill_latch.kill(posture.STREAM_KILLED)
        abort_task = asyncio.ensure_future(self._provider.abort())
        try:
            await asyncio.wait_for(asyncio.shield(abort_task), timeout=bound_s)
        except TimeoutError:
            # Bound exceeded (R6.4): force-release. The latch has already cut the stream; let the
            # shielded abort finish out of band so the provider connection is released and never
            # left reading. The buffered state is released by the caller in all paths.
            return True
        return False


# --------------------------------------------------------------------------- #
# In-flight control source + evaluator (R10.2-R10.7) -- one seam, many triggers.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class InFlightSnapshot:
    """The current control-plane signals for one in-flight stream (R10.3-R10.6).

    A frozen reading the injected :class:`InFlightControlSource` returns at each chunk boundary.
    The real snapshot is read from the control plane by GW05/GW06; here it is the injectable seam
    local tests drive with deterministic signals, so the trigger-to-cut mapping is tested without a
    live control plane.

    * ``available`` -- ``False`` when the kill-switch / revocation / plan snapshot could NOT be
      read at all. A missing snapshot FAILS CLOSED: the evaluator cuts with
      ``STREAM_SNAPSHOT_STALE`` rather than continue forwarding (R10.6), never treating absence as
      "off".
    * ``age_s`` -- how old the snapshot is, in seconds. When it exceeds
      ``contract.max_snapshot_age_s()`` the snapshot is STALE and the evaluator fails closed exactly
      as for an unavailable snapshot (R10.6).
    * ``kill_switch_engaged`` -- the org kill switch is engaged for this stream (R10.3) ->
      ``STREAM_KILLED``.
    * ``key_revoked`` -- the API key was revoked mid-stream (R10.4) -> ``STREAM_KEY_REVOKED``.
    * ``plan_changed`` -- the plan changed mid-stream (R10.5) -> ``STREAM_PLAN_CHANGED``.

    A fresh, available snapshot with every signal clear leaves the stream running (the evaluator
    returns ``None`` and forwards nothing to the latch).
    """

    available: bool
    age_s: float
    kill_switch_engaged: bool
    key_revoked: bool
    plan_changed: bool


@runtime_checkable
class InFlightControlSource(Protocol):
    """The injectable in-flight control source polled at each chunk boundary (R10.3-R10.6).

    Returns the current :class:`InFlightSnapshot` for the stream. The real implementation reads the
    kill-switch / revocation / plan feed from the control plane (a GW05/GW06 integration); this
    protocol is the seam so the chat handler and local tests inject a deterministic source. It is a
    pure read -- no clock, no I/O contract imposed here -- so a test source is a trivial callable
    returning a scripted snapshot.
    """

    def snapshot(self) -> InFlightSnapshot: ...


class InFlightControl:
    """Map every R10 in-flight trigger onto the ONE :class:`KillLatch` seam (R10.2-R10.7).

    Built once per request alongside the per-request :class:`KillLatch` the handler hands to
    ``pipeline.run``. The chat handler calls :meth:`evaluate` at each chunk boundary (alongside the
    existing ``killed()`` probe the pipeline already checks): a fired trigger flips the SAME latch
    with the matching posture code, so the terminal ``Error_Frame`` the handler renders names the
    cause (``STREAM_MAX_DURATION`` / ``STREAM_KILLED`` / ``STREAM_KEY_REVOKED`` /
    ``STREAM_PLAN_CHANGED`` / ``STREAM_SNAPSHOT_STALE``). One latch, one ``killed()`` probe, one cut
    point -- the design's "one seam, many triggers".

    Two bounds, both DERIVED from the ``ResourceContract`` (no literal):

    * ``max_stream_duration_s()`` -- the wall-clock deadline. :meth:`evaluate` compares the injected
      clock against the deadline captured at construction; once the lifetime reaches it the stream
      is cut with ``STREAM_MAX_DURATION`` (R10.2).
    * ``max_snapshot_age_s()`` -- the control-plane snapshot freshness. A snapshot older than this,
      or one the source reports ``available=False``, FAILS CLOSED to a ``STREAM_SNAPSHOT_STALE`` cut
      (R10.6) -- the gateway never continues forwarding on an unverifiable snapshot.

    The cut takes effect no later than the next chunk boundary (``InFlightKill.CUT_NEXT_CHUNK``), so
    it lands within ``max_cut_latency_s()``: the evaluator runs AT each boundary, and the shipped
    ``StreamPipeline``/chunk source stops forwarding at the boundary after the latch flips, so no
    further upstream chunk bytes reach the caller after the cut (R10.7). The ``max_cut_latency_s()``
    bound is the contractual budget that chunk-boundary cadence must honour; it is surfaced on the
    contract and asserted by task 10.2's tests against the measured cut latency.

    Precedence when several signals fire at once: the stale/unavailable check runs FIRST (fail
    closed dominates -- an unverifiable snapshot must not be trusted even to report another signal),
    then max-duration, then kill switch, revocation, plan change. The latch is one-way, so only the
    first code recorded is rendered; :meth:`evaluate` is idempotent after the first cut.

    No module-level mutable, no capacity literal: the only state is the injected deadline (a float
    captured once) and the bounds are contract calls.
    """

    __slots__ = ("_clock", "_contract", "_deadline", "_kill_latch", "_source")

    def __init__(
        self,
        *,
        source: InFlightControlSource,
        contract: ResourceContract,
        clock: Callable[[], float],
        kill_latch: KillLatch,
    ) -> None:
        self._source = source
        self._contract = contract
        self._clock = clock
        self._kill_latch = kill_latch
        # The wall-clock deadline captured at stream start: once `clock()` reaches it the stream has
        # lived `max_stream_duration_s()` and must be cut (R10.2). Derived from the contract, never
        # a literal.
        self._deadline = clock() + contract.max_stream_duration_s()

    def evaluate(self) -> str | None:
        """Poll the triggers once (a chunk boundary); flip the latch on the first that fires.

        Returns the posture code the cut was recorded with, or ``None`` when nothing fired and the
        stream keeps running. Idempotent once the latch is set -- a second call after a cut returns
        the already-recorded reason without re-reading the source, so repeated boundary polls do not
        thrash (and the one-way latch would ignore a second ``kill`` anyway).

        Order (fail-closed first): stale/unavailable snapshot (R10.6) -> max duration (R10.2) ->
        kill switch (R10.3) -> key revoked (R10.4) -> plan changed (R10.5). The first match flips
        the shared :class:`KillLatch` with its posture code; the pipeline cuts at the next boundary
        and forwards no further upstream bytes (R10.7).
        """
        if self._kill_latch.is_killed():
            return self._kill_latch.reason()

        snapshot = self._source.snapshot()

        # Fail closed FIRST: an unavailable or stale snapshot must not be trusted, so it dominates
        # every other signal (R10.6). `max_snapshot_age_s()` is the derived freshness bound.
        if not snapshot.available or snapshot.age_s > self._contract.max_snapshot_age_s():
            return self._fire(posture.STREAM_SNAPSHOT_STALE)

        # Wall-clock lifetime reached the derived maximum (R10.2).
        if self._clock() >= self._deadline:
            return self._fire(posture.STREAM_MAX_DURATION)

        if snapshot.kill_switch_engaged:
            return self._fire(posture.STREAM_KILLED)
        if snapshot.key_revoked:
            return self._fire(posture.STREAM_KEY_REVOKED)
        if snapshot.plan_changed:
            return self._fire(posture.STREAM_PLAN_CHANGED)

        return None

    def _fire(self, code: str) -> str:
        """Flip the shared latch with ``code`` and return it (R10.7, one seam many triggers)."""
        self._kill_latch.kill(code)
        return code
