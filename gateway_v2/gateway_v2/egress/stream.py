"""Thin, injectable streaming holdback harness (R2-06 / GW12b).

``StreamPipeline`` is the streaming egress component (the ``Stream_Pipeline`` of
the requirements): it parses upstream chunks, drives the holdback scanner per
text stream, applies the output ``Decision`` to released bytes, re-serialises
downstream frames, and records the separated latency / held-token observability.

**Deliberately thin and injectable.** The scanner, the output resolver, the
token counter, the config, the metrics producer, and the clock are all injected
through ``__init__``. Tests wire the real ``gateway_v2.detect.holdback.scan`` /
``TokenIndex`` / ``windowing.max_pattern_length()`` in by injection; GW13 later
wires this same harness to the real SSE state machine and backpressure.

Layering (import-linter ``layers`` contract in ``pyproject.toml``). ``detect``
sits ABOVE ``egress``, so this module MUST NOT import ``gateway_v2.detect`` --
no ``from gateway_v2.detect.holdback import ...``. The scanner, token counter,
and window value are therefore INJECTED, described here only by the structural
protocols :class:`ScanProtocol`, :class:`ScanResultLike`, and
:class:`TokenCounterProtocol`. This module imports only ``gateway_v2.domain``,
``gateway_v2.runtime`` (``HoldbackConfig`` / ``HoldbackMetrics``), and the
sibling ``gateway_v2.egress.output_guard`` -- never ``detect``. The test files,
which are not under the layer contract, import the concrete ``detect`` scanner
and inject it.

**Scope of this file (tasks 7.1-7.3, 9.1).** It defines the transport value
types, the exception vocabulary, the per-text-stream state (``_Text``), the
per-stream reading (``StreamStats``), the enforcing-output gating, the release
loop, the latency / held-token metrics (tasks 7.1-7.3), the force-release
trade-off state machine with its fail-closed terminal error frames (task 9.1),
and the in-flight-kill handling (task 10): ``run`` checks ``killed()`` at each
chunk boundary and, on a kill, cuts per ``InFlightKill.CUT_NEXT_CHUNK`` -- it
ceases emission at that boundary, discards still-held bytes, and emits a
``killed`` Terminal_Error_Frame (R7).

**Trade-off outcome mirror (task 9.1).** The per-pattern Trade_Off_Outcome lives
authoritatively in ``detect.holdback.TRADE_OFF``, which ``egress`` cannot import.
The sibling ``egress.holdback_tradeoff`` module is the egress-side mirror: it
derives each outcome from the resolver's ``Decision`` and folds a force-released
match's still-held remainder into the output ``Decision`` as a redaction. A test
pins that the derived outcome agrees with ``detect.holdback.TRADE_OFF``.

**Match source seam (task 9.1, GW07/GW08 drop-in).** The thin harness has no real
detector feeding ``_Text.hits``, so a completed-match source is INJECTED as the
optional ``detector`` callable: given the full accumulated text of a channel it
returns completed ``Finding``s with absolute spans. ``None`` keeps the thin
harness unchanged (tasks 7.* still pass). It is re-run on the WHOLE accumulated
text each delta so a match that straddles a release boundary or completes only
after force-release is seen whole (Properties 5 and 6). GW07/GW08's detector
drops in here.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from gateway_v2.domain import (
    ExecutionPlan,
    Finding,
    InFlightKill,
    PlanUnavailable,
    PlanUnknownTenant,
)
from gateway_v2.egress.holdback_tradeoff import (
    TRADE_OFF_OUTCOMES,
    TRADE_OFF_REDACT_REMAINDER,
    TRADE_OFF_TERMINATE,
    ForcedReleaseTerminate,
    completed_in,
    derive_trade_off_outcome,
    longest_unbroken_run_bytes,
    with_remainder_redactions,
)
from gateway_v2.egress.output_guard import (
    OutputBlocked,
    OutputResolver,
    apply_decision,
    enforcing_output_rules,
)
from gateway_v2.runtime.holdback_config import HoldbackConfig
from gateway_v2.runtime.holdback_metrics import HoldbackMetrics

__all__ = (
    "ChunkSource",
    "Detector",
    "DownstreamFrame",
    "EnforcementUndetermined",
    "HoldbackOverflow",
    "KillSignal",
    "OutputBlocked",
    "ScanFailure",
    "ScanProtocol",
    "ScanResultLike",
    "Send",
    "StreamPipeline",
    "StreamStats",
    "TRADE_OFF_OUTCOMES",
    "TRADE_OFF_REDACT_REMAINDER",
    "TRADE_OFF_TERMINATE",
    "TokenCounterProtocol",
    "TradeOffUndefined",
    "UpstreamChunk",
    "Undetermined",
    "UNDETERMINED",
    "UpstreamPlan",
)


# The force-release trade-off outcome mirror (``TRADE_OFF_*``), the terminate
# escalation (``ForcedReleaseTerminate``), the outcome derivation, and the
# remainder-redaction folding live in the sibling ``egress.holdback_tradeoff``
# module (imported above) so this module stays focused on the release loop. The
# ``TRADE_OFF_*`` names are re-exported here for callers and tests.


# --------------------------------------------------------------------------- #
# Transport value types (task 7.1).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class UpstreamChunk:
    """One upstream delivery: a delta per text stream, plus the final flag.

    ``text_deltas`` is a tuple of ``(channel_name, delta_text)`` pairs -- one per
    text stream carried in this chunk. ``final`` is set on the chunk that carries
    the last token of the stream, at which point all held bytes are flushed
    (R3.5).
    """

    text_deltas: tuple[tuple[str, str], ...]
    final: bool


@dataclass(frozen=True, slots=True)
class DownstreamFrame:
    """One downstream delivery of released (post-decision) bytes.

    ``error_code`` is ``None`` for an ordinary content frame. A set ``error_code``
    marks a ``Terminal_Error_Frame``: the stream stops and no further content
    frame is emitted after it (R3.4/R3.6/R6.*). Tasks 9 and 10 populate the
    terminal codes (``forced_release_tradeoff``, ``holdback_overflow``,
    ``undefined_tradeoff``, ``scan_failure``, ``output_blocked``, ``killed``).
    """

    text_deltas: tuple[tuple[str, str], ...]
    error_code: str | None = None


# --------------------------------------------------------------------------- #
# Injection aliases (task 7.1).
# --------------------------------------------------------------------------- #

#: The injected downstream sink. ``run`` awaits it for every frame it emits.
Send = Callable[[DownstreamFrame], Awaitable[None]]
#: The injected upstream source of chunks.
ChunkSource = AsyncIterator[UpstreamChunk]
#: The injected kill probe. ``True`` means the owning org has been killed; the
#: pipeline checks it at each chunk boundary (``InFlightKill.CUT_NEXT_CHUNK``).
KillSignal = Callable[[], bool]
#: The injected completed-match source (task 9.1, GW07/GW08 drop-in). Given the
#: full accumulated text of a channel it returns the completed ``Finding``s with
#: ABSOLUTE spans over the whole text stream. ``None`` means the thin harness (no
#: detector): no findings, ALLOW decision, identity redaction. The real GW07/GW08
#: detector satisfies this signature and drops in behind it unchanged.
Detector = Callable[[str], Sequence[Finding]]


# --------------------------------------------------------------------------- #
# Structural protocols for the INJECTED scanner / token counter (task 7.1).
#
# These describe exactly what the pipeline needs from `gateway_v2.detect` WITHOUT
# importing it (the layer contract forbids an egress -> detect edge). The real
# `detect.holdback.ScanResult` / `scan` / `TokenIndex` satisfy these structurally
# and are injected by the tests.
# --------------------------------------------------------------------------- #


@runtime_checkable
class ScanResultLike(Protocol):
    """The structural shape of a scanner result the pipeline reads.

    Mirrors ``detect.holdback.ScanResult`` field-for-field without importing it.
    ``hold_index`` is the earliest held byte (``buf[:hold_index]`` is releasable);
    ``held_class`` is the pattern class forcing the hold (or ``None``);
    ``is_relaxed`` is whether that class is relaxed; ``forced_release`` /
    ``forced_release_index`` carry the word-class cap force-release accounting;
    ``overflow`` signals a relaxed-class ceiling breach (handled by task 9).
    """

    @property
    def hold_index(self) -> int: ...
    @property
    def held_class(self) -> str | None: ...
    @property
    def is_relaxed(self) -> bool: ...
    @property
    def forced_release(self) -> bool: ...
    @property
    def forced_release_index(self) -> int: ...
    @property
    def overflow(self) -> bool: ...


class ScanProtocol(Protocol):
    """The injected scanner: a callable matching the pure holdback-scan core.

    Mirrors ``detect.holdback.scan`` exactly (a pure function of the pending
    buffer, the final flag, the hold cap, the injected token counter, and the
    window). The ``gateway_v2.detect.holdback.scan`` *function* satisfies this
    structurally, so a test injects it directly.

    It is a plain callable, not an object with a ``.scan`` attribute, on purpose:
    the data-plane tenant-scale gate forbids an attribute call named ``scan`` (a
    Redis ``SCAN`` over a whole collection), and the holdback scan is not a store
    read. The Callable form describes the same surface free of that false-positive.
    """

    def __call__(
        self,
        buf: str,
        *,
        final: bool,
        hold_cap_tokens: int,
        token_index: TokenCounterProtocol,
        window: int,
    ) -> ScanResultLike: ...


@runtime_checkable
class TokenCounterProtocol(Protocol):
    """The injected upstream-token counter.

    Mirrors ``detect.holdback.TokenIndex``: ``tokens_in`` counts the held
    Upstream_Tokens in a slice (the quantity the cap and histogram bound), and
    ``token_boundaries`` gives the start index of each token run so the oldest
    tokens can be force-released.
    """

    def tokens_in(self, s: str) -> int: ...
    def token_boundaries(self, s: str) -> list[int]: ...


# --------------------------------------------------------------------------- #
# Exceptions (task 7.1).
# --------------------------------------------------------------------------- #


class HoldbackOverflow(Exception):
    """A relaxed-class hold reached the ``2 * hold_cap`` ceiling (R6.5).

    Filled in by task 9; defined here so the vocabulary is complete and the
    scanner's ``overflow`` signal has a home.
    """


class TradeOffUndefined(Exception):
    """An over-cap pattern has no declared trade-off outcome (R6.6). Task 9."""


class ScanFailure(Exception):
    """The injected scanner raised while scanning the pending buffer (R3.6)."""


class EnforcementUndetermined(Exception):
    """The plan could not be resolved before the first chunk (R2.4).

    Raised before any byte is read or released, so the stream is withheld
    entirely and the caller learns the output-enforcement state is undetermined.
    """


# --------------------------------------------------------------------------- #
# Undetermined-plan sentinel (task 7.2 gating).
# --------------------------------------------------------------------------- #


class Undetermined:
    """A sentinel the caller passes to ``for_plan`` when the plan is unresolved.

    Distinct from ``PlanUnavailable`` / ``PlanUnknownTenant`` (which the caller
    may also pass): any of the three means the enforcing-output state cannot be
    determined, so ``for_plan`` raises ``EnforcementUndetermined`` and the stream
    releases nothing (R2.4).
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "UNDETERMINED"


