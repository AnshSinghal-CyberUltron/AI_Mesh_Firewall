"""Streaming chat orchestration, split out of `edge/routes.py` (size gate; a pure move).

:class:`ChatStream` holds the route and drives the egress pipeline under the no-splice gate,
in-flight control and the C27 timeouts; :func:`finish_stream` is the ONE terminal site and
:func:`emit_produced_mode` the Produced_Mode trace stamp. `ChatRoute` keeps thin delegators and
re-exports :class:`_ProducedModeHolder` so existing imports keep resolving.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from gateway_v2.detect.holdback import TokenIndex
from gateway_v2.detect.windowing import max_pattern_length
from gateway_v2.dispatch.provider import UpstreamRequest
from gateway_v2.domain.decision import Disposition
from gateway_v2.domain.plan import ExecutionPlan, Phase, Surface
from gateway_v2.domain.posture import PLAN_UNAVAILABLE, STREAM_KILLED
from gateway_v2.edge.asgi_sse import ASGISend
from gateway_v2.edge.asgi_sse import ErrorFrameScan as _ErrorFrameScan
from gateway_v2.edge.asgi_sse import TerminalLatch as _TerminalLatch
from gateway_v2.edge.asgi_sse import as_chunks as _as_chunks
from gateway_v2.edge.asgi_sse import send_sse as _send_sse
from gateway_v2.edge.audit_emit import note_stream_output
from gateway_v2.edge.cancel import InFlightControl, KillLatch
from gateway_v2.edge.console_projection import stream_trace_payload
from gateway_v2.edge.disconnect import StreamingDone
from gateway_v2.edge.phase_resolvers import resolver_for
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.stream_control import FirstByteLatch, StreamAttempt, run_with_no_splice
from gateway_v2.edge.stream_response import StreamResponse
from gateway_v2.edge.stream_timeouts import StreamTimeout, StreamTimeouts
from gateway_v2.edge.wire.sse import SSEEncoder
from gateway_v2.edge.wire.sse_chunks import ChunkEvent
from gateway_v2.edge.wire.tool_stream import Terminate, ToolCallAssembler
from gateway_v2.egress.backpressure import Coalescer
from gateway_v2.egress.output_guard import enforcing_output_rules
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
    from gateway_v2.edge.wire.openai import CanonicalRequest

__all__ = (
    "ChatStream",
    "PendingError",
    "_ProducedModeHolder",
    "emit_produced_mode",
    "finish_stream",
)


class _AssemblerTerminated(Exception):
    """The tool-call assembler terminated the stream; the code is on :class:`PendingError`.

    Raised from the sink so ``StreamPipeline._run_passthrough`` (which never checks ``killed()``)
    unwinds too; caught in the attempt and not counted as an incomplete stream.
    """


class PendingError:
    """The terminal code recorded while the response is still pending (per request, mutable)."""

    __slots__ = ("code",)

    def __init__(self) -> None:
        self.code: str | None = None


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
        response: StreamResponse,
        encoder: SSEEncoder,
        coalescer: Coalescer,
        plan: ExecutionPlan,
        destination: str,
        body: Mapping[str, object],
        holds: RequestHolds,
        req: CanonicalRequest | None = None,
        done: StreamingDone | None = None,
    ) -> None:
        """Drive the egress pipeline under the no-splice gate + in-flight control (R7, R10).

        A per-request :class:`FirstByteLatch` gates retry/fallback: the sink sets it the instant
        the FIRST content byte is released (R7.2), and :func:`run_with_no_splice` requests a fresh
        attempt only while it is unset (R7.1/R7.3). Each attempt opens the injected
        ``ProviderClient`` for the routed destination and runs the shipped pipeline; the released
        frames pass through the per-request :class:`ToolCallAssembler` (GW34) and are encoded by
        the one ``SSEEncoder``.

        **Deferred start (GW34 R7).** Nothing is written until the first content-bearing event, so
        a refusal or failure before it is a plain HTTP error (:class:`StreamResponse`), not an SSE
        error frame after a 200. **In-flight control (R10).** A per-request :class:`KillLatch` is
        the ``killed()`` seam; an :class:`InFlightControl` evaluated at each chunk boundary
        (:func:`_as_chunks`) flips it on a fired trigger. **Bounded timeouts (C27/R9).** A
        per-request :class:`StreamTimeouts` bounds the next-event await and the downstream write.
        Either way :func:`finish_stream` renders the latch's posture code at the ONE terminal site.
        """
        latch = FirstByteLatch()
        sent_terminal = _TerminalLatch()
        kill_latch = KillLatch()
        pending_error = PendingError()
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
        assembler = self._assembler(plan, coalescer, req)

        async def _write_events(events: Sequence[ChunkEvent]) -> None:
            for event in events:
                # The first released content byte closes the no-splice gate (R7.2): once a byte is
                # on the wire the orchestration never attempts a second upstream response (R7.4).
                latch.set_on_release()
                # Each downstream write is bounded by the Write_Timeout, the inter-write gap by the
                # Idle_Timeout (R9.3/R9.4); a breach flips the kill latch + raises.
                await timeouts.send(partial(response.write, encoder.chunk(event)))

        def _terminate(outcome: Terminate) -> _AssemblerTerminated:
            kill_latch.kill(outcome.code)
            pending_error.code = outcome.code
            return _AssemblerTerminated(outcome.code)

        async def _sink(frame: DownstreamFrame) -> None:
            if sent_terminal.is_set():
                return
            error_code = frame.error_code
            if error_code is not None:
                if response.pending:
                    pending_error.code = error_code  # nothing written: a plain HTTP error
                else:
                    await timeouts.send(lambda: response.write_started(encoder.error(error_code)))
                sent_terminal.set()
                return
            outcome = assembler.feed(frame)
            if isinstance(outcome, Terminate):
                raise _terminate(outcome)
            await _write_events(outcome)

        async def _finish_calls() -> None:
            outcome = await assembler.finish()
            if isinstance(outcome, Terminate):
                _terminate(outcome)
                return
            await _write_events(outcome)

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
                finish_calls=_finish_calls,
                pending_error=pending_error,
                sent_terminal=sent_terminal,
            )

        await self._run_attempts(latch, _factory)
        holds.note_tool_calls(assembler.calls_released, assembler.calls_withheld)
        if done is not None:
            done.set()  # the terminal write begins: a later disconnect must not cancel it
        await finish_stream(
            response.send,
            encoder,
            kill_latch,
            sent_terminal,
            produced,
            response=response,
            finish_reason=assembler.finish_reason,
            pending_error=pending_error,
        )
        await response.close()

    def _assembler(
        self, plan: ExecutionPlan, coalescer: Coalescer, req: CanonicalRequest | None
    ) -> ToolCallAssembler:
        """Build the per-request assembler from what the request declared (D6, D9, D18)."""
        from gateway_v2.edge.dev_components import bind_surface  # local: it imports ``edge.app``

        template = None if req is None else req.upstream_template
        declared = frozenset(tool.name for tool in (template.tools.tools if template else ()))
        fmt = None if template is None else template.tools.response_format
        resolver = resolver_for(self.route, plan, Phase.OUTPUT)
        scanner = bind_surface(self.route.error_scanner, Surface.CHAT)
        executor = self.route.executor

        async def decoded_clean(text: str) -> bool:
            def work() -> bool:
                decision = resolver.decide(list(scanner(text)))
                return decision.disposition in (Disposition.ALLOW, Disposition.FLAG)

            return await executor.run(work)

        return ToolCallAssembler(
            declared=declared,
            tools_declared=bool(declared),
            enforcing=bool(enforcing_output_rules(plan)),
            json_mode=fmt is not None and fmt.type != "text",
            bounds=self.route.tool_bounds,
            decoded_clean=decoded_clean,
            charge=coalescer.offer,
            refund=coalescer.release,
        )

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
        *,
        finish_calls: Callable[[], Awaitable[None]],
        pending_error: PendingError,
        sent_terminal: _TerminalLatch,
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
            try:
                stats = await pipeline.run(chunks, sink, kill_latch)
            except _AssemblerTerminated:
                # The assembler terminated the stream (its code is on the latch and the pending
                # holder): not an ``incomplete`` stream, and nothing more is released.
                return latch.is_set()
            produced.stats = stats
            # GW34 guarded finish: ``run`` RETURNS on an egress terminate (``incomplete``), so a
            # clean return alone is not success. Release the withheld calls only when nothing
            # terminated the stream; otherwise they are dropped with the request.
            if (
                not stats.incomplete
                and kill_latch.reason() is None
                and pending_error.code is None
                and not sent_terminal.is_set()
            ):
                await finish_calls()
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
    *,
    response: StreamResponse | None = None,
    finish_reason: str | None = None,
    pending_error: PendingError | None = None,
) -> None:
    """Close the stream: a plain HTTP error, an ``Error_Frame``, or the finish chunk + ``[DONE]``.

    If an in-flight trigger cut the stream (R10), a C27 timeout tripped (R9.2-R9.4) or the
    tool-call assembler terminated it, the per-request :class:`KillLatch` carries the reason code
    the FIRST cut set (``pending_error`` the code recorded while no byte had been written); this
    is the ONE terminal site for every cut reason, so a max-duration cut shows
    ``STREAM_MAX_DURATION``, a key revocation ``STREAM_KEY_REVOKED``, a stall a timeout code.

    **Pre-byte (GW34 R7).** With a ``response`` still pending, a failure code answers as a plain
    HTTP error (the same bytes a non-stream request gets; no ``amf.trace``), and a clean stream
    that produced no content starts the SSE response, sends the finish chunk, the trace and
    ``[DONE]`` (HTTP 200). **After the first byte** the terminal handling is unchanged: the
    declared ``Error_Frame``, or (clean) the finish chunk, the Produced_Mode trace and ``[DONE]``.
    No content frame follows a terminal frame (``sent_terminal`` guards a double terminal). With no
    ``response`` (the legacy callers) the behaviour is the pre-GW34 one.

    **Produced_Mode stamp (GW13 C5/R5, GW14b §5.4).** A NON-CONTENT ``amf.trace`` frame carries
    ``produced_mode`` and, when the input decision is known, the six-stage console object. No
    stats means no frame.
    """
    pending = response is not None and response.pending
    code = None if pending_error is None else pending_error.code
    reason = kill_latch.reason()
    if pending and response is not None:
        if code is None:
            code = reason
        if code is not None:
            await response.fail_pre_byte(code)
            return
        await response.start()  # a clean stream that produced no content: 200 + [DONE]
    if response is None:
        async def put(frame: bytes) -> None:
            await _send_sse(send, frame)
    else:
        put = response.write_started
    if sent_terminal.is_set() or encoder.terminated:
        await emit_produced_mode(send, encoder, produced, put)
        return
    if reason is not None:
        # The pass-through loop ended normally after the cut and recorded "allow"; the trace
        # must not report a cut stream as allowed (a kill-switch cut publishes no action).
        stats = produced.stats
        if reason != STREAM_KILLED and getattr(stats, "output_action", None) == "allow":
            stats.output_action = "block"  # type: ignore[union-attr]
        await put(encoder.error(reason))
        sent_terminal.set()
        await emit_produced_mode(send, encoder, produced, put)
        return
    if finish_reason is not None:
        await put(encoder.finish(finish_reason))
    await emit_produced_mode(send, encoder, produced, put)
    await put(encoder.done())


async def emit_produced_mode(
    send: ASGISend,
    encoder: SSEEncoder,
    produced: _ProducedModeHolder,
    put: Callable[[bytes], Awaitable[None]] | None = None,
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
    frame = encoder.trace(
        stream_trace_payload(produced, stats, bool(getattr(stats, "degenerated_to_strict", False)))
    )
    if put is None:
        await _send_sse(send, frame)
    else:
        await put(frame)
