"""Streaming chat orchestration, split out of `edge/routes.py` (size gate; a pure move).

:class:`ChatStream` holds the route and drives the egress pipeline under the no-splice gate,
in-flight control and the C27 timeouts; :func:`finish_stream` is the ONE terminal site and
:func:`emit_produced_mode` the Produced_Mode trace stamp. `ChatRoute` keeps thin delegators and
re-exports :class:`_ProducedModeHolder` so existing imports keep resolving.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from gateway_v2.detect.holdback import TokenIndex
from gateway_v2.detect.windowing import max_pattern_length
from gateway_v2.dispatch.provider import UpstreamRequest
from gateway_v2.domain.plan import ExecutionPlan, Phase
from gateway_v2.domain.posture import PLAN_UNAVAILABLE, STREAM_KILLED
from gateway_v2.edge.asgi_sse import ASGISend
from gateway_v2.edge.asgi_sse import ErrorFrameScan as _ErrorFrameScan
from gateway_v2.edge.asgi_sse import TerminalLatch as _TerminalLatch
from gateway_v2.edge.asgi_sse import as_chunks as _as_chunks
from gateway_v2.edge.asgi_sse import send_sse as _send_sse
from gateway_v2.edge.audit_emit import note_stream_output
from gateway_v2.edge.cancel import InFlightControl, KillLatch
from gateway_v2.edge.console_projection import stream_trace_payload
from gateway_v2.edge.phase_resolvers import resolver_for
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.stream_control import FirstByteLatch, StreamAttempt, run_with_no_splice
from gateway_v2.edge.stream_timeouts import StreamTimeout, StreamTimeouts
from gateway_v2.edge.wire.sse import SSEEncoder
from gateway_v2.egress.backpressure import Coalescer
from gateway_v2.egress.select import select_pipeline
from gateway_v2.egress.stream import (
    DownstreamFrame,
    EnforcementUndetermined,
    Send,
    StreamPipeline,
    StreamStats,
)
from gateway_v2.egress.strict import StrictStats, StrictWithholdPipeline
from gateway_v2.runtime.strict_metrics import StrictMetrics

if TYPE_CHECKING:
    from gateway_v2.edge.routes import ChatRoute

__all__ = ("ChatStream", "_ProducedModeHolder", "emit_produced_mode", "finish_stream")


class _ProducedModeHolder:
    """A per-request mutable sink for the pipeline's returned stats (GW13 C5).

    Both ``StreamPipeline.run`` and ``StrictWithholdPipeline.run`` return a stats
    value carrying ``produced_mode`` (and, on the INCREMENTAL side,
    ``degenerated_to_strict``). The ``_attempt`` coroutine captures that return and
    stashes it here so :func:`finish_stream` can emit the Produced_Mode
    trace frame on the wire just before the terminal ``[DONE]`` marker. It is a
    per-request LOCAL instance built in :meth:`ChatRoute._stream` (never a
    module-level mutable, gated), so nothing request-scoped is shared on the frozen
    route. ``stats`` is ``None`` until an attempt completed (a byte-less failure of
    every attempt leaves it unset, in which case no trace frame is emitted).
    """

    __slots__ = ("holds", "stats")

    def __init__(self, holds: RequestHolds | None = None) -> None:
        self.holds = holds
        self.stats: StreamStats | StrictStats | None = None


@dataclass(frozen=True, slots=True)
class ChatStream:
    """The streaming orchestration for one route (frozen; per-request state is local)."""

    route: ChatRoute


    async def stream(
        self,
        send: ASGISend,
        encoder: SSEEncoder,
        coalescer: Coalescer,
        plan: ExecutionPlan,
        destination: str,
        body: Mapping[str, object],
        holds: RequestHolds,
    ) -> None:
        """Drive the egress pipeline under the no-splice gate + in-flight control (R7, R10).

        A per-request :class:`FirstByteLatch` gates retry/fallback: ``_sink`` sets it the instant
        the FIRST content byte is released (R7.2), and :func:`run_with_no_splice` requests a fresh
        attempt only while it is unset (R7.1/R7.3). Each attempt opens the injected
        ``ProviderClient`` for the routed destination and runs the shipped ``StreamPipeline``
        release loop; released frames are encoded by the one ``SSEEncoder``.

        **In-flight control (R10).** A per-request :class:`KillLatch` is the ``killed()`` seam; an
        :class:`InFlightControl` evaluated at each chunk boundary (:func:`_as_chunks`) flips it on a
        fired trigger and stops the source (R10.7). **Bounded timeouts (C27/R9).** A per-request
        :class:`StreamTimeouts` bounds the next-event await (R9.2) and the downstream write + idle
        (R9.3/R9.4), flipping the SAME latch on a breach. Either way :meth:`_finish_stream` renders
        the latch's posture code as the declared terminal ``Error_Frame`` at the ONE terminal site.

        On a clean finish the terminal ``[DONE]`` marker is emitted; a byte-less failure of every
        eligible attempt closes the stream without a splice (R7.4).
        """
        latch = FirstByteLatch()
        sent_terminal = _TerminalLatch()
        kill_latch = KillLatch()
        produced = _ProducedModeHolder(holds)
        control = InFlightControl(
            source=self.route.control,
            contract=self.route.contract,
            clock=self.route.clock,
            kill_latch=kill_latch,
        )
        timeouts = StreamTimeouts(
            contract=self.route.contract,
            kill_latch=kill_latch,
            clock=self.route.clock,
        )

        async def _sink(frame: DownstreamFrame) -> None:
            if sent_terminal.is_set():
                return
            error_code = frame.error_code
            if error_code is not None:
                # The terminal error write is bounded by the write/idle timeout too (R9.3/R9.4)
                # so a client that stops reading cannot wedge the stream on the terminal frame.
                await timeouts.send(lambda: _send_sse(send, encoder.error(error_code)))
                sent_terminal.set()
                return
            # The first released content byte closes the no-splice gate (R7.2): once a byte is on
            # the wire the orchestration will never attempt a second upstream response (R7.4).
            latch.set_on_release()
            # Each downstream content write is bounded by the Write_Timeout, and the inter-write
            # gap by the Idle_Timeout (R9.3/R9.4); a breach flips the kill latch + raises.
            await timeouts.send(lambda: _send_sse(send, encoder.content(frame)))

        offered = _TerminalLatch()

        def _factory() -> StreamAttempt | None:
            return self._next_attempt(
                offered,
                latch,
                _sink,
                coalescer,
                kill_latch,
                control,
                timeouts,
                plan,
                destination,
                body,
                produced,
                holds,
            )

        await self._run_attempts(latch, _factory)
        await finish_stream(send, encoder, kill_latch, sent_terminal, produced)


    async def _run_attempts(
        self,
        latch: FirstByteLatch,
        factory: Callable[[], StreamAttempt | None],
    ) -> None:
        """Drive the no-splice attempt loop, swallowing a timeout cut as a terminal failure (R9).

        :func:`run_with_no_splice` re-raises a post-first-byte failure (R7.3). A C27 timeout is a
        legitimate terminal cut, not a splice candidate: it has already flipped the per-request
        :class:`KillLatch` with its posture code, so the stream must terminate and render that
        code — never retry. Catching :class:`StreamTimeout` here lets the loop fall through to
        :meth:`_finish_stream`, which reads the latch reason and emits the declared timeout
        ``Error_Frame`` at the single terminal site (R9.2–R9.4, fail closed: the resources are
        released as the loop unwinds).
        """
        try:
            await run_with_no_splice(latch, factory)
        except StreamTimeout:
            # The latch already carries the timeout reason; _finish_stream renders it. The raise
            # exists only to unwind the stalled await — resources release as the loop exits.
            return


    def _next_attempt(
        self,
        offered: _TerminalLatch,
        latch: FirstByteLatch,
        sink: Send,
        coalescer: Coalescer,
        kill_latch: KillLatch,
        control: InFlightControl,
        timeouts: StreamTimeouts,
        plan: ExecutionPlan,
        destination: str,
        body: Mapping[str, object],
        produced: _ProducedModeHolder,
        holds: RequestHolds,
    ) -> StreamAttempt | None:
        """Yield the next eligible stream attempt, or ``None`` when none remains (R7.1).

        There is exactly ONE signed upstream to try (a signed fallback chain is a later card), so
        the first call returns an attempt and every later call returns ``None`` — the ``offered``
        one-way latch records that the sole provider response has been handed out, so a byte-less
        failure is NOT retried against the same provider forever and there is nothing further to
        splice. :func:`run_with_no_splice` only invokes this factory while the first-byte latch
        still permits a retry, so a fresh attempt is impossible once a byte is on the wire.

        The attempt builds the pipeline via ``select_pipeline(plan, ...)`` (GW13 C4) — the
        resolved ``ExecutionPlan.streaming_mode`` selects the INCREMENTAL ``StreamPipeline`` or
        the STRICT_WITHHOLD ``StrictWithholdPipeline`` and drives the enforcing-output set (R2.3),
        replacing the old pass-through flag — opens the injected ``ProviderClient`` to the
        plan-derived
        ``destination`` with the transformed ``body`` (replacing the old ``_DEFAULT_DESTINATION`` /
        ``_EMPTY_BODY`` stand-ins, R2.2 / R8.4), hands the per-request :class:`KillLatch` to
        ``pipeline.run`` as the ``killed()`` seam, and threads the :class:`InFlightControl` +
        :class:`StreamTimeouts` into :func:`_as_chunks` (every R10 trigger at each chunk boundary,
        R10.2-R10.7; the Inter_Chunk_Timeout on the next-event await, R9.2).
        """
        if offered.is_set():
            return None
        offered.set()

        async def _attempt() -> bool:
            try:
                pipeline = self._build_pipeline(plan)
            except EnforcementUndetermined:
                # Defensive: the proceed outcome carries a resolved plan, so `for_plan` resolves
                # the enforcing-output set without raising. Were the enforcement state ever
                # undetermined, fail CLOSED — flip the kill latch with the declared
                # ``plan_unavailable`` code and release no byte; `_finish_stream` renders it (R2.4).
                kill_latch.kill(PLAN_UNAVAILABLE)
                return latch.is_set()
            request = UpstreamRequest(destination=destination, body=body)
            # The provider is about to be opened — after the pre-open ``_build_pipeline`` build
            # (which can fail CLOSED without opening) and the body assembly. Record the dispatch
            # so the budget spend now STANDS (the provider saw the request, C29 "spend stands
            # once dispatched"). On a no-splice RETRY this only re-sets a flag (idempotent).
            holds.mark_dispatched()
            chunks = _as_chunks(
                self.route.provider.open(request),
                coalescer,
                control,
                timeouts,
                error_scan=_ErrorFrameScan(
                    scanner=self.route.error_scanner,
                    # Group E: OUTPUT guard. Unset factory returns ``self.route.resolver`` (3.3).
                    resolver=resolver_for(self.route, plan, Phase.OUTPUT),
                    kill_latch=kill_latch,
                    executor=self.route.executor,
                ),
            )
            # Capture the returned stats so the Produced_Mode trace frame can be stamped on the
            # wire from the SELECTED mode + the INCREMENTAL degenerate fact (GW13 C5/R5). Both
            # StreamPipeline.run and StrictWithholdPipeline.run return a stats value carrying
            # ``produced_mode``, so the holder reads it uniformly regardless of mode.
            produced.stats = await pipeline.run(chunks, sink, kill_latch)
            note_stream_output(holds, produced.stats, plan.version)
            # The sink set the first-byte latch the instant a content byte went out; report that
            # as the "released a byte" signal so run_with_no_splice closes the gate (R7.2/R7.4).
            return latch.is_set()

        return _attempt


    def _build_pipeline(self, plan: ExecutionPlan) -> StreamPipeline | StrictWithholdPipeline:
        """Build the plan's stream pipeline and bind this route's surface on the detector."""
        from gateway_v2.edge.dev_components import bind_surface

        return select_pipeline(
            plan,
            scanner=self.route.scanner,
            token_index=TokenIndex(),
            cfg=self.route.cfg,
            metrics=self.route.holdback_metrics,
            window=max_pattern_length(),
            # Group E: the streaming output guard resolves through the pinned plan at
            # ``Phase.OUTPUT``. The detector gets this route's surface (GW26).
            resolver=resolver_for(self.route, plan, Phase.OUTPUT),
            detector=(
                bind_surface(self.route.output_detector, self.route.surface)
                if self.route.output_detector is not None
                else None
            ),
            max_buffer_bytes=self.route.contract.stream_buffer_bytes(self.route.active_streams()),
            strict_metrics=StrictMetrics(),
        )



async def finish_stream(
    send: ASGISend,
    encoder: SSEEncoder,
    kill_latch: KillLatch,
    sent_terminal: _TerminalLatch,
    produced: _ProducedModeHolder,
) -> None:
    """Close the stream: render an in-flight cut's ``Error_Frame`` or the ``[DONE]`` marker.

    If an in-flight trigger cut the stream (R10) OR a C27 timeout tripped (R9.2-R9.4), the
    per-request :class:`KillLatch` carries the reason posture code the FIRST cut set; the
    handler renders it as the declared terminal ``Error_Frame`` here (the pass-through
    pipeline does not emit one of its own), so a max-duration cut shows ``STREAM_MAX_DURATION``,
    a key revocation shows ``STREAM_KEY_REVOKED`` (R10.2-R10.6), and a stall shows
    ``STREAM_INTER_CHUNK_TIMEOUT`` / ``STREAM_IDLE_TIMEOUT`` / ``STREAM_WRITE_TIMEOUT``
    (R9.2-R9.4) — one terminal site for every cut reason. No content frame follows the terminal
    frame (``sent_terminal`` guards against a double terminal). Only when nothing was cut and no
    terminal has been emitted does the clean ``[DONE]`` marker close the stream.

    The in-loop content/error writes in ``_sink`` are the ones bounded by the Write_Timeout +
    Idle_Timeout (R9.3/R9.4); this terminal close is a best-effort final marker emitted after
    the resources have already been released by the unwinding loop, so it uses the plain
    ``_send_sse`` and never re-raises a timeout past the handler.

    **Produced_Mode stamp (GW13 C5/R5, GW14b §5.4).** A NON-CONTENT ``amf.trace``
    frame carries ``produced_mode`` and, when the input decision is known, the
    six-stage console object. On a clean completion it precedes ``[DONE]``. When
    the pipeline already emitted a terminal error, the same frame follows that
    error: ``trace()`` is not content, so no content frame follows the error.
    No stats means no frame.
    """
    if sent_terminal.is_set() or encoder.terminated:
        await emit_produced_mode(send, encoder, produced)
        return
    reason = kill_latch.reason()
    if reason is not None:
        # The pass-through loop ended normally after the cut and recorded "allow"; the trace
        # must not report a cut stream as allowed (a kill-switch cut publishes no action).
        stats = produced.stats
        if reason != STREAM_KILLED and getattr(stats, "output_action", None) == "allow":
            stats.output_action = "block"  # type: ignore[union-attr]
        await _send_sse(send, encoder.error(reason))
        sent_terminal.set()
        await emit_produced_mode(send, encoder, produced)
        return
    # Called on the class (not ``self``) so the terminal site stays usable with ``self``
    # unbound — the Produced_Mode stamp reads only the ``produced`` holder, never ``self``.
    await emit_produced_mode(send, encoder, produced)
    await _send_sse(send, encoder.done())



async def emit_produced_mode(
    send: ASGISend,
    encoder: SSEEncoder,
    produced: _ProducedModeHolder,
) -> None:
    """Emit the Produced_Mode trace frame from the captured stats (GW13 C5/R5).

    Reads the stats the completing attempt stashed on ``produced``: the
    ``produced_mode`` is the SELECTED streaming mode the pipeline ran
    (``incremental`` / ``strict_withhold``, R5.2/R5.3) and
    ``degenerated_to_strict`` is the INCREMENTAL per-response trace fact (R6.2) —
    ``StrictStats`` carries no such flag, so a STRICT run stamps ``False`` by
    construction. Emitted as the one non-content ``amf.trace`` frame; a holder
    with no stats (every attempt failed byte-less) emits nothing.
    """
    stats = produced.stats
    if stats is None:
        return
    await _send_sse(send, encoder.trace(stream_trace_payload(
        produced, stats, bool(getattr(stats, "degenerated_to_strict", False)),
    )))