#: The shared undetermined sentinel instance.
UNDETERMINED = Undetermined()

#: The only in-flight-kill semantics this pipeline implements (owner lock,
#: ``domain.locks``): cut emission no later than the next upstream chunk boundary
#: (R7). Held authoritatively in ``domain``; named here so the kill handler reads
#: against the lock rather than an ad-hoc literal.
_KILL_MODE: InFlightKill = InFlightKill.CUT_NEXT_CHUNK

#: What ``for_plan`` accepts for the pinned plan: a resolved ``ExecutionPlan`` or
#: one of the unresolved markers that force ``EnforcementUndetermined``.
UpstreamPlan = ExecutionPlan | PlanUnavailable | PlanUnknownTenant | Undetermined


# --------------------------------------------------------------------------- #
# Per-text-stream state (task 7.1).
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Text:
    """Mutable per-text-stream holdback state.

    ``pending`` is the unreleased buffer tail (held bytes). ``base_offset`` is the
    absolute index of ``pending[0]`` within the whole text stream, so a released
    slice can be given an absolute base offset for the ``Decision``.
    ``held_since_ns`` is the clock reading when the OLDEST currently-held byte
    first entered ``pending`` -- the ``oldest_arrival`` used for the holdback-wait
    decomposition. ``released`` counts bytes already written downstream.
    ``max_held_tokens`` / ``max_unbroken_run_bytes`` are the per-stream maxima
    folded into the histogram sample. ``hits`` accumulates completed findings
    (populated by the injected detector in task 9.1; empty in the thin harness).
    ``whole`` is the full accumulated text of the stream (every delta ever
    appended), so the injected detector can classify a match against the whole
    buffer -- essential for a match that straddles a release boundary or that
    completes only after the hold cap force-released its prefix (task 9.1).
    ``forced_release_end`` is the absolute index up to which bytes were released
    EARLY because the word-class token cap forced release past the minimal
    suffix; a completed match whose span starts before this index is a
    force-release trade-off (R6.2-R6.4).
    """

    pending: str = ""
    whole: str = ""
    base_offset: int = 0
    held_since_ns: int = 0
    released: int = 0
    forced_release_end: int = 0
    max_held_tokens: int = 0
    max_unbroken_run_bytes: int = 0
    hits: list[Finding] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Per-stream reading (task 7.1).
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class StreamStats:
    """The per-stream reading the pipeline hands to the metrics producer.

    Structurally satisfies ``runtime.holdback_metrics.StreamStatsLike``
    (``max_held_tokens``, ``release_processing_ns``, ``holdback_wait_ns``,
    ``max_unbroken_run_bytes``). ``release_processing_ns`` and ``holdback_wait_ns``
    are the two SEPARATE latency series (R4.6/R4.7/R5.3): gateway compute vs.
    time a released byte waited on disambiguating upstream bytes. ``incomplete``
    flags an abnormal termination (R5.2); ``error`` carries the terminal code.
    """

    upstream_chunks: int = 0
    frames_out: int = 0
    max_held_tokens: int = 0
    max_unbroken_run_bytes: int = 0
    release_processing_ns: int = 0
    holdback_wait_ns: int = 0
    done: bool = False
    incomplete: bool = False
    error: str | None = None


