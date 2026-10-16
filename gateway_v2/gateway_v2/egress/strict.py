"""Whole-response withhold mode — the STRICT_WITHHOLD pipeline (GW13, C1).

``StrictWithholdPipeline`` is the second explicit streaming mode (the
``Strict_Withhold`` of the requirements): it accumulates the WHOLE upstream
response, releases nothing, inspects the whole buffer ONCE, resolves it through
the single ``OutputResolver`` seam at ``Phase.OUTPUT``, applies the ``Decision``
via the shipped ``apply_decision``, and then either emits the sanitized whole
response as content frame(s) or a single terminal ``output_blocked`` frame with
ZERO content before it (LGW13-1).

**A DISTINCT mode, not a holdback variant (C25).** STRICT does NOT use the
holdback scanner, ``TokenIndex``, or the hold cap. The bounded O(1) holdback
engine that ``StreamPipeline`` drives is shipped and not rebuilt here. STRICT
inspects the whole accumulated buffer a single time at ``final`` (R3.6); its
boundary safety is therefore trivial (the whole text is present before any
release, R7.2), and its latency profile is the completion overhead only (the
clock at ``final`` through the clock when the whole sanitized response is
emitted, R9.4) -- it never claims the INCREMENTAL time-to-first-token (R9.2).

**Sibling of ``StreamPipeline``.** Same injection-seam shape: the resolver, the
clock, the completed-match ``Detector``, and the ``StrictMetrics`` producer are
all injected through ``__init__``; the transport value types (``UpstreamChunk``
/ ``DownstreamFrame``), the injection aliases (``Send`` / ``ChunkSource`` /
``KillSignal`` / ``Detector``), the unresolved-plan vocabulary
(``EnforcementUndetermined`` / ``Undetermined`` / ``UpstreamPlan``), and the one
resolver seam (``OutputResolver`` / ``apply_decision`` / ``enforcing_output_rules``
/ ``OutputBlocked``) are all REUSED from the sibling ``egress.stream`` and
``egress.output_guard`` modules -- never redefined.

Layering (import-linter ``layers`` contract in ``pyproject.toml``). ``detect``
sits ABOVE ``egress``, so this module MUST NOT import ``gateway_v2.detect``. The
completed-match source is the INJECTED ``Detector`` callable, described
structurally there and reused here; ``None`` means the thin harness (no
findings, ALLOW decision, identity ``apply_decision``). This module imports only
``gateway_v2.domain``, ``gateway_v2.runtime`` (``StrictMetrics``), and the
sibling ``gateway_v2.egress`` modules -- never ``detect``, ``plan``, ``admit``,
or ``edge``.

**Terminal-frame discipline (mirrors ``StreamPipeline._terminate``).** Every
terminal frame is a ``DownstreamFrame`` carrying ``error_code`` and NO content;
it is the LAST frame emitted for the stream, and ``run`` returns immediately
after emitting it, so no content frame can follow a terminal frame. STRICT
reuses the SAME raw ``error_code`` spellings the shipped pipeline emits:
``"output_blocked"`` for a BLOCK ``Decision`` and ``"killed"`` for an in-flight
kill (``StreamPipeline._cut_on_kill`` emits ``error="killed"``, which the edge
renderer maps to ``posture.STREAM_KILLED`` == ``"stream_killed"``). The one new
spelling is the STRICT buffer-overflow terminal, taken from the shared
vocabulary ``posture.STREAM_STRICT_BUFFER_OVERFLOW`` == ``"strict_buffer_overflow"``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from gateway_v2.domain import (
    Finding,
    InFlightKill,
    PlanUnavailable,
    PlanUnknownTenant,
    StreamingMode,
    posture,
)
from gateway_v2.egress.output_guard import (
    OutputBlocked,
    OutputResolver,
    apply_decision,
    enforcing_output_rules,
)
from gateway_v2.egress.stream import (
    ChunkSource,
    Detector,
    DownstreamFrame,
    EnforcementUndetermined,
    KillSignal,
    Send,
    Undetermined,
    UpstreamPlan,
)
from gateway_v2.runtime.strict_metrics import StrictMetrics

__all__ = (
    "StrictStats",
    "StrictWithholdPipeline",
)

#: The raw ``error_code`` the shipped pipeline puts on the terminal frame for an
#: in-flight kill (``StreamPipeline._cut_on_kill`` -> ``error="killed"``). STRICT
#: mirrors it byte-for-byte so the edge/SSE renderer maps both pipelines' kill
#: terminal to the single ``posture.STREAM_KILLED`` spelling (the C37 join).
_KILLED_CODE = "killed"
#: The raw ``error_code`` the shipped pipeline puts on the terminal frame for a
#: BLOCK ``Decision`` (``StreamPipeline`` -> ``error="output_blocked"``). Reused.
_OUTPUT_BLOCKED_CODE = "output_blocked"
#: The only in-flight-kill semantics this pipeline implements, read against the
#: domain lock exactly as ``StreamPipeline`` does: cut no later than the next
#: upstream chunk boundary (R7).
_KILL_MODE: InFlightKill = InFlightKill.CUT_NEXT_CHUNK


# --------------------------------------------------------------------------- #
# Per-run reading (Data Models).
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class StrictStats:
    """The per-run reading ``StrictWithholdPipeline`` hands to the metrics producer.

    Mutable per-run (``slots=True``, NOT frozen), mirroring ``StreamStats``. It
    structurally satisfies ``runtime.strict_metrics.StrictStatsLike`` (it exposes
    ``completion_overhead_ns``) and is passed straight into
    ``StrictMetrics.observe_stream_complete`` / ``observe_stream_incomplete``.

    ``upstream_chunks`` counts delivered chunks; ``frames_out`` counts CONTENT
    frames emitted (``0`` on BLOCK, kill, or overflow -- the terminal frame is
    never counted). ``buffered_bytes`` is the peak accumulated byte count,
    bounded by ``max_buffer_bytes``. ``completion_overhead_ns`` is the whole
    STRICT latency profile (R9.4): the clock at the ``final`` chunk through the
    clock when the whole sanitized response is emitted. ``produced_mode`` is
    always ``StreamingMode.STRICT_WITHHOLD`` (R5.3). ``incomplete`` flags an
    abnormal termination (kill / overflow / block); ``error`` carries the
    terminal code (``output_blocked`` | ``killed`` | ``strict_buffer_overflow``).
    """

    upstream_chunks: int = 0
    frames_out: int = 0
    buffered_bytes: int = 0
    completion_overhead_ns: int = 0
    produced_mode: StreamingMode = StreamingMode.STRICT_WITHHOLD
    done: bool = False
    incomplete: bool = False
    error: str | None = None


# --------------------------------------------------------------------------- #
# StrictWithholdPipeline (C1).
# --------------------------------------------------------------------------- #


class StrictWithholdPipeline:
    """Accumulate the whole response, inspect it once, resolve once, then emit.

    The enforcing-output gating decision (R2.3) belongs to the CALLER / plan
    resolution: it is computed once, before the first chunk, from
    ``output_guard.enforcing_output_rules(plan)``. Use :meth:`for_plan` so the
    "determine before the first chunk" contract is explicit and an unresolved
    plan is rejected before any byte is read. ``__init__`` takes the already
    resolved ``enforcing_output`` boolean for callers (and GW13's factory) that
    resolved it elsewhere.

    This is a DISTINCT mode: it never touches the holdback scanner, the
    ``TokenIndex``, or the hold cap (C25). The resolver seam and ``apply_decision``
    byte-discipline are the ONLY shared machinery with ``StreamPipeline``.
    """

    def __init__(
        self,
        *,
        resolver: OutputResolver,
        enforcing_output: bool,
        max_buffer_bytes: int,
        metrics: StrictMetrics,
        clock: Callable[[], int] = time.perf_counter_ns,
        detector: Detector | None = None,
    ) -> None:
        self._resolver = resolver
        self._enforcing_output = enforcing_output
        self._max_buffer_bytes = max_buffer_bytes
        self._metrics = metrics
        self._clock = clock
        self._detector = detector

    @classmethod
    def for_plan(
        cls,
        plan: UpstreamPlan,
        *,
        resolver: OutputResolver,
        max_buffer_bytes: int,
        metrics: StrictMetrics,
        clock: Callable[[], int] = time.perf_counter_ns,
        detector: Detector | None = None,
    ) -> StrictWithholdPipeline:
        """Build a pipeline, resolving ``enforcing_output`` from the plan (R2.3).

        Mirrors ``StreamPipeline.for_plan``: the enforcing-output state is
        computed ONCE here, before the first chunk is read, from
        ``enforcing_output_rules(plan)`` -- a non-empty result means STRICT
        accumulates and inspects; an empty result means pass-through (it has
        nothing to withhold).

        If the plan is unresolved -- an ``Undetermined`` sentinel, a
        ``PlanUnavailable``, or a ``PlanUnknownTenant`` -- this raises
        ``EnforcementUndetermined`` (reused from ``egress.stream``) BEFORE
        constructing the pipeline, so the stream is withheld and no byte is ever
        released (R2.4, fail closed: an undetermined enforcement state never
        defaults to pass-through).
        """
        if isinstance(plan, (Undetermined, PlanUnavailable, PlanUnknownTenant)):
            raise EnforcementUndetermined(
                "output-enforcement state is undetermined; the plan could not be resolved",
            )
        enforcing_output = bool(enforcing_output_rules(plan))
        return cls(
            resolver=resolver,
            enforcing_output=enforcing_output,
            max_buffer_bytes=max_buffer_bytes,
            metrics=metrics,
            clock=clock,
            detector=detector,
        )

    async def run(
        self,
        chunks: ChunkSource,
        send: Send,
        killed: KillSignal,
    ) -> StrictStats:
        """Consume ``chunks``, withhold until the whole response is inspected.

        Returns the per-run ``StrictStats`` after the stream ends. When
        ``enforcing_output`` is false the stream passes through unheld (R2.2
        parity, still reporting ``produced_mode = strict_withhold``); otherwise it
        accumulates every chunk into per-channel ``whole`` buffers, releasing
        nothing (R3.2), and on the ``final`` chunk inspects the whole buffer once,
        resolves once through the single ``OutputResolver`` seam, and emits either
        the sanitized whole response or a single terminal ``output_blocked`` frame
        with zero content before it (R3.4, LGW13-1).
        """
        stats = StrictStats()
        if not self._enforcing_output:
            return await self._run_passthrough(chunks, send, stats)
        return await self._run_enforcing(chunks, send, killed, stats)

    # -- pass-through (no enforcing rule, R2.2 parity) ---------------------- #

    async def _run_passthrough(
        self,
        chunks: ChunkSource,
        send: Send,
        stats: StrictStats,
    ) -> StrictStats:
        """Release every chunk unheld; withhold nothing (R2.2 parity).

        A STRICT plan with no enforcing output rule has nothing to withhold, so
        it mirrors ``StreamPipeline._run_passthrough`` -- each chunk's deltas pass
        straight through as content frames -- while still reporting
        ``produced_mode = strict_withhold`` on the returned stats.
        """
        try:
            async for chunk in chunks:
                stats.upstream_chunks += 1
                await send(DownstreamFrame(text_deltas=chunk.text_deltas))
                stats.frames_out += 1
                if chunk.final:
                    break
        except Exception:
            stats.incomplete = True
            self._metrics.observe_stream_incomplete(stats)
            raise
        stats.done = True
        self._metrics.observe_stream_complete(stats)
        return stats

    # -- enforcing (accumulate whole, inspect once, R3) --------------------- #

    async def _run_enforcing(
        self,
        chunks: ChunkSource,
        send: Send,
        killed: KillSignal,
        stats: StrictStats,
    ) -> StrictStats:
        """Accumulate per-channel ``whole`` buffers, inspect once at ``final``.

        The channel insertion order is preserved so the sanitized content frames
        are emitted deterministically in the order channels first appeared.
        """
        whole: dict[str, str] = {}
        try:
            async for chunk in chunks:
                stats.upstream_chunks += 1

                # In-flight kill (R7 parity). Checked at the chunk boundary -- the
                # delivery of one complete upstream chunk -- so the cut takes
                # effect no later than the NEXT boundary (InFlightKill.CUT_NEXT_CHUNK,
                # R7.1). Because the check runs BEFORE this chunk is accumulated,
                # zero content is emitted after the cut (R7.2) and every
                # accumulated buffer is discarded, never released (R7.3). A kill
                # that flips true only AFTER the final chunk was processed is never
                # seen (the loop broke on `final`), so a done stream completes
                # normally and retracts nothing (R7.5).
                if killed():
                    return await self._cut_on_kill(send, stats)

                # Accumulate; release NOTHING (R3.2).
                for channel, delta in chunk.text_deltas:
                    whole[channel] = whole.get(channel, "") + delta
                stats.buffered_bytes = sum(len(text) for text in whole.values())

                # Fail closed if accumulation would exceed the capacity-derived
                # bound (R9.5): emit the terminal frame, release zero content.
                if stats.buffered_bytes > self._max_buffer_bytes:
                    return await self._overflow(send, stats)

                if chunk.final:
                    return await self._finalize(whole, send, stats)
        except Exception:
            stats.incomplete = True
            self._metrics.observe_stream_incomplete(stats)
            raise

        # The source ended without a `final` chunk: nothing was ever releasable,
        # so withhold everything and record an abnormal termination (fail closed,
        # never default to releasing an un-inspected buffer).
        return await self._terminate(send, stats, error=_KILLED_CODE)

    # -- final-chunk inspection + resolution (R3.3-R3.5) -------------------- #

    async def _finalize(
        self,
        whole: dict[str, str],
        send: Send,
        stats: StrictStats,
    ) -> StrictStats:
        """Inspect the whole buffer once, resolve once, emit or BLOCK.

        Multi-channel decision choice: STRICT resolves PER CHANNEL -- for each
        channel it runs ``detector(whole_channel)`` and ``resolver.decide`` over
        that channel's own findings, then ``apply_decision(whole_channel,
        decision, base_offset=0)`` in that channel's own 0-based coordinate space.
        A BLOCK on ANY channel blocks the WHOLE response: the first channel whose
        ``apply_decision`` raises ``OutputBlocked`` yields a single terminal
        ``output_blocked`` frame with ZERO content frames before it (nothing is
        emitted until every channel has been sanitized, so no content precedes the
        block, R3.4 / LGW13-1). This matches "a BLOCK on any part of the response
        is always a truthful block" (R3 intro). The latency clock starts here (at
        ``final``) and stops when the whole sanitized response has been emitted
        (R9.4).
        """
        t_final = self._clock()

        # Sanitize every channel BEFORE emitting any content, so a BLOCK on a
        # later channel still yields zero content frames (LGW13-1). An
        # OutputBlocked anywhere short-circuits to the terminal block path.
        sanitized: list[tuple[str, str]] = []
        try:
            for channel, text in whole.items():
                findings: Sequence[Finding] = (
                    self._detector(text) if self._detector is not None else ()
                )
                decision = self._resolver.decide(findings)
                sanitized.append((channel, apply_decision(text, decision, 0)))
        except OutputBlocked:
            return await self._terminate(send, stats, error=_OUTPUT_BLOCKED_CODE)

        # ALLOW/REDACT: emit the sanitized whole response as a single content
        # frame carrying every channel's bytes, then complete cleanly.
        await send(DownstreamFrame(text_deltas=tuple(sanitized)))
        stats.frames_out += 1
        stats.completion_overhead_ns = max(0, self._clock() - t_final)
        stats.done = True
        self._metrics.observe_stream_complete(stats)
        return stats

    # -- terminal paths (mirror StreamPipeline._terminate / _cut_on_kill) --- #

    async def _cut_on_kill(self, send: Send, stats: StrictStats) -> StrictStats:
        """Cut the in-flight stream per ``InFlightKill.CUT_NEXT_CHUNK`` (R7 parity).

        Reached when ``killed()`` is observed at a chunk boundary before the whole
        response has been inspected. Emission ceases at this boundary (R7.1); no
        content frame is emitted (R7.2); every accumulated buffer is discarded,
        never released (R7.3) -- discard is structural, this returns before
        ``_finalize`` is ever reached. The ``killed`` Terminal_Error_Frame signals
        the caller the stream was ended by a kill action and ``stats.error``
        records ``"killed"`` (R7.4), mirroring ``StreamPipeline._cut_on_kill``.
        """
        if _KILL_MODE is not InFlightKill.CUT_NEXT_CHUNK:  # pragma: no cover - lock
            raise AssertionError(f"unsupported in-flight-kill mode: {_KILL_MODE!r}")
        return await self._terminate(send, stats, error=_KILLED_CODE)

    async def _overflow(self, send: Send, stats: StrictStats) -> StrictStats:
        """Fail closed on a buffer that exceeded ``max_buffer_bytes`` (R9.5).

        Emits the terminal ``strict_buffer_overflow`` frame (the one new spelling,
        from ``posture.STREAM_STRICT_BUFFER_OVERFLOW``), releases zero content, and
        records the overflow on the dedicated STRICT counter.
        """
        self._metrics.observe_overflow()
        await send(
            DownstreamFrame(
                text_deltas=(),
                error_code=posture.STREAM_STRICT_BUFFER_OVERFLOW,
            ),
        )
        stats.incomplete = True
        stats.error = posture.STREAM_STRICT_BUFFER_OVERFLOW
        return stats

    async def _terminate(
        self,
        send: Send,
        stats: StrictStats,
        *,
        error: str,
    ) -> StrictStats:
        """Emit the Terminal_Error_Frame and record the abnormal termination.

        A ``DownstreamFrame`` carrying ``error_code`` and no content is the
        Terminal_Error_Frame; it is the LAST frame emitted for the stream, so no
        content frame can follow it (R3.4 / R7.4). The invariant is structural:
        ``_run_enforcing`` / ``_finalize`` return immediately after this call. The
        BLOCK path routes through ``observe_blocked`` so the dedicated STRICT
        block counter is bumped; every other terminal routes through
        ``observe_stream_incomplete`` to record the completion overhead-to-date
        and increment the incomplete counter (mirroring the shipped pipeline).
        """
        await send(DownstreamFrame(text_deltas=(), error_code=error))
        stats.incomplete = True
        stats.error = error
        if error == _OUTPUT_BLOCKED_CODE:
            self._metrics.observe_blocked()
        else:
            self._metrics.observe_stream_incomplete(stats)
        return stats
