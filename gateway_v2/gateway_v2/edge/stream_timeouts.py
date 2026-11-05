"""Bounded stream timeouts for the SSE serving loop (GW12, task 11.1 — C27 / R9).

The streaming chat handler in :mod:`gateway_v2.edge.routes` must guarantee that a stalled
client or a stalled provider cannot hold a stream open past drain (R9). This module is the ONE
home for the three C27 bounds wired into the serving loop, each DERIVED from the injected
``ResourceContract`` — no caller holds a timeout literal (R9.5):

* **Inter_Chunk_Timeout (R9.2).** The maximum time the gateway waits for the *next upstream
  chunk*. :meth:`StreamTimeouts.next_event` wraps the ``await`` on the provider's next event
  in ``asyncio.wait_for(..., contract.inter_chunk_timeout_s())``; a breach flips the shared
  :class:`~gateway_v2.edge.cancel.KillLatch` with ``STREAM_INTER_CHUNK_TIMEOUT`` and stops the
  source so no further upstream byte is forwarded.
* **Write_Timeout (R9.4).** The maximum time a *single downstream write* may block.
  :meth:`StreamTimeouts.send` wraps the ``await`` on the ASGI ``send`` in
  ``asyncio.wait_for(..., contract.write_timeout_s())``; a breach flips the latch with
  ``STREAM_WRITE_TIMEOUT``.
* **Idle_Timeout (R9.3).** The maximum time a *downstream client may stop reading* before the
  stream is terminated. See "Modelling downstream idle" below.

**One cut seam, one terminal-rendering site (consistency with task 10.1).** A timeout is a
*sibling cut* to the in-flight triggers of task 10 (max-duration, kill switch, revocation, …):
rather than inventing a second terminal path, a timeout flips the SAME per-request
:class:`KillLatch` with its posture code. The chat handler's :meth:`ChatRoute._finish_stream`
already reads :meth:`KillLatch.reason` after the run and renders the matching declared
``Error_Frame`` through the single :class:`~gateway_v2.edge.wire.sse.SSEEncoder`. So the three
timeout codes render exactly where ``STREAM_MAX_DURATION`` / ``STREAM_KEY_REVOKED`` already do —
one terminal site, one envelope, no duplicate rendering. The latch is one-way, so whichever cut
(timeout or in-flight trigger) fires FIRST owns the client-facing reason.

**Fail closed (R9.*).** On any breach the latch is flipped (``killed()`` is now true, so the
shipped ``StreamPipeline`` cuts at the next boundary) AND the breaching coroutine raises
:class:`StreamTimeout` so the serving loop unwinds immediately and releases resources — a
timeout never leaves the loop hanging on a stalled await. ``asyncio.wait_for`` cancels the
wrapped awaitable on timeout, so the stalled provider read / blocked write is torn down rather
than left running.

**Modelling downstream idle on a push-only SSE channel.** A chat SSE response is pure-send: the
gateway writes ``data:`` frames onto the ASGI ``send`` channel and the client never sends read
acknowledgements back up a chat stream, so there is no explicit "client read" event to time
against. The observable proxy for "the client stopped reading" is back-pressure on ``send``:
when the client (or any downstream buffer) stops draining, the ASGI server stops accepting
``http.response.body`` events and the ``await send(...)`` blocks. We therefore model downstream
idle as *the time since the last byte the client accepted*:

* The ``write`` bound (R9.4) caps how long a SINGLE ``send`` may block — one stuck write.
* The ``idle`` bound (R9.3) caps the AGGREGATE gap since the last ``send`` that COMPLETED — how
  long the client has gone without accepting a byte. Before each ``send`` we read the injected
  clock; if ``now - last_completed_write > idle_timeout_s()`` the client has been idle too long
  and we cut with ``STREAM_IDLE_TIMEOUT`` before issuing the write.

  **Liveness mode (strict-withhold-idle-timeout, F1).** For a ``STRICT_WITHHOLD`` stream with an
  enforcing output rule the gateway deliberately writes NOTHING downstream until it has inspected
  and decided the whole response. The idle clock, seeded at stream start and advanced only by a
  completed write, would therefore measure how long the GATEWAY withheld, not how long the CLIENT
  stopped reading, and the first and only content write would trip ``STREAM_IDLE_TIMEOUT`` for any
  answer that took longer than ``idle_timeout_s()`` — blaming the client for silence the gateway
  created by design. When ``upstream_liveness=True`` (set once in ``ChatStream._controls`` for
  exactly that mode), idle is instead measured as *the time since the last upstream event or
  completed write*: every event :meth:`next_event` returns re-seeds the idle clock (the same
  assignment the D-18 first-event branch performs). The upstream stall is still bounded by
  ``inter_chunk_timeout_s()`` (1 s < idle's 3 s at defaults, so a silent upstream is always caught
  by :meth:`next_event` first) and total time by ``max_stream_duration_s()``; the idle check in
  :meth:`send` is unchanged, so once writing begins, client back-pressure is detected exactly as
  before. In the default (``upstream_liveness=False``) mode — INCREMENTAL and pass-through strict —
  none of this runs and the write-based model is byte-for-byte unchanged. A ``send`` that itself
  blocks past the idle bound is likewise an idle client, so :meth:`StreamTimeouts.send` bounds
  the single write by ``min(write_timeout_s, idle_timeout_s)`` is NOT used — instead the single
  write is bounded by its own ``write_timeout_s`` and the *inter-write gap* is bounded by
  ``idle_timeout_s``; the two compose so that a client which neither accepts the current write
  nor has accepted one recently is cut by whichever bound trips first. This is the documented
  push-only idle model: idle is measured against the injected clock across the serving loop's
  writes, so a client that stops reading is detected without a read channel.

The injected ``clock`` is a seconds-reading ``Callable[[], float]`` so the idle accounting is
deterministic in tests; the inter-chunk and write bounds use ``asyncio.wait_for`` with the
derived real timeout, which 11.2's property test drives deterministically with a stub provider /
send that delays past the bound.

**Layering.** ``edge`` is the top layer; it may import ``runtime`` (the ``ResourceContract``)
and ``domain`` (the posture codes) below it, and the sibling ``edge.cancel`` (the
``KillLatch``). No HTTP object is constructed here — a timeout flips the latch and raises; the
handler renders the ``Error_Frame`` (the codes-vs-render boundary is untouched). No
module-level mutable, no capacity literal: every bound is a ``contract`` method call.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from gateway_v2.domain import posture
from gateway_v2.edge.cancel import KillLatch
from gateway_v2.runtime.resources import ResourceContract

__all__ = (
    "StreamTimeout",
    "StreamTimeouts",
)

_T = TypeVar("_T")


class StreamTimeout(Exception):
    """A C27 bound was breached (R9.2–R9.4): the stream must terminate and release resources.

    Carries the posture ``code`` the breach was recorded with (``STREAM_INTER_CHUNK_TIMEOUT`` /
    ``STREAM_IDLE_TIMEOUT`` / ``STREAM_WRITE_TIMEOUT``) so a caller that catches it can confirm
    which bound tripped. The matching :class:`KillLatch` has ALREADY been flipped with the same
    code before this is raised, so the terminal ``Error_Frame`` is rendered from the latch
    reason at the single terminal site (``ChatRoute._finish_stream``); the raise exists so the
    serving loop unwinds immediately rather than hanging on the stalled await (fail closed).
    """

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class StreamTimeouts:
    """Wrap the serving loop's awaits with the three derived C27 bounds (R9.2–R9.5).

    Built once per request alongside the per-request :class:`KillLatch` the handler hands to
    ``pipeline.run`` and the :class:`~gateway_v2.edge.cancel.InFlightControl`. The three bounds
    all derive from the injected ``ResourceContract`` — no literal here (R9.5). A breach flips
    the shared latch (so the shipped pipeline cuts at the next boundary) and raises
    :class:`StreamTimeout` (so the serving loop unwinds immediately and releases resources).

    State is exactly one mutable cell: ``_last_write_s``, the injected-clock reading of the last
    COMPLETED downstream write, used for the push-only idle model (see the module docstring). It
    is initialised to the stream-start clock reading so the first inter-write gap is measured
    from stream start. No module-level mutable; the per-request instance carries it.
    """

    __slots__ = (
        "_clock",
        "_contract",
        "_first_event_s",
        "_first_pending",
        "_kill_latch",
        "_last_write_s",
        "_upstream_liveness",
    )

    def __init__(
        self,
        *,
        contract: ResourceContract,
        kill_latch: KillLatch,
        clock: Callable[[], float],
        first_event_s: float | None = None,
        upstream_liveness: bool = False,
    ) -> None:
        self._contract = contract
        # GW28 D-13/D-18: when routing is on, the FIRST next-event await uses this wider bound and
        # the idle clock is re-seeded once it returns. ``None`` leaves behaviour byte-identical.
        self._first_event_s = first_event_s
        self._first_pending = first_event_s is not None
        self._kill_latch = kill_latch
        self._clock = clock
        # strict-withhold-idle-timeout (F1): when a withholding strict stream writes nothing
        # downstream until the whole response is decided, the write-based idle gap would blame the
        # client for a silence the gateway created on purpose. In liveness mode EVERY upstream
        # event re-seeds the idle clock (see ``_note_upstream_activity``), turning "time since the
        # last completed write" into "time since the last upstream event or completed write". The
        # default is False so every existing construction (INCREMENTAL, pass-through strict, tests,
        # routing boot) is byte-for-byte unchanged.
        self._upstream_liveness = upstream_liveness
        # The last completed-write clock reading seeds from stream start so the first write's
        # idle gap is measured from when the stream opened (R9.3). Mutable one-cell state.
        self._last_write_s = clock()

    async def next_event(self, awaitable: Awaitable[_T]) -> _T:
        """Await the next upstream event under the Inter_Chunk_Timeout (R9.2).

        Wraps the provider's next-event ``await`` in ``asyncio.wait_for`` with the derived
        ``inter_chunk_timeout_s()`` (no literal, R9.5). If the next upstream chunk does not
        arrive within the bound, ``wait_for`` cancels the stalled read, the shared latch is
        flipped with ``STREAM_INTER_CHUNK_TIMEOUT`` (so the pipeline cuts at the next boundary
        and the handler renders the declared ``Error_Frame``), and :class:`StreamTimeout` is
        raised so the serving loop unwinds and releases resources (R9.2, fail closed).
        """
        first = self._first_pending
        self._first_pending = False
        limit = self._contract.inter_chunk_timeout_s()
        if first and self._first_event_s is not None:
            limit = self._first_event_s
        try:
            result = await asyncio.wait_for(awaitable, timeout=limit)
        except TimeoutError as exc:
            raise self._cut(posture.STREAM_INTER_CHUNK_TIMEOUT) from exc
        if first:
            # D-18: idle measures client back-pressure, not upstream latency, so the clock
            # restarts when the first upstream event of ANY kind has arrived.
            self._last_write_s = self._clock()
        if self._upstream_liveness:
            # strict-withhold-idle-timeout (F1): a withholding strict stream makes no write while
            # it accumulates, so the idle clock would otherwise measure the gateway's own withhold.
            # Re-seed it on EVERY upstream event (the same assignment the D-18 branch performs; for
            # a routed liveness stream both lines run and the second assignment is idempotent). The
            # cut path (``_cut`` on a timeout) never reaches here, so a timeout never re-seeds
            # (fail closed): the refresh happens only on the successful return path.
            self._note_upstream_activity()
        return result

    def _note_upstream_activity(self) -> None:
        """Re-seed the idle clock on an upstream event (strict-withhold liveness mode).

        Assigns ``self._last_write_s = self._clock()`` — exactly the assignment the GW28 D-18
        first-event branch performs — so that, in ``upstream_liveness`` mode, the idle gap that
        :meth:`send` checks is measured from the last upstream event rather than from a downstream
        write that a withholding strict stream never issued until the decision. One mutable cell,
        no new state; only ever called from the successful :meth:`next_event` return path, so a
        timeout cut can never re-seed (fail closed).
        """
        self._last_write_s = self._clock()

    async def send(self, do_send: Callable[[], Awaitable[None]]) -> None:
        """Perform one downstream write under the Write_Timeout + Idle_Timeout (R9.3/R9.4).

        Two bounds compose on the push-only SSE channel (see the module docstring):

        * **Idle (R9.3).** Before issuing the write, the injected clock is read; if the gap
          since the last COMPLETED write exceeds ``idle_timeout_s()`` the client has stopped
          reading for too long, so the stream is cut with ``STREAM_IDLE_TIMEOUT`` and
          :class:`StreamTimeout` raised BEFORE the write is attempted.
        * **Write (R9.4).** The single write ``await`` is wrapped in ``asyncio.wait_for`` with
          ``write_timeout_s()``; a block past the bound cancels the stuck write, cuts with
          ``STREAM_WRITE_TIMEOUT``, and raises.

        On success the last-completed-write clock reading is advanced so the next call measures
        its idle gap from here. Both bounds derive from the contract — no literal (R9.5).
        """
        now = self._clock()
        if now - self._last_write_s > self._contract.idle_timeout_s():
            raise self._cut(posture.STREAM_IDLE_TIMEOUT)
        try:
            await asyncio.wait_for(do_send(), timeout=self._contract.write_timeout_s())
        except TimeoutError as exc:
            raise self._cut(posture.STREAM_WRITE_TIMEOUT) from exc
        # The write completed: the client accepted a byte now, so the idle gap resets (R9.3).
        self._last_write_s = self._clock()

    def _cut(self, code: str) -> StreamTimeout:
        """Flip the shared latch with ``code`` and build the matching :class:`StreamTimeout`.

        One seam, many triggers (the task 10 pattern): a timeout flips the SAME per-request
        :class:`KillLatch` so ``killed()`` is true and the shipped ``StreamPipeline`` cuts at
        the next chunk boundary, and the handler renders ``code`` as the terminal ``Error_Frame``
        from :meth:`KillLatch.reason` at the single terminal site. The latch is one-way, so if an
        in-flight trigger already fired this ``kill`` is a no-op and that earlier reason is kept;
        the raised :class:`StreamTimeout` still unwinds the serving loop so resources release.
        """
        self._kill_latch.kill(code)
        return StreamTimeout(code)
