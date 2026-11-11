"""Wiring tests for ``ChatStream._controls`` upstream-liveness (strict-withhold-idle-timeout, 3.3).

# Feature: strict-withhold-idle-timeout, Property 1 / Property 4 (wiring level)
# Validates: Requirements 2.1, 2.2, 2.3, 3.4, 3.6, 3.7

These are the task-3.3 tests of the one line that connects the shipped
:func:`~gateway_v2.egress.output_guard.withholds_until_decision` predicate (task 3.1) to the
:class:`~gateway_v2.edge.stream_timeouts.StreamTimeouts` ``upstream_liveness`` flag (task 3.2):
``ChatStream._controls(binding, kill_latch, plan)`` now takes ``plan`` and passes
``upstream_liveness=withholds_until_decision(plan)`` to the per-stream ``StreamTimeouts``.

Two layers of coverage:

* **Flag wiring** — ``ChatStream._controls`` builds a ``StreamTimeouts`` whose
  ``_upstream_liveness`` is True for a STRICT_WITHHOLD plan with an enforcing output rule and
  False for every non-withholding shape: INCREMENTAL, pass-through strict (no enforcing output
  rule), and a strict plan whose only OUTPUT rules are ``mode=off`` / a ``flag`` (ALLOW) action.
  This is the ``_controls`` truth table (design.md "Unit Tests"); it drives the REAL ``_controls``
  so the call site and the predicate are exercised together.
* **ASGI-level behaviour** — the whole streaming orchestration (``ChatRoute._stream_response`` ->
  ``ChatStream.stream`` -> the shipped ``StrictWithholdPipeline`` under the real ``StreamTimeouts``
  and ``InFlightControl``), driven with a clock-advancing in-process provider (the task-1 harness
  style, zero wall time). A 6 s withheld strict stream returns a 200-shaped SSE response with the
  sanitized content and raises NO ``stream_idle_timeout`` (the counter/event-log signal — the
  per-request ``RequestHolds.failure_code`` — stays clear); a block payload gives a terminal
  ``output_blocked`` frame with zero content before it; a 1.5 s upstream silence gives a terminal
  ``stream_inter_chunk_timeout``.

Layering: a test file (outside the import-linter contract); it injects the concrete
``detect``/``egress``/``edge`` seams directly the same way the sibling task-1 bug harness
(``test_strict_withhold_idle_timeout_bug.py``) and the LGW15 stream property test
(``test_lgw15_stream.py``) do.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import cast

from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.dispatch.provider import ProviderClient, UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.routing import DispatchRouter
from gateway_v2.domain import (
    Action,
    Category,
    Decision,
    Disposition,
    ExecutionPlan,
    FailurePosture,
    Finding,
    Mode,
    Rule,
    RuleCondition,
    RuleKind,
    RuleScope,
    StreamingMode,
    Surface,
    posture,
)
from gateway_v2.domain.incident import StreamBinding
from gateway_v2.edge.cancel import InFlightSnapshot, KillLatch
from gateway_v2.edge.chat_lifecycle import LifecycleOutcome
from gateway_v2.edge.chat_stream import ChatStream
from gateway_v2.edge.executor import ScanExecutor
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.routes import ChatRoute
from gateway_v2.egress.output_guard import (
    OutputResolver,
    enforcing_output_rules,
    withholds_until_decision,
)
from gateway_v2.egress.stream import ScanProtocol
from gateway_v2.runtime.holdback_config import HoldbackConfig
from gateway_v2.runtime.resources import ResourceContract
from tests.edge._org_limits import org_limits_for

# --------------------------------------------------------------------------- #
# SLOWSTREAM parameters (the integration F1 reproduction), shared by the ASGI cases.
# --------------------------------------------------------------------------- #

#: 40 content events, 0.15 s apart, then one ``final`` event: 6.0 s total, past the 3 s idle bound
#: while every inter-event gap (0.15 s) stays well under the 1 s inter-chunk bound. These are
#: scenario parameters, not capacity literals.
_SLOWSTREAM_CHUNKS = 40
_SLOWSTREAM_GAP_S = 0.15
_CONTENT_CHANNEL = "output_content"
#: A sentinel the block resolver keys off, so the block case trips deterministically without
#: depending on any detector's matching internals.
_BLOCK_SENTINEL = "[internal address 10.0.0.1]"


def _contract() -> ResourceContract:
    """A serviceable contract at the default 20 ms SLO: idle 3 s, inter-chunk 1 s, write 0.5 s."""
    return ResourceContract(
        cpu_quota=4.0,
        memory_limit=1 << 34,
        fd_limit=4096,
        guard_capacity=None,
        target_p99_ms=20.0,
        utilization_cap=0.75,
        per_worker_rss=400 << 20,
        worker_override=None,
        cpu_source="t",
        mem_source="t",
        fd_source="t",
    )


# --------------------------------------------------------------------------- #
# Plan fixtures: the four _controls truth-table shapes.
# --------------------------------------------------------------------------- #


def _plan(
    *, mode: StreamingMode, rules: tuple[Rule, ...] = ()
) -> ExecutionPlan:
    return ExecutionPlan(
        "org", 1, 1, "abcdef0123456789abcdef", 0.0, rules, frozenset(), mode, True,
    )


def _ip_leak_rule(*, rule_mode: Mode = Mode.ENFORCE, action: Action = Action.BLOCK) -> Rule:
    """A single OUTPUT-scope ip_leak rule with the given mode/action."""
    condition = RuleCondition(
        RuleKind.DETECTOR, "", RuleScope.OUTPUT, (), "", "output.ip_leak", "",
    )
    return Rule(
        "out_ip_leak", Category.IP_LEAK, rule_mode, action, None, 200,
        RuleScope.OUTPUT, FailurePosture.FAIL_CLOSED, frozenset({Surface.CHAT}), condition,
        "[internal address]",
    )


def _strict_enforcing_plan() -> ExecutionPlan:
    """STRICT_WITHHOLD + an ENFORCE/BLOCK ip_leak OUTPUT rule (the D-20 promotion shape)."""
    plan = _plan(mode=StreamingMode.STRICT_WITHHOLD, rules=(_ip_leak_rule(),))
    assert enforcing_output_rules(plan), "fixture must be a withholding strict plan"
    return plan


def _incremental_plan() -> ExecutionPlan:
    return _plan(mode=StreamingMode.INCREMENTAL)


def _passthrough_strict_plan() -> ExecutionPlan:
    """STRICT_WITHHOLD with NO output rules at all -> pass-through (holds nothing, 3.7)."""
    plan = _plan(mode=StreamingMode.STRICT_WITHHOLD)
    assert not enforcing_output_rules(plan), "pass-through strict must have no enforcing rule"
    return plan


def _strict_off_or_flag_only_plan() -> ExecutionPlan:
    """STRICT_WITHHOLD whose only OUTPUT rules are ``mode=off`` and a ``flag`` (ALLOW) action.

    Neither is an Enforcing_Output_Rule (``enforcing_output_rules`` filters to
    ENFORCE + REDACT/BLOCK/REWRITE), so ``withholds_until_decision`` is False and the stream
    keeps the write-based idle model (3.7).
    """
    off_rule = _ip_leak_rule(rule_mode=Mode.OFF, action=Action.BLOCK)
    flag_rule = _ip_leak_rule(rule_mode=Mode.ENFORCE, action=Action.ALLOW)
    plan = _plan(mode=StreamingMode.STRICT_WITHHOLD, rules=(off_rule, flag_rule))
    assert not enforcing_output_rules(plan), "off/flag-only strict must have no enforcing rule"
    return plan


# --------------------------------------------------------------------------- #
# Route + clock + provider scaffolding for both layers.
# --------------------------------------------------------------------------- #


class _StubClock:
    """A manually-advanced seconds clock for the injected ``clock`` seam (zero wall time)."""

    __slots__ = ("now",)

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def _fresh_snapshot() -> InFlightSnapshot:
    return InFlightSnapshot(
        available=True, age_s=0.0, kill_switch_engaged=False, key_revoked=False,
        plan_changed=False,
    )


class _FreshControlSource:
    """An ``InFlightControlSource`` returning a fresh snapshot — no in-flight trigger fires."""

    __slots__ = ()

    def snapshot(self) -> InFlightSnapshot:
        return _fresh_snapshot()


def _no_findings(_text: str) -> tuple[Finding, ...]:
    return ()


class _AllowResolver:
    """An ``OutputResolver`` that ALLOWs every channel (the sanitized-content path)."""

    __slots__ = ()

    def decide(self, matches: Sequence[Finding]) -> Decision:
        return Decision(
            disposition=Disposition.ALLOW,
            per_finding=(),
            transformations=(),
            findings=tuple(matches),
            plan_version="wiring-test",
            deciding_rules=(),
            unavailable_detectors=(),
        )


class _BlockOnFindingResolver:
    """An ``OutputResolver`` that BLOCKs the whole response when any EXECUTED finding is present.

    The route injects no base ``output_detector``, so for the enforcing strict plan (an ip_leak
    OUTPUT rule) ``build_output_detectors`` makes the whole-response detector the real
    :class:`~gateway_v2.detect.output.ip_leak.IpLeakDetector`. The SLOWSTREAM content carries an
    internal address (``10.0.0.1``), so the detector yields an EXECUTED finding at ``final`` and
    this resolver blocks — a deterministic BLOCK driven by the real detector, no sentinel probe.
    """

    __slots__ = ()

    def decide(self, matches: Sequence[Finding]) -> Decision:
        from gateway_v2.domain import FindingStatus

        block = any(f.status is FindingStatus.EXECUTED for f in matches)
        return Decision(
            disposition=Disposition.BLOCK if block else Disposition.ALLOW,
            per_finding=(),
            transformations=(),
            findings=tuple(matches),
            plan_version="wiring-test",
            deciding_rules=("out_ip_leak",) if block else (),
            unavailable_detectors=(),
        )


def _route(
    *,
    contract: ResourceContract,
    clock: _StubClock,
    provider: ProviderClient,
    resolver: OutputResolver,
) -> ChatRoute:
    """A real ``ChatRoute`` wired to production seams with ``provider`` + ``resolver`` injected."""
    return ChatRoute(
        org_limits=org_limits_for(contract),
        contract=contract,
        provider=provider,
        scanner=cast(ScanProtocol, holdback_scan),
        resolver=resolver,
        cfg=HoldbackConfig(hold_cap_tokens=3, relaxed_ceiling_tokens=6),
        router=DispatchRouter(),
        active_streams=lambda: 1,
        control=_FreshControlSource(),
        clock=clock,
        error_scanner=_no_findings,
        executor=ScanExecutor(contract=contract),
    )


# =========================================================================== #
# Layer 1 — the _controls flag truth table (the wiring under test).
# =========================================================================== #


def _liveness_flag_for(plan: ExecutionPlan) -> bool:
    """Build a ``StreamTimeouts`` through the REAL ``_controls`` and read its liveness flag."""
    contract = _contract()
    clock = _StubClock()
    route = _route(
        contract=contract, clock=clock, provider=_SilentProvider(clock, 0.0),
        resolver=_AllowResolver(),
    )
    try:
        binding = StreamBinding(org_id="org", model="m", key_hash="k", key_feed_seq=0)
        _control, timeouts = ChatStream(route)._controls(binding, KillLatch(), plan)
        return timeouts._upstream_liveness  # noqa: SLF001
    finally:
        route.executor.shutdown()


def test_controls_sets_liveness_for_strict_enforcing_plan() -> None:
    """``_controls`` sets ``upstream_liveness`` for STRICT_WITHHOLD + an enforcing rule (2.1, 2.2).

    # Feature: strict-withhold-idle-timeout, Property 1
    # Validates: Requirements 2.1, 2.2
    """
    plan = _strict_enforcing_plan()
    assert withholds_until_decision(plan), "the predicate must agree the plan withholds"
    assert _liveness_flag_for(plan) is True


def test_controls_does_not_set_liveness_for_incremental() -> None:
    """INCREMENTAL keeps the write-based idle model: the flag is not set (3.1).

    # Feature: strict-withhold-idle-timeout, Property 4: Preservation
    # Validates: Requirements 3.1
    """
    plan = _incremental_plan()
    assert withholds_until_decision(plan) is False
    assert _liveness_flag_for(plan) is False


def test_controls_does_not_set_liveness_for_passthrough_strict() -> None:
    """Pass-through strict (no enforcing output rule) keeps the write-based model (3.7).

    # Feature: strict-withhold-idle-timeout, Property 4: Preservation
    # Validates: Requirements 3.7
    """
    plan = _passthrough_strict_plan()
    assert withholds_until_decision(plan) is False
    assert _liveness_flag_for(plan) is False


def test_controls_does_not_set_liveness_for_off_or_flag_only_strict() -> None:
    """A strict plan whose only OUTPUT rules are off/flag does not withhold: no flag (3.7).

    # Feature: strict-withhold-idle-timeout, Property 4: Preservation
    # Validates: Requirements 3.7
    """
    plan = _strict_off_or_flag_only_plan()
    assert withholds_until_decision(plan) is False
    assert _liveness_flag_for(plan) is False


# =========================================================================== #
# Layer 2 — ASGI-level behaviour through _stream_response.
# =========================================================================== #


class _SilentProvider:
    """A clock-advancing in-process ``ProviderClient``: SLOWSTREAM, then one ``final`` event.

    Advances the injected clock by ``gap_s`` as each content event is yielded (zero wall time),
    so the whole stream takes 6 s of injected time. ``content`` is the per-event text.

    ``stall_after`` (if set) makes the provider go genuinely silent after that many content
    events, by awaiting a never-resolving event — a REAL upstream stall, so the inter-chunk
    ``asyncio.wait_for`` bound (made microseconds by a tiny-SLO contract) actually trips. The
    injected clock cannot drive ``wait_for`` (it is wall-clock), so a true stall must block.
    """

    __slots__ = ("_clock", "_gap_s", "_content", "_stall_after", "_aborted")

    def __init__(
        self,
        clock: _StubClock,
        gap_s: float,
        *,
        content: str = "token",
        stall_after: int | None = None,
    ) -> None:
        self._clock = clock
        self._gap_s = gap_s
        self._content = content
        self._stall_after = stall_after
        self._aborted = False

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        async def _gen() -> AsyncIterator[UpstreamEvent]:
            for i in range(_SLOWSTREAM_CHUNKS):
                if self._aborted:
                    return
                if self._stall_after is not None and i >= self._stall_after:
                    # A genuine upstream stall: block so the inter-chunk wait_for bound trips.
                    await asyncio.Event().wait()
                self._clock.now += self._gap_s
                yield UpstreamEvent(
                    text_deltas=((_CONTENT_CHANNEL, f"{self._content}{i} "),), final=False,
                )
            if self._aborted:
                return
            self._clock.now += self._gap_s
            yield UpstreamEvent(text_deltas=((_CONTENT_CHANNEL, "done."),), final=True)

        return _gen()

    async def abort(self) -> None:
        self._aborted = True


class _Capture:
    """Capture the SSE ``data:`` frame bodies written onto the ASGI ``send`` channel, in order."""

    __slots__ = ("frames", "started")

    def __init__(self) -> None:
        self.frames: list[bytes] = []
        self.started = False

    async def send(self, event: Mapping[str, object]) -> None:
        kind = event.get("type")
        if kind == "http.response.start":
            self.started = True
            return
        if kind != "http.response.body":
            return
        body = event.get("body")
        if isinstance(body, (bytes, bytearray)) and body:
            self.frames.append(bytes(body))

    @property
    def wire(self) -> bytes:
        return b"".join(self.frames)


def _proceed(plan: ExecutionPlan) -> LifecycleOutcome:
    return LifecycleOutcome(
        plan=plan,
        destination="dest",
        body={},
        refuse_code=None,
        routed_model="gpt-test",
        binding=StreamBinding(org_id="org", model="gpt-test", key_hash="k", key_feed_seq=0),
    )


def _drive_asgi(
    *,
    provider: ProviderClient,
    resolver: OutputResolver,
    plan: ExecutionPlan,
    clock: _StubClock,
    contract: ResourceContract | None = None,
) -> tuple[_Capture, RequestHolds]:
    """Drive ``_stream_response`` end to end and return ``(capture, holds)``."""
    if contract is None:
        contract = _contract()
    route = _route(contract=contract, clock=clock, provider=provider, resolver=resolver)
    capture = _Capture()
    holds = RequestHolds()
    holds.note_request("rid-wiring")
    try:
        asyncio.run(
            route._stream_response(  # noqa: SLF001
                capture.send, plan, "dest", {}, holds, None, outcome=_proceed(plan),
            )
        )
    finally:
        route.executor.shutdown()
    return capture, holds


def test_asgi_6s_withheld_strict_stream_returns_200_with_sanitized_content() -> None:
    """A 6 s withheld strict stream answers 200 with content; no idle cut recorded (1.3, 2.1, 2.2).

    # Feature: strict-withhold-idle-timeout, Property 1: Expected Behavior
    # Validates: Requirements 1.3, 2.1, 2.2

    The SLOWSTREAM (40 chunks 0.15 s apart = 6 s) under an enforcing strict plan. With the
    ``_controls`` liveness wiring the first (and only) content write is NOT cut as a false idle;
    the SSE response opens (200) and carries the sanitized content, and the per-request
    ``failure_code`` — the idle-counter / event-log signal — never records ``stream_idle_timeout``.
    """
    clock = _StubClock()
    capture, holds = _drive_asgi(
        provider=_SilentProvider(clock, _SLOWSTREAM_GAP_S),
        resolver=_AllowResolver(),
        plan=_strict_enforcing_plan(),
        clock=clock,
    )

    detail = f"failure_code={holds.failure_code!r}; wire={capture.wire[:120]!r}"
    assert capture.started, f"the SSE response never opened (a pre-byte error); {detail}"
    assert holds.failure_code != posture.STREAM_IDLE_TIMEOUT, (
        f"a false idle cut was recorded on a healthy withheld strict stream; {detail}"
    )
    # The idle-counter / event-log signal stays clear: no cut of any kind occurred.
    assert holds.failure_code == "", f"an unexpected cut was recorded; {detail}"
    assert posture.STREAM_IDLE_TIMEOUT.encode() not in capture.wire, detail
    assert b"token0" in capture.wire, f"the sanitized content was not delivered; {detail}"
    assert b"done." in capture.wire, f"the final token was not delivered; {detail}"
    assert b"[DONE]" in capture.wire, f"the stream did not terminate cleanly; {detail}"


def test_asgi_block_payload_gives_terminal_output_blocked_with_zero_content_before() -> None:
    """A block payload yields a terminal ``output_blocked`` with zero content before it (3.6).

    # Feature: strict-withhold-idle-timeout, Property 4: Preservation
    # Validates: Requirements 3.6

    The withheld strict stream is decided at ``final``; the resolver blocks the whole response, so
    the pipeline emits a single terminal ``output_blocked`` frame and NO content frame precedes it
    (the zero-content-before-block guarantee). The liveness wiring does not change the frames.
    """
    clock = _StubClock()
    capture, holds = _drive_asgi(
        provider=_SilentProvider(clock, _SLOWSTREAM_GAP_S, content=_BLOCK_SENTINEL),
        resolver=_BlockOnFindingResolver(),
        plan=_strict_enforcing_plan(),
        clock=clock,
    )

    wire = capture.wire
    detail = f"failure_code={holds.failure_code!r}; wire={wire[:200]!r}"
    assert b"output_blocked" in wire, f"no terminal output_blocked frame was emitted; {detail}"
    # Zero content before the block: the sentinel content bytes never reached the wire.
    assert _BLOCK_SENTINEL.encode() not in wire, (
        f"content was released before the block (zero-content-before-block violated); {detail}"
    )
    # And no false idle cut slipped in on the 6 s withheld path.
    assert posture.STREAM_IDLE_TIMEOUT.encode() not in wire, detail
    assert holds.failure_code != posture.STREAM_IDLE_TIMEOUT, detail


def _small_slo_contract() -> ResourceContract:
    """A small-SLO contract so the REAL inter-chunk ``wait_for`` bound trips in tens of ms.

    ``target_p99_ms=1.0`` gives inter-chunk 50 ms and idle 150 ms — both real wall-clock bounds a
    genuinely stalled upstream must respect — while max-stream-duration stays 15 s and snapshot age
    1 s, comfortably above the near-zero injected-clock time the few pre-stall events elapse. This
    lets a real ``asyncio.Event().wait()`` stall trip the inter-chunk bound deterministically in
    tens of milliseconds without a multi-second wall-clock wait, the same device the 3.2 unit test
    (``tests/runtime/test_stream_timeouts_liveness.py``) uses for the stall cut.
    """
    return ResourceContract(
        cpu_quota=4.0,
        memory_limit=1 << 34,
        fd_limit=4096,
        guard_capacity=None,
        target_p99_ms=1.0,
        utilization_cap=0.75,
        per_worker_rss=400 << 20,
        worker_override=None,
        cpu_source="t",
        mem_source="t",
        fd_source="t",
    )


def test_asgi_upstream_silence_gives_terminal_inter_chunk_timeout() -> None:
    """An upstream silence gives a terminal ``stream_inter_chunk_timeout``, not idle (2.3, 3.2).

    # Feature: strict-withhold-idle-timeout, Property 2
    # Validates: Requirements 2.3, 3.2

    Inter-chunk (stricter than idle) is checked on the next-event await, so an upstream that goes
    genuinely quiet mid-stream is cut by ``next_event`` first — the liveness flag does not suppress
    the stall cut. The provider emits one content event (which re-seeds the idle clock in liveness
    mode) and then blocks forever; the real inter-chunk ``wait_for`` bound trips and renders
    ``stream_inter_chunk_timeout`` with no content. The injected clock does not advance before the
    stall, so the small-SLO max-duration (15 s) never pre-empts the inter-chunk cut.
    """
    clock = _StubClock()
    contract = _small_slo_contract()
    # One content event, then a genuine stall (no clock advance before it), so the small-SLO
    # inter-chunk wait_for (50 ms) trips before anything else.
    provider = _SilentProvider(clock, 0.0, stall_after=1)
    capture, holds = _drive_asgi(
        provider=provider, resolver=_AllowResolver(), plan=_strict_enforcing_plan(),
        clock=clock, contract=contract,
    )

    detail = f"failure_code={holds.failure_code!r}; wire={capture.wire[:200]!r}"
    assert holds.failure_code == posture.STREAM_INTER_CHUNK_TIMEOUT, (
        f"the upstream stall was not cut with the inter-chunk code; {detail}"
    )
    # The stall is NOT a false idle cut, and no content was released (withheld strict).
    assert holds.failure_code != posture.STREAM_IDLE_TIMEOUT, detail
    assert b"token0" not in capture.wire, f"content leaked on a stalled withheld stream; {detail}"