# --------------------------------------------------------------------------- #
# StreamPipeline (tasks 7.2 + 7.3).
# --------------------------------------------------------------------------- #


class StreamPipeline:
    """Drive the holdback scanner over an injected chunk stream.

    The enforcing-output gating decision (R2.3) belongs to the CALLER / plan
    resolution: it is computed once, before the first chunk, from
    ``output_guard.enforcing_output_rules(plan)``. Use :meth:`for_plan` so that
    "determine before the first chunk" contract is explicit and the undetermined
    plan is rejected before any byte is read. ``__init__`` takes the already
    resolved ``enforcing_output`` boolean for callers (and GW13) that resolved it
    elsewhere.
    """

    def __init__(
        self,
        *,
        scanner: ScanProtocol,
        resolver: OutputResolver,
        token_index: TokenCounterProtocol,
        cfg: HoldbackConfig,
        metrics: HoldbackMetrics,
        enforcing_output: bool,
        window: int,
        clock: Callable[[], int] = time.perf_counter_ns,
        detector: Detector | None = None,
    ) -> None:
        self._scanner = scanner
        self._resolver = resolver
        self._token_index = token_index
        self._cfg = cfg
        self._metrics = metrics
        self._enforcing_output = enforcing_output
        self._window = window
        self._clock = clock
        self._detector = detector

    @classmethod
    def for_plan(
        cls,
        plan: UpstreamPlan,
        *,
        scanner: ScanProtocol,
        resolver: OutputResolver,
        token_index: TokenCounterProtocol,
        cfg: HoldbackConfig,
        metrics: HoldbackMetrics,
        window: int,
        clock: Callable[[], int] = time.perf_counter_ns,
        detector: Detector | None = None,
    ) -> StreamPipeline:
        """Build a pipeline, resolving ``enforcing_output`` from the plan (R2.3).

        The enforcing-output state is computed ONCE here, before the first chunk
        is read, from ``enforcing_output_rules(plan)`` -- a non-empty result means
        the stream engages the cap and byte-ceiling (R2.1); an empty result means
        pass-through (R2.2).

        If the plan is unresolved -- an ``Undetermined`` sentinel, a
        ``PlanUnavailable``, or a ``PlanUnknownTenant`` -- this raises
        ``EnforcementUndetermined`` before constructing the pipeline, so the
        stream is withheld and no byte is ever released (R2.4, fail-closed: an
        undetermined enforcement state never defaults to pass-through).
        """
        if isinstance(plan, (Undetermined, PlanUnavailable, PlanUnknownTenant)):
            raise EnforcementUndetermined(
                "output-enforcement state is undetermined; the plan could not be resolved",
            )
        enforcing_output = bool(enforcing_output_rules(plan))
        return cls(
            scanner=scanner,
            resolver=resolver,
            token_index=token_index,
            cfg=cfg,
            metrics=metrics,
            enforcing_output=enforcing_output,
            window=window,
            clock=clock,
            detector=detector,
        )

    async def run(
        self,
        chunks: ChunkSource,
        send: Send,
        killed: KillSignal,
    ) -> StreamStats:
        """Consume ``chunks``, hold / release / redact, and emit downstream frames.

        Returns the per-stream ``StreamStats`` reading after the stream ends.
        When ``enforcing_output`` is false the stream passes through unheld
        (R2.2); otherwise it drives the holdback scanner per text stream, applies
        the ``Decision`` to released bytes, and flushes everything on the final
        chunk (R3.5).

        Force-release trade-off handling (task 9) and in-flight-kill handling
        (task 10) are marked extension points below; the ``killed()`` probe is
        already called at each chunk boundary as a no-op-safe hook.
        """
        stats = StreamStats()
        if not self._enforcing_output:
            return await self._run_passthrough(chunks, send, stats)
        return await self._run_enforcing(chunks, send, killed, stats)

    # -- pass-through (no enforcing rule, R2.2) ----------------------------- #

    async def _run_passthrough(
        self,
        chunks: ChunkSource,
        send: Send,
        stats: StreamStats,
    ) -> StreamStats:
        """Release every chunk unheld; hold nothing (R2.2)."""
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

    # -- enforcing (hold cap + byte ceiling, R2.1 / R3) --------------------- #

    async def _run_enforcing(
        self,
        chunks: ChunkSource,
        send: Send,
        killed: KillSignal,
        stats: StreamStats,
    ) -> StreamStats:
        """Drive the scanner per text stream, release and redact, flush on final."""
        texts: dict[str, _Text] = {}
        try:
            async for chunk in chunks:
                stats.upstream_chunks += 1

                # In-flight kill (task 10, R7). Checked at the chunk boundary --
                # the delivery of one complete upstream chunk -- so the cut takes
                # effect no later than the NEXT boundary (InFlightKill.CUT_NEXT_CHUNK,
                # R7.1). Because the check runs BEFORE this chunk is processed, zero
                # additional upstream chunks are emitted after the cut (R7.2); the
                # held bytes still in `texts` are never released, so they are
                # discarded (R7.3). A kill that flips true only AFTER the final
                # chunk was processed is never seen (the loop broke on `final`), so
                # a done stream completes normally and retracts nothing (R7.5).
                if killed():
                    return await self._cut_on_kill(send, stats)

                seen: set[str] = set()
                for channel, delta in chunk.text_deltas:
                    seen.add(channel)
                    text = texts.get(channel)
                    if text is None:
                        text = _Text()
                        texts[channel] = text
                    out = self._process_delta(text, channel, delta, final=chunk.final, stats=stats)
                    await self._emit(out, send, stats)

                if chunk.final:
                    # Flush any text stream that carried bytes earlier but was not
                    # present in the final chunk's deltas (R3.5: release all held
                    # bytes on the final token).
                    for channel, text in texts.items():
                        if channel in seen or not text.pending:
                            continue
                        out = self._process_delta(text, channel, "", final=True, stats=stats)
                        await self._emit(out, send, stats)
                    break
        except ScanFailure:
            # Scanner failed: retain all pending, emit nothing further (R3.6).
            return await self._terminate(send, stats, error="scan_failure")
        except OutputBlocked:
            # Completed match blocked: withhold unreleased bytes (R3.4).
            return await self._terminate(send, stats, error="output_blocked")
        except HoldbackOverflow:
            # Relaxed-class ceiling: release no held bytes of the match (R6.5).
            return await self._terminate(send, stats, error="holdback_overflow")
        except ForcedReleaseTerminate:
            # Over-cap match declared terminate-the-stream (R6.4).
            return await self._terminate(send, stats, error="forced_release_tradeoff")
        except TradeOffUndefined:
            # Over-cap pattern with no declared outcome: fail closed (R6.6).
            return await self._terminate(send, stats, error="undefined_tradeoff")
        except Exception:
            stats.incomplete = True
            self._metrics.observe_stream_incomplete(stats)
            raise

        stats.done = True
        self._metrics.observe_stream_complete(stats)
        return stats

    def _process_delta(
        self,
        text: _Text,
        channel: str,
        delta: str,
        *,
        final: bool,
        stats: StreamStats,
    ) -> tuple[DownstreamFrame, int] | None:
        """Append ``delta``, scan, and emit the slice it releases.

        Returns ``(frame, t_ready)`` -- the released (post-decision) bytes and the
        clock reading once scanned+resolved; the caller stamps ``t_sent`` after
        the send to close ``T_release_processing``. Returns ``None`` when nothing
        is releasable this delta. ``T_holdback_wait = t_ready - oldest_arrival``
        is accumulated here (Property 8). No left-context carry is required: the
        scanner holds the minimal matchable suffix, so bytes before ``hold_index``
        are final and dropping exactly ``hold_index`` keeps Released_Bytes equal
        to the input minus redactions (Property 4) and chunk-split invariant.
        """
        now = self._clock()
        if not text.pending:
            # Fresh hold: these bytes arrive now and become the oldest held byte.
            text.held_since_ns = now
        oldest_arrival = text.held_since_ns
        text.pending += delta
        text.whole += delta

        try:
            result = self._scanner(
                text.pending,
                final=final,
                hold_cap_tokens=self._cfg.hold_cap_tokens,
                token_index=self._token_index,
                window=self._window,
            )
        except Exception as exc:  # scanner failure -> fail-closed (R3.6)
            raise ScanFailure(str(exc)) from exc

        # Refresh the completed-match set against the WHOLE accumulated buffer so
        # a match that straddles a release boundary -- or that completes only
        # after the cap force-released its prefix -- is seen whole (task 9.1).
        if self._detector is not None:
            text.hits = list(self._detector(text.whole))

        # Relaxed-class overflow (R6.5): fail closed, release NO in-progress bytes
        # (the raise unwinds before any release below).
        if result.overflow:
            self._metrics.observe_overflow()
            raise HoldbackOverflow(
                f"relaxed-class hold exceeded the ceiling (class={result.held_class})",
            )
        if result.forced_release:
            self._metrics.observe_forced_release()

        hold_index = len(text.pending) if final else result.hold_index
        released = text.pending[:hold_index]
        held_suffix = text.pending[hold_index:]

        # Record the absolute boundary of a cap-forced EARLY release so a later
        # completed match whose span starts before it is a trade-off (R6.2-R6.4).
        if result.forced_release and result.forced_release_index < hold_index:
            text.forced_release_end = text.base_offset + hold_index

        self._record_maxima(text, held_suffix, stats)

        if not released:
            # Nothing releasable: the whole delta stays held; its holdback wait
            # keeps accruing and is attributed when it releases.
            return None

        emitted = self._resolve_released(text, released)
        t_ready = self._clock()

        # Drop the released bytes from pending; the remaining tail is held.
        text.released += hold_index
        text.base_offset += hold_index
        text.pending = held_suffix
        text.held_since_ns = t_ready  # the fresh tail is now the oldest held byte

        stats.holdback_wait_ns += max(0, t_ready - oldest_arrival)
        return DownstreamFrame(text_deltas=((channel, emitted),)), t_ready

    def _record_maxima(self, text: _Text, held_suffix: str, stats: StreamStats) -> None:
        """Fold the per-stream held-token / unbroken-run maxima (R5.1/R5.5)."""
        held_tokens = self._token_index.tokens_in(held_suffix)
        text.max_held_tokens = max(text.max_held_tokens, held_tokens)
        run_bytes = longest_unbroken_run_bytes(held_suffix)
        text.max_unbroken_run_bytes = max(text.max_unbroken_run_bytes, run_bytes)
        stats.max_held_tokens = max(stats.max_held_tokens, text.max_held_tokens)
        stats.max_unbroken_run_bytes = max(
            stats.max_unbroken_run_bytes, text.max_unbroken_run_bytes
        )

    def _resolve_released(self, text: _Text, released: str) -> str:
        """Apply the output Decision + force-release trade-off to a released slice.

        A force-released match's declared outcome applies (terminate raises;
        redact-the-remainder folds the still-held remainder span into the SAME
        single-pass redaction as the normal decision, keeping offsets absolute).
        ``apply_decision`` raises ``OutputBlocked`` on BLOCK and fails closed on an
        uncovered span.
        """
        release_start = text.base_offset
        release_end = text.base_offset + len(released)
        remainder_edits = self._forced_release_remainder_edits(
            text, release_start, release_end,
        )
        matches = completed_in(text.hits, release_start, len(released))
        decision = self._resolver.decide(matches)
        decision = with_remainder_redactions(decision, remainder_edits)
        return apply_decision(released, decision, release_start)

    # -- force-release trade-off helper (task 9.1) ------------------------- #

    def _forced_release_remainder_edits(
        self,
        text: _Text,
        release_start: int,
        release_end: int,
    ) -> tuple[tuple[int, int], ...]:
        """Absolute remainder spans to redact for force-release trade-offs.

        A completed match whose span starts before ``text.forced_release_end`` had
        its prefix released early by the cap (R6.2). Its declared outcome applies:
        ``terminate`` raises ``ForcedReleaseTerminate`` (R6.4); ``redact_remainder``
        yields the still-held remainder span inside THIS slice to redact before any
        of it is written (R6.3; the prefix was already redacted at its own release
        point, so Released_Bytes never carries an unredacted match, Property 1);
        no derivable outcome raises ``TradeOffUndefined`` (R6.6, fail closed).
        """
        if text.forced_release_end <= 0 or not text.hits:
            return ()
        edits: list[tuple[int, int]] = []
        for finding in text.hits:
            for span in finding.spans:
                if span.start >= text.forced_release_end:
                    continue  # prefix was not force-released; normal path handles it
                # This match's prefix was released early -> trade-off applies.
                outcome = derive_trade_off_outcome(self._resolver.decide((finding,)))
                if outcome == TRADE_OFF_TERMINATE:
                    raise ForcedReleaseTerminate(finding.detector)
                if outcome != TRADE_OFF_REDACT_REMAINDER:
                    # Over-cap pattern with no declared outcome -> fail closed.
                    raise TradeOffUndefined(
                        f"over-cap pattern has no declared trade-off outcome "
                        f"(class={finding.detector})",
                    )
                # Redact the still-held remainder of the match that lies within
                # this release slice: [max(span.start, release_start), span.end).
                rem_start = max(span.start, release_start)
                rem_end = min(span.end, release_end)
                if rem_start < rem_end:
                    edits.append((rem_start, rem_end))
        return tuple(edits)

    async def _emit(
        self,
        out: tuple[DownstreamFrame, int] | None,
        send: Send,
        stats: StreamStats,
    ) -> None:
        """Send a released frame and close its release-processing measurement.

        ``T_release_processing = t_sent - t_ready`` (R4.6) is stamped here, after
        the awaited send, so the send latency is attributed to gateway compute
        and kept separate from the holdback wait (Property 8).
        """
        if out is None:
            return
        frame, t_ready = out
        await send(frame)
        stats.frames_out += 1
        t_sent = self._clock()
        stats.release_processing_ns += max(0, t_sent - t_ready)

    async def _terminate(
        self,
        send: Send,
        stats: StreamStats,
        *,
        error: str,
    ) -> StreamStats:
        """Emit the Terminal_Error_Frame and record the abnormal termination.

        A ``DownstreamFrame`` carrying ``error_code`` and no content is the
        Terminal_Error_Frame; it is the LAST frame emitted for the stream, so no
        content frame can follow it (R3.4/R3.6/R6.4/R6.5/R6.6). The invariant that
        no content frame follows a terminal frame is structural: ``_run_enforcing``
        returns immediately after this call.
        """
        await send(DownstreamFrame(text_deltas=(), error_code=error))
        return self._finish_incomplete(stats, error=error)

    async def _cut_on_kill(self, send: Send, stats: StreamStats) -> StreamStats:
        """Cut the in-flight stream per ``InFlightKill.CUT_NEXT_CHUNK`` (R7).

        Reached when ``killed()`` is observed at a chunk boundary while bytes may
        still be held. Emission ceases at this boundary (R7.1); no further upstream
        chunk is emitted (R7.2); every still-held byte that has not already passed
        the output ``Decision`` is discarded, never released (R7.3) -- discard is
        structural, this returns before any further ``_process_delta`` release. A
        ``killed`` Terminal_Error_Frame signals the caller the stream was ended by
        a kill action and ``stats.error`` records ``"killed"`` (R7.4); the stream
        terminates within this loop iteration, trivially inside the 1 s contractual
        bound GW13 must honour against a real socket. The terminal-frame discipline
        is preserved: this is the last frame, no content frame can follow it.
        """
        if _KILL_MODE is not InFlightKill.CUT_NEXT_CHUNK:  # pragma: no cover - lock
            raise AssertionError(f"unsupported in-flight-kill mode: {_KILL_MODE!r}")
        return await self._terminate(send, stats, error="killed")

    def _finish_incomplete(self, stats: StreamStats, *, error: str) -> StreamStats:
        """Record an abnormal termination and return the reading (R5.2)."""
        stats.incomplete = True
        stats.error = error
        self._metrics.observe_stream_incomplete(stats)
        return stats
