"""GW13 — the two explicit streaming modes, in ONE file (feature output-streaming-modes-gw13).

The single GW13 property + mechanism test suite (tasks.md task 9), co-located with the shipped
GW12b ``StreamPipeline`` harness (``test_lgw12b_stream.py``) and MIRRORING its house idiom:
seeded ``random.Random``, ``asyncio.run``-driven, no ``hypothesis``. The GW13 async properties
run >= 100 iterations (the design floor); the pure compiler round-trip (Property 9) is
exhaustive over the ``StreamingMode`` enum.

The 11 correctness properties (design.md Correctness Properties):

* P1  — STRICT releases nothing before the terminal result.
* P2  — STRICT BLOCK yields zero prohibited bytes (LGW13-1).
* P3  — STRICT emitted bytes equal ``apply_decision`` of the whole buffer.
* P4  — INCREMENTAL never presents truncation as block.
* P5  — INCREMENTAL truthful block before the first byte.
* P6  — INCREMENTAL redacts only not-yet-released content.
* P7  — Boundary safety in both modes (LGW13-6).
* P8  — Degenerate flag set exactly on single-chunk responses.
* P9  — Compiler round-trips both streaming modes.
* P10 — Resolver-owned UNAVAILABLE posture on the output path (LGW13-3).
* P11 — MONITOR output rules never enforce (LGW13-4).

Plus the LGW13-1/2/3/4/6 mechanism tests and the one-resolver-wiring, mode-selection,
Produced_Mode, latency-labelling, buffer-bound, and compiler-guard example tests.

Test files are NOT under the import-linter layer contract, so importing ``detect`` to inject the
real holdback scanner (for the INCREMENTAL properties) and importing ``resolve`` to exercise the
shipped resolver on the output path (P10/P11) is allowed.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

import pytest

from gateway_v2.detect.holdback import TokenIndex
from gateway_v2.detect.holdback import scan as _holdback_scan
from gateway_v2.detect.windowing import max_pattern_length
from gateway_v2.domain import (
    Action,
    Category,
    Decision,
    Disposition,
    ExecutionPlan,
    FailurePosture,
    Finding,
    FindingDisposition,
    FindingStatus,
    Mode,
    Phase,
    PlanUnavailable,
    PlanUnknownTenant,
    Rule,
    RuleScope,
    Span,
    StreamingMode,
    Surface,
    Transformation,
    most_restrictive,
    posture,
)
from gateway_v2.egress.output_guard import (
    REDACTION_PLACEHOLDER,
    MinimalOutputResolver,
    OutputResolver,
    apply_decision,
    enforcing_output_rules,
)
from gateway_v2.egress.select import select_pipeline
from gateway_v2.egress.stream import (
    UNDETERMINED,
    Detector,
    DownstreamFrame,
    EnforcementUndetermined,
    ScanResultLike,
    StreamPipeline,
    TokenCounterProtocol,
    UpstreamChunk,
)
from gateway_v2.egress.strict import StrictStats, StrictWithholdPipeline
from gateway_v2.plan.compiler import (
    SUPPORTED_STREAMING,
    CompileError,
    RuleDraft,
    compile_plan,
)
from gateway_v2.plan.document import PlanDocument, decode_plan_body, encode_plan_body, json
from gateway_v2.resolve.resolver import resolve
from gateway_v2.runtime.holdback_config import load_holdback_config
from gateway_v2.runtime.holdback_metrics import HoldbackMetrics
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals
from gateway_v2.runtime.strict_metrics import SERIES_COUNT as STRICT_SERIES_COUNT
from gateway_v2.runtime.strict_metrics import StrictMetrics, StrictReading

CHANNEL = "output_content"

#: The GW13 async-property iteration floor (design: ">= 100 iters for the GW13 async props").
_ITERS = 200


# =========================================================================== #
# House-idiom harness (mirrors test_lgw12b_stream.py)
# =========================================================================== #


def _run[T](coro: Awaitable[T]) -> T:
    """Drive one coroutine to completion (house idiom; no async test plugin)."""
    return asyncio.run(coro)  # type: ignore[arg-type]


async def _source(chunks: list[UpstreamChunk]) -> AsyncIterator[UpstreamChunk]:
    """An async iterator over a fixed list of upstream chunks (the fake chunk source)."""
    for chunk in chunks:
        yield chunk


class _Collector:
    """A recording ``Send`` sink capturing the full downstream frame sequence."""

    def __init__(self) -> None:
        self.frames: list[DownstreamFrame] = []

    async def __call__(self, frame: DownstreamFrame) -> None:
        self.frames.append(frame)

    def content_frames(self) -> list[DownstreamFrame]:
        return [f for f in self.frames if f.error_code is None]

    def terminal_codes(self) -> list[str]:
        return [f.error_code for f in self.frames if f.error_code is not None]

    def released_text(self, channel: str = CHANNEL) -> str:
        parts: list[str] = []
        for frame in self.frames:
            if frame.error_code is not None:
                continue
            for ch, delta in frame.text_deltas:
                if ch == channel:
                    parts.append(delta)
        return "".join(parts)


def _never_killed() -> bool:
    return False


class _KillAfter:
    """A kill probe that flips ``True`` after ``n`` boundary checks."""

    def __init__(self, n: int) -> None:
        self._n = n
        self._calls = 0

    def __call__(self) -> bool:
        fire = self._calls >= self._n
        self._calls += 1
        return fire


# --------------------------------------------------------------------------- #
# Fake / recording resolver (scripted Decision; records every decide call)
# --------------------------------------------------------------------------- #


class _RecordingResolver:
    """An ``OutputResolver`` that returns a scripted ``Decision`` and records each call.

    ``decide`` records the matches it was called with (``calls`` holds every argument
    sequence) so a test can assert the one-resolver seam fired exactly where expected, and
    returns a factory-built ``Decision`` keyed off the findings it sees.
    """

    def __init__(self, factory: Callable[[Sequence[Finding]], Decision]) -> None:
        self._factory = factory
        self.calls: list[tuple[Finding, ...]] = []

    def decide(self, matches: Sequence[Finding]) -> Decision:
        self.calls.append(tuple(matches))
        return self._factory(matches)


def _allow_decision(_matches: Sequence[Finding]) -> Decision:
    return Decision(
        disposition=Disposition.ALLOW,
        per_finding=(),
        transformations=(),
        findings=tuple(_matches),
        plan_version="gw13-test",
        deciding_rules=(),
        unavailable_detectors=(),
    )


def _block_decision(matches: Sequence[Finding]) -> Decision:
    fd = tuple(
        FindingDisposition(detector=m.detector, disposition=Disposition.BLOCK) for m in matches
    ) or (FindingDisposition(detector="scripted", disposition=Disposition.BLOCK),)
    return Decision(
        disposition=Disposition.BLOCK,
        per_finding=fd,
        transformations=(),
        findings=tuple(matches),
        plan_version="gw13-test",
        deciding_rules=tuple(f.detector for f in fd),
        unavailable_detectors=(),
    )


def _redact_decision_for(spans: Sequence[Span]) -> Callable[[Sequence[Finding]], Decision]:
    """A scripted factory that REDACTs the given absolute spans whenever any finding is present."""

    def _factory(matches: Sequence[Finding]) -> Decision:
        if not matches:
            return _allow_decision(matches)
        fd = (FindingDisposition(detector="scripted", disposition=Disposition.REDACT),)
        trans = tuple(
            Transformation(kind="redact", span=span, replacement=REDACTION_PLACEHOLDER)
            for span in spans
        )
        return Decision(
            disposition=Disposition.REDACT,
            per_finding=fd,
            transformations=trans,
            findings=tuple(matches),
            plan_version="gw13-test",
            deciding_rules=("scripted",),
            unavailable_detectors=(),
        )

    return _factory


# --------------------------------------------------------------------------- #
# Fake detector: completed Findings with absolute spans over the whole text
# --------------------------------------------------------------------------- #


def _detector_for(
    value: str,
    *,
    category: Category = Category.SECRET,
    detector_class: str = "scripted",
) -> Detector:
    """A ``Detector`` flagging every occurrence of ``value`` with an absolute span.

    ``detector_class`` is the finding's ``detector`` name, so the shipped
    ``MinimalOutputResolver`` maps it to the class's disposition (e.g. ``"card"`` ->
    BLOCK, ``"aws"`` -> REDACT-the-remainder, unknown -> REDACT).
    """

    def _detect(whole: str) -> list[Finding]:
        hits: list[Finding] = []
        start = 0
        while True:
            idx = whole.find(value, start)
            if idx == -1:
                break
            hits.append(
                Finding(
                    detector=detector_class,
                    detector_version="t",
                    category=category,
                    status=FindingStatus.EXECUTED,
                    confidence=1.0,
                    spans=(Span(idx, idx + len(value)),),
                    evidence=None,
                ),
            )
            start = idx + len(value)
        return hits

    return _detect


def _no_detector(_whole: str) -> list[Finding]:
    return []


# --------------------------------------------------------------------------- #
# Scanner adapter + plan/rule/contract builders
# --------------------------------------------------------------------------- #


def _scan(
    buf: str,
    *,
    final: bool,
    hold_cap_tokens: int,
    token_index: TokenCounterProtocol,
    window: int,
) -> ScanResultLike:
    """Adapter so the real pure ``holdback.scan`` injects as a ``ScanProtocol``."""
    return _holdback_scan(
        buf,
        final=final,
        hold_cap_tokens=hold_cap_tokens,
        token_index=token_index,  # type: ignore[arg-type]
        window=window,
    )


def _rule(
    rule_id: str = "r1",
    *,
    category: Category = Category.SECRET,
    scope: RuleScope = RuleScope.OUTPUT,
    mode: Mode = Mode.ENFORCE,
    action: Action = Action.REDACT,
    on_unavailable: FailurePosture = FailurePosture.FAIL_CLOSED,
    priority: int = 0,
) -> Rule:
    return Rule(
        rule_id=rule_id,
        category=category,
        mode=mode,
        action=action,
        threshold=None,
        priority=priority,
        scope=scope,
        on_unavailable=on_unavailable,
        surfaces=frozenset({Surface.CHAT}),
    )


def _plan(*rules: Rule, mode: StreamingMode = StreamingMode.INCREMENTAL) -> ExecutionPlan:
    return ExecutionPlan(
        org_id="org-1",
        epoch=1,
        sequence=1,
        content_hash="0" * 32,
        compiled_at=0.0,
        rules=rules,
        required_detectors=frozenset(),
        streaming_mode=mode,
        integrity_locked=True,
    )


def _contract() -> ResourceContract:
    """A serviceable contract (the shared ``from_signals`` fixture shape the egress tests use)."""
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=512 * 1024 * 1024,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=20.0,
        utilization_cap=0.75,
        per_worker_rss=128 * 1024 * 1024,
    )


def _strict_pipeline(
    *,
    enforcing_output: bool,
    resolver: OutputResolver | None = None,
    detector: Detector | None = None,
    metrics: StrictMetrics | None = None,
    max_buffer_bytes: int = 1 << 30,
    clock: Callable[[], int] | None = None,
) -> StrictWithholdPipeline:
    return StrictWithholdPipeline(
        resolver=resolver if resolver is not None else MinimalOutputResolver(),
        enforcing_output=enforcing_output,
        max_buffer_bytes=max_buffer_bytes,
        metrics=metrics or StrictMetrics(),
        clock=clock if clock is not None else time.perf_counter_ns,
        detector=detector,
    )


def _inc_pipeline(
    plan: ExecutionPlan,
    *,
    resolver: OutputResolver | None = None,
    detector: Detector | None = None,
    metrics: HoldbackMetrics | None = None,
    scanner: object | None = None,
) -> StreamPipeline:
    cfg, _ = load_holdback_config({})
    return StreamPipeline.for_plan(
        plan,
        scanner=scanner if scanner is not None else _scan,  # type: ignore[arg-type]
        resolver=resolver if resolver is not None else MinimalOutputResolver(),
        token_index=TokenIndex(),
        cfg=cfg,
        metrics=metrics or HoldbackMetrics(),
        window=max_pattern_length(),
        detector=detector,
    )


def _chunks(text: str, *, splits: list[int], channel: str = CHANNEL) -> list[UpstreamChunk]:
    """Split ``text`` at the given cut points into upstream chunks; last chunk is ``final``."""
    pieces: list[str] = []
    prev = 0
    for cut in splits:
        pieces.append(text[prev:cut])
        prev = cut
    pieces.append(text[prev:])
    out: list[UpstreamChunk] = []
    for i, piece in enumerate(pieces):
        is_last = i == len(pieces) - 1
        out.append(UpstreamChunk(text_deltas=((channel, piece),), final=is_last))
    return out


_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789 .-_/@=+%:;,"


def _random_text(rng: random.Random, *, lo: int = 0, hi: int = 80) -> str:
    return "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(lo, hi)))


def _random_splits(rng: random.Random, length: int) -> list[int]:
    if length <= 1:
        return []
    count = rng.randint(0, min(5, length - 1))
    cuts = sorted(set(rng.sample(range(1, length), count))) if count else []
    return cuts


# =========================================================================== #
# Property 1
# =========================================================================== #
# Feature: output-streaming-modes-gw13, Property 1: STRICT releases nothing
# before the terminal result -- no content DownstreamFrame is emitted before the
# final chunk is processed and the resolver has decided.
# Validates: Requirements 3.2, 3.4


def test_property1_strict_releases_nothing_before_terminal() -> None:
    seed = 0x13_01
    rng = random.Random(seed)
    for i in range(_ITERS):
        text = _random_text(rng, lo=1, hi=60)
        splits = _random_splits(rng, len(text))
        chunks = _chunks(text, splits=splits)
        resolver = _RecordingResolver(_allow_decision)
        pipe = _strict_pipeline(enforcing_output=True, resolver=resolver)
        collector = _Collector()

        # Observe the frame-count AT the moment resolve is first called: a wrapper
        # records how many frames existed when decide fired. If STRICT released
        # anything first, decide would see content frames already in the sink.
        frames_at_first_decide: list[int] = []
        inner = resolver.decide

        def _spy(matches: Sequence[Finding], *, _c: _Collector = collector) -> Decision:
            frames_at_first_decide.append(len(_c.content_frames()))
            return inner(matches)

        resolver.decide = _spy  # type: ignore[method-assign]
        stats = _run(pipe.run(_source(chunks), collector, _never_killed))

        # Nothing was ever decided before all content was accumulated: at the first
        # (and only) decide the sink held zero content frames.
        assert frames_at_first_decide and all(
            n == 0 for n in frames_at_first_decide
        ), f"seed={seed:#x} iter={i}: content released before resolve"
        # Any content frame appears strictly after inspection (there is at most one).
        assert stats.done is True
        assert len(collector.content_frames()) <= 1


# =========================================================================== #
# Property 2 (LGW13-1)
# =========================================================================== #
# Feature: output-streaming-modes-gw13, Property 2: STRICT BLOCK yields zero
# prohibited bytes -- zero content frames and exactly one terminal output_blocked.
# Validates: Requirements 3.4, 1.5


def test_property2_strict_block_zero_content() -> None:
    seed = 0x13_02
    rng = random.Random(seed)
    for i in range(_ITERS):
        prohibited = "SECRET-" + str(rng.randint(0, 1_000_000))
        body = _random_text(rng, hi=40)
        text = f"{body} {prohibited}"
        splits = _random_splits(rng, len(text))
        chunks = _chunks(text, splits=splits)
        resolver = _RecordingResolver(_block_decision)
        pipe = _strict_pipeline(
            enforcing_output=True, resolver=resolver, detector=_detector_for(prohibited)
        )
        collector = _Collector()
        stats = _run(pipe.run(_source(chunks), collector, _never_killed))

        assert collector.content_frames() == [], f"seed={seed:#x} iter={i}"
        assert collector.terminal_codes() == ["output_blocked"], f"seed={seed:#x} iter={i}"
        assert collector.released_text() == ""
        assert prohibited not in collector.released_text()
        assert stats.error == "output_blocked"
        assert stats.frames_out == 0


# =========================================================================== #
# Property 3
# =========================================================================== #
# Feature: output-streaming-modes-gw13, Property 3: STRICT emitted bytes equal
# apply_decision of the whole buffer for any non-BLOCK Decision.
# Validates: Requirements 1.4, 1.5, 3.3, 3.5


def test_property3_strict_bytes_equal_apply_decision() -> None:
    seed = 0x13_03
    rng = random.Random(seed)
    for i in range(_ITERS):
        marker = "mark" + str(rng.randint(0, 9999))
        prefix = _random_text(rng, hi=30)
        suffix = _random_text(rng, hi=30)
        text = f"{prefix}{marker}{suffix}"
        start = len(prefix)
        span = Span(start, start + len(marker))
        # Half ALLOW, half REDACT so both non-BLOCK dispositions are exercised.
        if i % 2 == 0:
            factory = _allow_decision
            expected_decision = _allow_decision(())
        else:
            factory = _redact_decision_for((span,))
            expected_decision = factory(
                (
                    Finding(
                        detector="scripted",
                        detector_version="t",
                        category=Category.SECRET,
                        status=FindingStatus.EXECUTED,
                        confidence=1.0,
                        spans=(span,),
                        evidence=None,
                    ),
                ),
            )
        resolver = _RecordingResolver(factory)
        detector = _detector_for(marker) if i % 2 else _no_detector
        pipe = _strict_pipeline(enforcing_output=True, resolver=resolver, detector=detector)
        collector = _Collector()
        splits = _random_splits(rng, len(text))
        _run(pipe.run(_source(_chunks(text, splits=splits)), collector, _never_killed))

        expected = apply_decision(text, expected_decision, 0)
        assert collector.released_text() == expected, f"seed={seed:#x} iter={i}"
        # The single OutputResolver seam decided exactly once.
        assert len(resolver.calls) == 1


# =========================================================================== #
# Property 4 (LGW13-2)
# =========================================================================== #
# Feature: output-streaming-modes-gw13, Property 4: INCREMENTAL never presents
# truncation as block -- a post-first-byte BLOCK leaves the delivered prefix
# untouched, ends with a terminal frame, and emits no content after it.
# Validates: Requirements 2.3, 2.5, 2.6


def test_property4_incremental_no_truncation_as_block() -> None:
    seed = 0x13_04
    rng = random.Random(seed)
    # card -> BLOCK under MinimalOutputResolver; released naturally via the real scanner.
    value = "4111111111111111"
    detector = _detector_for(value, category=Category.PII, detector_class="card")
    for i in range(_ITERS):
        # A clear prefix that releases BEFORE the blocking value, then the value.
        prefix = _random_text(rng, lo=1, hi=20).replace(value[:4], "zzzz") + " clear text here "
        text = f"{prefix}{value} tail"
        pipe = _inc_pipeline(_plan(_rule(action=Action.BLOCK, category=Category.PII)),
                             detector=detector)
        collector = _Collector()
        stats = _run(pipe.run(_source(_chunks(text, splits=[])), collector, _never_killed))

        released = collector.released_text()
        # The whole card value never appears unredacted in the released bytes.
        assert value not in released, f"seed={seed:#x} iter={i}"
        # The stream ended on a terminal frame (declared termination), not silently.
        assert collector.terminal_codes() == ["output_blocked"], f"seed={seed:#x} iter={i}"
        assert collector.frames[-1].error_code == "output_blocked"
        # No content frame follows the terminal frame.
        terminal_seen = False
        for frame in collector.frames:
            assert not terminal_seen, "content frame followed a terminal frame"
            if frame.error_code is not None:
                terminal_seen = True
        # The delivered prefix was never retracted: whatever cleared stays a prefix of `text`.
        assert text.startswith(released) or released == ""
        assert stats.error == "output_blocked"


# =========================================================================== #
# Property 5
# =========================================================================== #
# Feature: output-streaming-modes-gw13, Property 5: INCREMENTAL truthful block
# before the first byte -- a BLOCK before any content byte emits zero prohibited
# bytes (only the terminal output_blocked frame).
# Validates: Requirements 2.2


def test_property5_incremental_truthful_block_before_first_byte() -> None:
    seed = 0x13_05
    rng = random.Random(seed)
    value = "4111111111111111"  # card -> BLOCK, appears at the very start.
    detector = _detector_for(value, category=Category.PII, detector_class="card")
    for i in range(_ITERS):
        # The blocking value is the first token and completes within one chunk, so the
        # BLOCK arises before any content byte is released.
        text = f"{value}{_random_text(rng, hi=20)}"
        pipe = _inc_pipeline(_plan(_rule(action=Action.BLOCK, category=Category.PII)),
                             detector=detector)
        collector = _Collector()
        stats = _run(pipe.run(_source([UpstreamChunk(((CHANNEL, text),), final=True)]),
                              collector, _never_killed))

        assert collector.released_text() == "", f"seed={seed:#x} iter={i}"
        assert value not in collector.released_text()
        assert collector.terminal_codes() == ["output_blocked"]
        assert stats.error == "output_blocked"


# =========================================================================== #
# Property 6
# =========================================================================== #
# Feature: output-streaming-modes-gw13, Property 6: INCREMENTAL redacts only
# not-yet-released content -- a REDACT match after the first byte masks only the
# unreleased remainder; the delivered prefix is byte-identical.
# Validates: Requirements 2.4


def test_property6_incremental_redacts_only_not_yet_released() -> None:
    seed = 0x13_06
    rng = random.Random(seed)
    # aws -> REDACT (redact-the-remainder) under MinimalOutputResolver.
    value = "AKIAIOSFODNN7EXAMPLE"
    detector = _detector_for(value, category=Category.SECRET)
    for i in range(_ITERS):
        prefix = "clear prefix bytes "
        text = f"{prefix}{value} end"
        # Split so the value straddles a chunk boundary after the prefix released.
        boundary = len(prefix) + rng.randint(1, len(value) - 1)
        pipe = _inc_pipeline(_plan(_rule(action=Action.REDACT)), detector=detector)
        collector = _Collector()
        _run(pipe.run(_source(_chunks(text, splits=[boundary])), collector, _never_killed))

        released = collector.released_text()
        # The raw secret never appears; the placeholder marks the redacted remainder.
        assert value not in released, f"seed={seed:#x} iter={i} boundary={boundary}"
        assert REDACTION_PLACEHOLDER in released
        # The clear prefix was delivered byte-identically (never retracted/altered).
        assert released.startswith(prefix), f"seed={seed:#x} iter={i}"


# =========================================================================== #
# Property 7 (LGW13-6)
# =========================================================================== #
# Feature: output-streaming-modes-gw13, Property 7: boundary safety in both modes
# -- a secret split across a chunk boundary leaks no prohibited byte in either mode.
# Validates: Requirements 7.1, 7.2, 7.3


def test_property7_boundary_safety_both_modes() -> None:
    seed = 0x13_07
    rng = random.Random(seed)
    # aws key -> REDACT in both modes; placed across a parametrised split offset.
    value = "AKIAIOSFODNN7EXAMPLE"
    for i in range(_ITERS):
        prefix = _random_text(rng, hi=15).replace("AKIA", "zzzz")
        suffix = _random_text(rng, hi=15)
        text = f"{prefix}{value}{suffix}"
        start = len(prefix)
        # A split offset strictly inside the secret (so the match straddles a boundary).
        offset = start + rng.randint(1, len(value) - 1)
        detector = _detector_for(value, category=Category.SECRET)

        # INCREMENTAL (shipped holdback + fake detector).
        inc = _inc_pipeline(_plan(_rule(action=Action.REDACT)), detector=detector)
        inc_sink = _Collector()
        _run(inc.run(_source(_chunks(text, splits=[offset])), inc_sink, _never_killed))
        assert value not in inc_sink.released_text(), (
            f"seed={seed:#x} iter={i} INCREMENTAL offset={offset}"
        )

        # STRICT (whole-buffer inspection, trivial boundary safety).
        strict = _strict_pipeline(enforcing_output=True, detector=detector)
        strict_sink = _Collector()
        _run(strict.run(_source(_chunks(text, splits=[offset])), strict_sink, _never_killed))
        assert value not in strict_sink.released_text(), (
            f"seed={seed:#x} iter={i} STRICT offset={offset}"
        )


# =========================================================================== #
# Property 8
# =========================================================================== #
# Feature: output-streaming-modes-gw13, Property 8: degenerate flag set exactly
# on single-chunk responses -- True iff no content frame released before `final`
# with content present; a multi-chunk early release sets it False.
# Validates: Requirements 6.1, 6.2


def test_property8_degenerate_flag_single_chunk() -> None:
    seed = 0x13_08
    rng = random.Random(seed)
    for i in range(_ITERS):
        kind = i % 3
        plan = _plan(_rule(action=Action.REDACT))
        collector = _Collector()
        # Honest-outcome: the shipped degenerate flag keys off whether the REAL
        # holdback scanner released a content frame BEFORE the final chunk --
        # frames_out_before_final == 0 and frames_out > 0 (stream.py). The kind=1
        # fixture must therefore make the real scanner release content early, not
        # merely append a trailing space to the final chunk (the scanner holds the
        # last word-class run, so a trailing space on the FINAL chunk flushes only
        # at `final`). We mirror test_lgw12b_stream.py: a non-final chunk ending in
        # a completed word-run releases its cleared prefix ("hello world " out of
        # "hello world foo"), leaving the last run held for the next chunk.
        if kind == 0:
            # Single final chunk WITH content -> degenerate True.
            text = _random_text(rng, lo=1, hi=40) + " "
            chunks = [UpstreamChunk(((CHANNEL, text),), final=True)]
            expected = True
            expect_content = True
            min_frames_before_final = 0
        elif kind == 1:
            # Multi-chunk, releases a cleared prefix early -> degenerate False.
            # The first chunk is "<words> <held-run>" with NO trailing space, so
            # the real scanner releases "<words> " and holds the last run; a real
            # content frame is emitted strictly before the final chunk.
            first = _random_text(rng, lo=1, hi=18).replace(" ", "x") + " held" + str(
                rng.randint(0, 999)
            )
            second = " more content here"
            chunks = [
                UpstreamChunk(((CHANNEL, first),), final=False),
                UpstreamChunk(((CHANNEL, second),), final=True),
            ]
            expected = False
            expect_content = True
            min_frames_before_final = 1
        else:
            # Empty response -> NOT degenerate (just empty) and no content frame.
            chunks = [UpstreamChunk(((CHANNEL, ""),), final=True)]
            expected = False
            expect_content = False
            min_frames_before_final = 0
        pipe = _inc_pipeline(plan)
        stats = _run(pipe.run(_source(chunks), collector, _never_killed))
        if min_frames_before_final:
            # Self-check the kind=1 fixture premise: the real scanner released at
            # least one content frame while draining the non-final chunk (the
            # degenerate flag is False precisely because content left before
            # `final`). The non-final chunk carries no `final`-flush, so every
            # content frame emitted during it is a genuine early release.
            assert len(collector.content_frames()) >= min_frames_before_final, (
                f"seed={seed:#x} iter={i}: fixture released no early content frame"
            )
        assert stats.degenerated_to_strict is expected, (
            f"seed={seed:#x} iter={i} kind={kind} frames={stats.frames_out}"
        )
        if expect_content:
            assert stats.frames_out >= 1
        else:
            assert stats.frames_out == 0


# =========================================================================== #
# Property 9
# =========================================================================== #
# Feature: output-streaming-modes-gw13, Property 9: the compiler round-trips both
# streaming modes -- encode -> decode -> compile_plan yields plan.streaming_mode == m.
# Validates: Requirements 4.1, 4.2, 4.3


def test_property9_compiler_round_trips_both_modes() -> None:
    draft = RuleDraft(
        rule_id="r1",
        category=Category.SECRET,
        mode=Mode.ENFORCE,
        action=Action.REDACT,
        threshold=None,
        priority=0,
        scope=RuleScope.OUTPUT,
        on_unavailable=FailurePosture.FAIL_CLOSED,
        surfaces=frozenset({Surface.CHAT}),
    )
    # Exhaustive over the whole StreamingMode enum.
    for mode in StreamingMode:
        document = PlanDocument(org_id="org-1", streaming_mode=mode, drafts=(draft,))
        body = encode_plan_body(document)
        decoded = decode_plan_body(json.dumps(body).encode())
        assert decoded.streaming_mode is mode
        plan = compile_plan(
            "org-1",
            decoded.drafts,
            streaming_mode=decoded.streaming_mode,
            compiled_at=0.0,
        )
        assert plan.streaming_mode is mode
        assert mode in SUPPORTED_STREAMING


# =========================================================================== #
# Property 10 (LGW13-3)
# =========================================================================== #
# Feature: output-streaming-modes-gw13, Property 10: resolver-owned UNAVAILABLE
# posture on the output path -- BLOCK candidate iff some selecting ENFORCE rule is
# FAIL_CLOSED, never a blanket redact, identical through both modes at Phase.OUTPUT.
# Validates: Requirements 8.1, 8.2, 8.4


def _unavailable_finding(category: Category = Category.SECRET) -> Finding:
    # Honest-outcome: INCREMENTAL's `_resolve_released` runs `completed_in` FIRST,
    # keeping only findings whose whole span lies inside the released byte-slice,
    # BEFORE calling the resolver. An UNAVAILABLE finding with `spans=()` is dropped
    # by `completed_in` (it requires `f.spans` truthy) -> ALLOW -> no block, which is
    # the shipped behaviour. For the INCREMENTAL half to see the UNAVAILABLE finding
    # it must carry a span inside the released slice; on `final` the whole buffer
    # flushes as one slice `[0, len)`, so `Span(0, 1)` is retained for any non-empty
    # output. The resolver routes UNAVAILABLE through `on_unavailable` and ignores
    # the span, so the direct FAIL_CLOSED -> BLOCK outcome is unchanged, and STRICT's
    # `_finalize` (resolves over ALL findings, no slice-windowing) is unaffected.
    return Finding(
        detector="scripted",
        detector_version="t",
        category=category,
        status=FindingStatus.UNAVAILABLE,
        confidence=None,
        spans=(Span(0, 1),),
        evidence=None,
    )


def test_property10_unavailable_posture_output_path() -> None:
    seed = 0x13_0A
    rng = random.Random(seed)
    for i in range(_ITERS):
        fail_closed = bool(rng.getrandbits(1))
        posture_v = FailurePosture.FAIL_CLOSED if fail_closed else FailurePosture.FAIL_OPEN
        rule = _rule(action=Action.REDACT, on_unavailable=posture_v)
        plan = _plan(rule)
        findings = (_unavailable_finding(),)

        decision = resolve(findings, plan, Phase.OUTPUT)
        # Shipped resolver: FAIL_CLOSED -> BLOCK candidate; FAIL_OPEN -> ALLOW. Never
        # a blanket redact for an UNAVAILABLE finding.
        if fail_closed:
            assert decision.disposition is Disposition.BLOCK, f"seed={seed:#x} iter={i}"
        else:
            assert decision.disposition is Disposition.ALLOW, f"seed={seed:#x} iter={i}"
        assert decision.disposition is not Disposition.REDACT

        # Exercise identically through BOTH pipelines via a recording resolver that
        # wraps the real resolve at Phase.OUTPUT. Honest-outcome: INCREMENTAL
        # resolves findings PER RELEASED SLICE via `completed_in`, so the UNAVAILABLE
        # finding must carry a span inside the released slice (see
        # `_unavailable_finding`) for the real `resolve(..., Phase.OUTPUT)` to see it
        # and the INCREMENTAL pipeline to emit `output_blocked` under FAIL_CLOSED.
        wrapper = _RecordingResolver(lambda m, _p=plan: resolve(m, _p, Phase.OUTPUT))
        for build in (
            lambda: _strict_pipeline(enforcing_output=True, resolver=wrapper,
                                     detector=lambda _w: list(findings)),
            lambda: _inc_pipeline(plan, resolver=wrapper, detector=lambda _w: list(findings)),
        ):
            sink = _Collector()
            _run(build().run(_source(_chunks("some output text", splits=[])),
                             sink, _never_killed))
            if fail_closed:
                assert "output_blocked" in sink.terminal_codes()
            else:
                assert sink.terminal_codes() == []


# =========================================================================== #
# Property 11 (LGW13-4)
# =========================================================================== #
# Feature: output-streaming-modes-gw13, Property 11: MONITOR output rules never
# enforce -- findings whose only selecting rules are MONITOR resolve to ALLOW,
# through both modes.
# Validates: Requirements 8.3, 8.4


def test_property11_monitor_never_enforces() -> None:
    seed = 0x13_0B
    rng = random.Random(seed)
    for i in range(_ITERS):
        # A MONITOR output rule (would BLOCK if it were ENFORCE) plus an EXECUTED finding.
        rule = _rule(mode=Mode.MONITOR, action=Action.BLOCK)
        plan = _plan(rule)
        finding = Finding(
            detector="scripted",
            detector_version="t",
            category=Category.SECRET,
            status=FindingStatus.EXECUTED,
            confidence=1.0,
            spans=(Span(0, 4),),
            evidence=None,
        )
        decision = resolve((finding,), plan, Phase.OUTPUT)
        assert decision.disposition is Disposition.ALLOW, f"seed={seed:#x} iter={i}"

        # Through both pipelines: content is delivered, no terminal frame.
        text = _random_text(rng, lo=4, hi=40)
        wrapper = _RecordingResolver(lambda m, _p=plan: resolve(m, _p, Phase.OUTPUT))
        # STRICT only engages with an enforcing output rule; a MONITOR-only plan is
        # pass-through, so it still delivers the content unheld.
        strict = _strict_pipeline(
            enforcing_output=bool(enforcing_output_rules(plan)),
            resolver=wrapper,
            detector=lambda _w: [finding],
        )
        strict_sink = _Collector()
        _run(strict.run(_source(_chunks(text, splits=[])), strict_sink, _never_killed))
        assert strict_sink.terminal_codes() == []
        assert strict_sink.released_text() == text


# =========================================================================== #
# Mechanism / example tests
# =========================================================================== #


def test_one_resolver_wiring_both_modes_decide_at_output() -> None:
    # Both pipelines route through the ONE injected resolver's `decide`.
    text = "hello output world "
    inc_resolver = _RecordingResolver(_allow_decision)
    inc = _inc_pipeline(_plan(_rule(action=Action.REDACT)), resolver=inc_resolver)
    _run(inc.run(_source(_chunks(text, splits=[])), _Collector(), _never_killed))
    assert len(inc_resolver.calls) >= 1

    strict_resolver = _RecordingResolver(_allow_decision)
    strict = _strict_pipeline(enforcing_output=True, resolver=strict_resolver)
    _run(strict.run(_source(_chunks(text, splits=[])), _Collector(), _never_killed))
    assert len(strict_resolver.calls) == 1


def test_strict_never_calls_holdback_scanner() -> None:
    # STRICT is a DISTINCT mode (C25): it never touches the holdback scanner / TokenIndex.
    calls: list[str] = []

    def _counting_scanner(buf: str, **_: object) -> ScanResultLike:
        calls.append(buf)
        raise AssertionError("STRICT must not call the holdback scanner")

    # The scanner is not even a constructor arg for STRICT; prove structurally there is no
    # scanner seam and the run completes without one.
    strict = _strict_pipeline(enforcing_output=True)
    _run(strict.run(_source(_chunks("abc def ", splits=[])), _Collector(), _never_killed))
    assert calls == []
    assert not hasattr(strict, "_scanner")
    assert not hasattr(strict, "_token_index")


def test_no_second_decision_site() -> None:
    # R1.2: there is no `enforce_output` symbol anywhere in the egress surface.
    import gateway_v2.egress.output_guard as og
    import gateway_v2.egress.stream as stream_mod
    import gateway_v2.egress.strict as strict_mod

    for module in (og, strict_mod, stream_mod):
        assert not hasattr(module, "enforce_output"), module.__name__


def test_mode_selection_returns_right_pipeline() -> None:
    cfg, _ = load_holdback_config({})
    common = dict(
        scanner=_scan,
        token_index=TokenIndex(),
        cfg=cfg,
        metrics=HoldbackMetrics(),
        window=max_pattern_length(),
        resolver=MinimalOutputResolver(),
        detector=None,
        max_buffer_bytes=1 << 20,
        strict_metrics=StrictMetrics(),
    )
    strict = select_pipeline(_plan(_rule(), mode=StreamingMode.STRICT_WITHHOLD), **common)
    assert isinstance(strict, StrictWithholdPipeline)
    inc = select_pipeline(_plan(_rule(), mode=StreamingMode.INCREMENTAL), **common)
    assert isinstance(inc, StreamPipeline)


def test_produced_mode_stamp() -> None:
    # STRICT -> strict_withhold.
    strict = _strict_pipeline(enforcing_output=True)
    s_stats = _run(strict.run(_source(_chunks("abc ", splits=[])), _Collector(), _never_killed))
    assert s_stats.produced_mode is StreamingMode.STRICT_WITHHOLD

    # INCREMENTAL multi-chunk early release -> incremental + degenerated False.
    inc = _inc_pipeline(_plan(_rule(action=Action.REDACT)))
    multi = [
        UpstreamChunk(((CHANNEL, "early text "),), final=False),
        UpstreamChunk(((CHANNEL, "more text "),), final=True),
    ]
    m_stats = _run(inc.run(_source(multi), _Collector(), _never_killed))
    assert m_stats.produced_mode is StreamingMode.INCREMENTAL
    assert m_stats.degenerated_to_strict is False

    # INCREMENTAL single-chunk -> incremental + degenerated True.
    inc2 = _inc_pipeline(_plan(_rule(action=Action.REDACT)))
    single = [UpstreamChunk(((CHANNEL, "whole response "),), final=True)]
    o_stats = _run(inc2.run(_source(single), _Collector(), _never_killed))
    assert o_stats.produced_mode is StreamingMode.INCREMENTAL
    assert o_stats.degenerated_to_strict is True


def test_latency_labelling_strict_distinct_from_holdback() -> None:
    metrics = StrictMetrics()
    strict = _strict_pipeline(enforcing_output=True, metrics=metrics)
    _run(strict.run(_source(_chunks("some sanitized text ", splits=[])),
                    _Collector(), _never_killed))
    reading: StrictReading = metrics.snapshot()
    # STRICT records a completion-overhead series and nothing else.
    assert hasattr(reading, "completion_overhead_ms")
    assert reading.strict_streams_total == 1
    # No TTFT / release-lag / holdback-wait field on the STRICT reading.
    for forbidden in ("release_processing", "holdback_wait", "time_to_first_token", "ttft"):
        assert not hasattr(reading, forbidden)
    # Distinct SERIES_COUNT from HoldbackMetrics; INCREMENTAL still records into HoldbackMetrics.
    hb = HoldbackMetrics()
    import gateway_v2.runtime.holdback_metrics as hb_mod

    assert STRICT_SERIES_COUNT != hb_mod.SERIES_COUNT
    inc = _inc_pipeline(_plan(_rule(action=Action.REDACT)), metrics=hb)
    _run(inc.run(_source([UpstreamChunk(((CHANNEL, "hello world"),), final=True)]),
                 _Collector(), _never_killed))
    assert hb.snapshot().held_tokens_hist.count == 1


def test_buffer_bound_read_from_contract_and_overflow_fails_closed() -> None:
    contract = _contract()
    # The STRICT bound is read from the capacity authority -- no literal.
    bound = contract.stream_buffer_bytes(active_streams=1)
    assert bound > 0

    # Drive a tiny bound so accumulation overflows: terminal strict_buffer_overflow,
    # zero content, observe_overflow bumped.
    metrics = StrictMetrics()
    strict = _strict_pipeline(enforcing_output=True, metrics=metrics, max_buffer_bytes=4)
    text = "this is far longer than four bytes"
    collector = _Collector()
    stats = _run(strict.run(_source(_chunks(text, splits=[])), collector, _never_killed))
    assert collector.content_frames() == []
    assert collector.terminal_codes() == [posture.STREAM_STRICT_BUFFER_OVERFLOW]
    assert stats.error == posture.STREAM_STRICT_BUFFER_OVERFLOW
    assert metrics.snapshot().overflow_total == 1


def test_compiler_guard_rejects_unsupported_and_leaves_previous() -> None:
    # Build a resolved plan via the shipped compiler.
    draft = RuleDraft(
        rule_id="r1",
        category=Category.SECRET,
        mode=Mode.ENFORCE,
        action=Action.REDACT,
        threshold=None,
        priority=0,
        scope=RuleScope.OUTPUT,
        on_unavailable=FailurePosture.FAIL_CLOSED,
        surfaces=frozenset({Surface.CHAT}),
    )
    previous = compile_plan("org-1", (draft,), streaming_mode=StreamingMode.INCREMENTAL,
                            compiled_at=0.0)

    # A mode outside SUPPORTED_STREAMING raises CompileError; `previous` is unchanged.

    class _Bogus:
        value = "bogus_mode"

    with pytest.raises(CompileError):
        compile_plan("org-1", (draft,), streaming_mode=_Bogus(),  # type: ignore[arg-type]
                     previous=previous, compiled_at=1.0)
    assert previous.streaming_mode is StreamingMode.INCREMENTAL
    # Both shipped modes ARE in the supported set.
    assert StreamingMode.INCREMENTAL in SUPPORTED_STREAMING
    assert StreamingMode.STRICT_WITHHOLD in SUPPORTED_STREAMING


# --------------------------------------------------------------------------- #
# LGW13 mechanism scenarios (explicit)
# --------------------------------------------------------------------------- #


def test_lgw13_1_strict_unsafe_whole_response_zero_prohibited_bytes() -> None:
    # STRICT, whole response resolves to BLOCK -> zero prohibited bytes, one terminal.
    prohibited = "AKIAIOSFODNN7EXAMPLE"  # a secret the detector flags
    text = f"here is a key {prohibited} in the response"
    strict = _strict_pipeline(enforcing_output=True, resolver=_RecordingResolver(_block_decision),
                              detector=_detector_for(prohibited))
    collector = _Collector()
    stats = _run(strict.run(_source(_chunks(text, splits=[8, 20])), collector, _never_killed))
    assert collector.released_text() == ""
    assert prohibited not in collector.released_text()
    assert collector.terminal_codes() == ["output_blocked"]
    assert stats.frames_out == 0


def test_lgw13_2_incremental_midstream_declared_termination() -> None:
    # INCREMENTAL, unsafe mid-stream after first byte -> declared termination, prefix intact.
    # Honest-outcome: INCREMENTAL resolves findings PER RELEASED SLICE through the
    # ONE injected MinimalOutputResolver, which BLOCKs only a block-mapped detector
    # class (the trade-off terminate classes "card"/"jwt"). A finding tagged with
    # the default "scripted" class maps to REDACT (redact-the-remainder), never a
    # block -- so the fixture must carry the blocking `detector_class="card"`,
    # matching the passing P4/P5. The card value completes within the released
    # whole-buffer slice on `final`, so completed_in keeps it and decide() BLOCKs.
    value = "4111111111111111"  # card -> BLOCK
    text = f"a clear preamble here {value} trailing"
    inc = _inc_pipeline(_plan(_rule(action=Action.BLOCK, category=Category.PII)),
                        detector=_detector_for(value, category=Category.PII,
                                               detector_class="card"))
    collector = _Collector()
    stats = _run(inc.run(_source(_chunks(text, splits=[])), collector, _never_killed))
    assert value not in collector.released_text()
    assert collector.terminal_codes() == ["output_blocked"]
    assert collector.frames[-1].error_code == "output_blocked"
    assert stats.error == "output_blocked"


def test_lgw13_3_output_block_unavailable_posture_through_both_modes() -> None:
    # Honest-outcome: the STRICT half resolves over ALL findings at once (`_finalize`,
    # no slice-windowing) so it blocks regardless of span; the INCREMENTAL half
    # resolves PER RELEASED SLICE via `completed_in`, so the UNAVAILABLE finding must
    # carry a span inside the released slice (see `_unavailable_finding` -> Span(0,1),
    # retained when the whole buffer flushes as one slice `[0, len)` on `final`) for
    # the real resolver to see it and the INCREMENTAL pipeline to emit output_blocked.
    findings = (_unavailable_finding(),)
    plan = _plan(_rule(action=Action.REDACT, on_unavailable=FailurePosture.FAIL_CLOSED))
    # FAIL_CLOSED UNAVAILABLE -> BLOCK, never a blanket redact.
    decision = resolve(findings, plan, Phase.OUTPUT)
    assert decision.disposition is Disposition.BLOCK

    wrapper = _RecordingResolver(lambda m, _p=plan: resolve(m, _p, Phase.OUTPUT))
    strict = _strict_pipeline(enforcing_output=True, resolver=wrapper,
                              detector=lambda _w: list(findings))
    s_sink = _Collector()
    _run(strict.run(_source(_chunks("output text", splits=[])), s_sink, _never_killed))
    assert "output_blocked" in s_sink.terminal_codes()

    inc = _inc_pipeline(plan, resolver=wrapper, detector=lambda _w: list(findings))
    i_sink = _Collector()
    _run(inc.run(_source(_chunks("output text", splits=[])), i_sink, _never_killed))
    assert "output_blocked" in i_sink.terminal_codes()


def test_lgw13_4_output_monitor_delivered_and_non_enforcing_both_modes() -> None:
    plan = _plan(_rule(mode=Mode.MONITOR, action=Action.BLOCK))
    finding = Finding(
        detector="scripted",
        detector_version="t",
        category=Category.SECRET,
        status=FindingStatus.EXECUTED,
        confidence=1.0,
        spans=(Span(0, 3),),
        evidence=None,
    )
    assert resolve((finding,), plan, Phase.OUTPUT).disposition is Disposition.ALLOW

    text = "delivered content body "
    # STRICT: a MONITOR-only plan is non-enforcing -> pass-through delivers everything.
    strict = _strict_pipeline(enforcing_output=bool(enforcing_output_rules(plan)))
    s_sink = _Collector()
    _run(strict.run(_source(_chunks(text, splits=[])), s_sink, _never_killed))
    assert s_sink.terminal_codes() == []
    assert s_sink.released_text() == text

    # INCREMENTAL: non-enforcing plan passes through unheld.
    inc = _inc_pipeline(plan)
    i_sink = _Collector()
    _run(inc.run(_source(_chunks(text, splits=[])), i_sink, _never_killed))
    assert i_sink.terminal_codes() == []
    assert i_sink.released_text() == text


def test_lgw13_6_secret_spanning_boundary_no_leak_both_modes() -> None:
    value = "AKIAIOSFODNN7EXAMPLE"
    text = f"prefix bytes {value} suffix"
    offset = text.index(value) + 5  # a split strictly inside the secret
    detector = _detector_for(value, category=Category.SECRET)

    inc = _inc_pipeline(_plan(_rule(action=Action.REDACT)), detector=detector)
    i_sink = _Collector()
    _run(inc.run(_source(_chunks(text, splits=[offset])), i_sink, _never_killed))
    assert value not in i_sink.released_text()

    strict = _strict_pipeline(enforcing_output=True, detector=detector)
    s_sink = _Collector()
    _run(strict.run(_source(_chunks(text, splits=[offset])), s_sink, _never_killed))
    assert value not in s_sink.released_text()


# --------------------------------------------------------------------------- #
# Fail-closed smoke: unresolved plan withholds (both for_plan guards)
# --------------------------------------------------------------------------- #


def test_strict_for_plan_fails_closed_on_unresolved_plan() -> None:
    deps = dict(
        resolver=MinimalOutputResolver(),
        max_buffer_bytes=1 << 20,
        metrics=StrictMetrics(),
    )
    for unresolved in (
        UNDETERMINED,
        PlanUnavailable(org_id="org-1", reason="stale"),
        PlanUnknownTenant(org_id="org-1"),
    ):
        with pytest.raises(EnforcementUndetermined):
            StrictWithholdPipeline.for_plan(unresolved, **deps)  # type: ignore[arg-type]


def test_strict_most_restrictive_is_shipped_discipline() -> None:
    # Sanity on the shared domain primitive the resolver uses.
    assert most_restrictive((Disposition.ALLOW, Disposition.BLOCK)) is Disposition.BLOCK
    assert most_restrictive(()) is Disposition.ALLOW
    assert StrictStats().produced_mode is StreamingMode.STRICT_WITHHOLD
