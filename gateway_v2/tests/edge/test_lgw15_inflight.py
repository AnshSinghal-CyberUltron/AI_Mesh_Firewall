# Feature: chat-surface-gw15
# Validates: Requirements 10.4
"""Per-trigger in-flight example tests (task 5.9).

One EXAMPLE per :class:`~gateway_v2.edge.cancel.InFlightControl` trigger — max stream duration,
kill switch, key revocation, plan change, and a stale/unavailable snapshot (fail closed) — each
asserting the matching DECLARED posture code is rendered at ``ChatRoute._finish_stream``'s single
terminal site as the terminal SSE ``Error_Frame``:

* max stream duration (R10.2) → :data:`~gateway_v2.domain.posture.STREAM_MAX_DURATION`
* kill switch engaged (R10.3) → :data:`~gateway_v2.domain.posture.STREAM_KILLED`
* key revoked (R10.4)        → :data:`~gateway_v2.domain.posture.STREAM_KEY_REVOKED`
* plan changed (R10.5)       → :data:`~gateway_v2.domain.posture.STREAM_PLAN_CHANGED`
* snapshot stale / unavailable (R10.6, fail closed) →
  :data:`~gateway_v2.domain.posture.STREAM_SNAPSHOT_STALE`

These are supporting example/unit tests (one per trigger), NOT a property loop — there is no
``>= 10,000`` iteration requirement and no ``hypothesis``.

Unlike ``test_lgw12_inflight_control.py`` (which asserts the trigger → latch → reason mapping at
the ``InFlightControl.evaluate()`` level), THIS file closes the loop at the handler's single
terminal RENDER site: a real :class:`~gateway_v2.edge.cancel.InFlightControl` over a scripted
:class:`~gateway_v2.edge.cancel.InFlightControlSource` double flips the shared per-request
:class:`~gateway_v2.edge.cancel.KillLatch`, and the shipped
:meth:`~gateway_v2.edge.routes.ChatRoute._finish_stream` then renders the latch's recorded reason
code (NOT the pipeline's boolean ``killed()``) as the terminal ``Error_Frame`` through the one
:class:`~gateway_v2.edge.wire.sse.SSEEncoder`. Both the ``InFlightControl``/``KillLatch`` seams and
``_finish_stream`` are the shipped production objects — nothing is reimplemented — so each trigger
is proven to drive the declared terminal code all the way onto the wire at the single terminal
site (the design's Error Handling table, R10.4).

House idiom contract-fixture via ``from_signals`` (the LGW06/LGW12 shape); the terminal close is
driven under ``asyncio.run`` because ``_finish_stream`` is a coroutine.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from gateway_v2.domain import posture
from gateway_v2.edge.cancel import InFlightControl, InFlightSnapshot, KillLatch
from gateway_v2.edge.routes import _TerminalLatch
from gateway_v2.edge.wire.sse import DONE_MARKER, SSEEncoder, error_frame_for
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

# --------------------------------------------------------------------------- #
# Test doubles: a scripted control source + a controllable clock + an SSE capture.
# --------------------------------------------------------------------------- #


class _ScriptedSource:
    """An :class:`InFlightControlSource` double returning one scripted :class:`InFlightSnapshot`.

    Pure read, no clock, no I/O — the evaluator polls :meth:`snapshot` at each chunk boundary.
    """

    __slots__ = ("snap",)

    def __init__(self, snap: InFlightSnapshot) -> None:
        self.snap = snap

    def snapshot(self) -> InFlightSnapshot:
        return self.snap


class _Clock:
    """A controllable monotonic-seconds clock read on every ``()`` call.

    ``InFlightControl`` captures ``clock() + max_stream_duration_s()`` as the deadline at
    construction and compares the LIVE ``clock()`` against it in ``evaluate()``, so advancing
    ``now`` to the deadline fires the max-duration trigger deterministically.
    """

    __slots__ = ("now",)

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class _SSECapture:
    """Capture the non-empty SSE ``data:`` frame bodies written onto the ASGI ``send`` channel.

    ``_finish_stream`` writes exactly one terminal frame (either the declared ``Error_Frame`` for a
    cut reason or the clean ``[DONE]`` marker) via the plain ``_send_sse`` helper, which emits a
    non-terminal ``http.response.body`` event carrying the frame bytes. Only those non-empty bodies
    are the SSE frames these examples reason about.
    """

    __slots__ = ("frames",)

    def __init__(self) -> None:
        self.frames: list[bytes] = []

    async def send(self, event: Mapping[str, object]) -> None:
        if event.get("type") != "http.response.body":
            return
        body = event.get("body")
        if isinstance(body, (bytes, bytearray)) and body:
            self.frames.append(bytes(body))


def _contract(*, target_p99_ms: float = 20.0) -> ResourceContract:
    """A serviceable contract via ``from_signals`` (same shape as ``test_lgw12_resources.py``).

    The signals clear ``from_signals``' refuse-to-start checks on the CPU and RAM worker paths, so
    ``max_stream_duration_s()`` / ``max_snapshot_age_s()`` are valid derivations of
    ``target_p99_ms``. These are TEST signals, not production capacity literals.
    """
    per_worker_rss = 400 * 1024 * 1024
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=per_worker_rss * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=target_p99_ms,
        utilization_cap=0.75,
        per_worker_rss=per_worker_rss,
    )


def _clear_snapshot(*, age_s: float = 0.0) -> InFlightSnapshot:
    """A fresh, available snapshot with every signal clear — nothing fires."""
    return InFlightSnapshot(
        available=True,
        age_s=age_s,
        kill_switch_engaged=False,
        key_revoked=False,
        plan_changed=False,
    )


def _finish_terminal_code(latch: KillLatch) -> bytes:
    """Run the shipped ``ChatRoute._finish_stream`` single terminal site; return the frame bytes.

    Builds a fresh per-request :class:`SSEEncoder` + ``_TerminalLatch`` (exactly as the handler's
    ``_stream`` does) and drives the shipped
    :meth:`~gateway_v2.edge.routes.ChatRoute._finish_stream` — the ONE terminal rendering site —
    with the cut ``latch``. ``_finish_stream`` reads :meth:`KillLatch.reason` and renders it as the
    terminal ``Error_Frame`` (or emits ``[DONE]`` when nothing was cut). Returns the single SSE
    frame body ``_finish_stream`` emitted.

    ``_finish_stream`` is unbound here (``ChatRoute._finish_stream``) and called with ``self=None``:
    the terminal close reads only the ``send`` / ``encoder`` / ``kill_latch`` / ``sent_terminal``
    arguments and never touches ``self``, so no route need be constructed to exercise the single
    terminal site in isolation.
    """
    from gateway_v2.edge.routes import ChatRoute, _ProducedModeHolder

    encoder = SSEEncoder()
    sent_terminal = _TerminalLatch()
    capture = _SSECapture()
    # A fresh holder with no captured stats (no attempt completed in this isolated terminal-site
    # drive), so the GW13 Produced_Mode trace frame is NOT emitted and the single terminal site
    # still renders exactly one frame — the clean [DONE] or the declared cut Error_Frame.
    produced = _ProducedModeHolder()

    async def _drive() -> None:
        await ChatRoute._finish_stream(
            None,  # type: ignore[arg-type]  # terminal close never touches ``self``
            capture.send,
            encoder,
            latch,
            sent_terminal,
            produced,
        )

    asyncio.run(_drive())

    assert len(capture.frames) == 1, (
        f"_finish_stream emitted {len(capture.frames)} SSE frames, want exactly one terminal frame"
    )
    assert sent_terminal.is_set() or latch.reason() is None, (
        "a cut reason was present but _finish_stream did not mark the terminal latch set"
    )
    return capture.frames[0]


def _build_control(
    snap: InFlightSnapshot,
    *,
    clock: _Clock | None = None,
    contract: ResourceContract | None = None,
) -> tuple[InFlightControl, KillLatch, _Clock, ResourceContract]:
    """Build the shipped ``(InFlightControl, KillLatch, clock, contract)`` around a snapshot."""
    clk = clock if clock is not None else _Clock()
    ctr = contract if contract is not None else _contract()
    latch = KillLatch()
    control = InFlightControl(
        source=_ScriptedSource(snap),
        contract=ctr,
        clock=clk,
        kill_latch=latch,
    )
    return control, latch, clk, ctr


def _assert_terminal_error_frame(frame: bytes, expected_code: str) -> None:
    """Assert ``frame`` is the declared terminal SSE ``Error_Frame`` for ``expected_code``.

    The shipped :meth:`SSEEncoder.error` renders a code via ``error_frame_for`` as a
    ``data: {"error": {...}}\\n\\n`` body whose ``code`` echoes the posture code verbatim, so the
    byte-exact expected frame is ``SSEEncoder().error(expected_code)``. We assert the whole frame
    matches that declared shape (not merely that the code string appears), is NOT the clean
    ``[DONE]`` marker, and carries the ``"error"`` object a cut terminal frame must.
    """
    assert frame != DONE_MARKER, (
        f"a cut rendered the clean [DONE] marker instead of the declared Error_Frame "
        f"for {expected_code!r}"
    )
    assert b'"error"' in frame, (
        f"the terminal frame for {expected_code!r} is not an SSE Error_Frame: {frame!r}"
    )
    expected_frame = SSEEncoder().error(expected_code)
    assert frame == expected_frame, (
        f"terminal frame {frame!r} is not the declared Error_Frame for {expected_code!r} "
        f"(expected {expected_frame!r})"
    )
    # The declared body echoes the posture code verbatim under ``code`` (the single spelling).
    assert error_frame_for(expected_code).code == expected_code


# --------------------------------------------------------------------------- #
# One example per R10 trigger: fired via the real InFlightControl, rendered at
# _finish_stream's single terminal site.
# --------------------------------------------------------------------------- #


def test_max_stream_duration_renders_stream_max_duration_at_finish() -> None:
    """R10.2: clock past the derived deadline → ``STREAM_MAX_DURATION`` terminal frame."""
    clk = _Clock(start=0.0)
    contract = _contract()
    control, latch, _clk, _ctr = _build_control(
        _clear_snapshot(), clock=clk, contract=contract
    )

    # Not yet at the deadline: nothing fires.
    assert control.evaluate() is None
    assert not latch.is_killed()

    # Advance to the derived deadline so the max-duration trigger fires.
    clk.now = contract.max_stream_duration_s()
    assert control.evaluate() == posture.STREAM_MAX_DURATION
    assert latch.reason() == posture.STREAM_MAX_DURATION

    frame = _finish_terminal_code(latch)
    _assert_terminal_error_frame(frame, posture.STREAM_MAX_DURATION)


def test_kill_switch_renders_stream_killed_at_finish() -> None:
    """R10.3: kill switch engaged → ``STREAM_KILLED`` terminal frame."""
    snap = InFlightSnapshot(
        available=True,
        age_s=0.0,
        kill_switch_engaged=True,
        key_revoked=False,
        plan_changed=False,
    )
    control, latch, _clk, _ctr = _build_control(snap)

    assert control.evaluate() == posture.STREAM_KILLED
    assert latch.reason() == posture.STREAM_KILLED

    frame = _finish_terminal_code(latch)
    _assert_terminal_error_frame(frame, posture.STREAM_KILLED)


def test_key_revoked_renders_stream_key_revoked_at_finish() -> None:
    """R10.4: key revoked mid-stream → ``STREAM_KEY_REVOKED`` terminal frame."""
    snap = InFlightSnapshot(
        available=True,
        age_s=0.0,
        kill_switch_engaged=False,
        key_revoked=True,
        plan_changed=False,
    )
    control, latch, _clk, _ctr = _build_control(snap)

    assert control.evaluate() == posture.STREAM_KEY_REVOKED
    assert latch.reason() == posture.STREAM_KEY_REVOKED

    frame = _finish_terminal_code(latch)
    _assert_terminal_error_frame(frame, posture.STREAM_KEY_REVOKED)


def test_plan_changed_renders_stream_plan_changed_at_finish() -> None:
    """R10.5: plan snapshot changed mid-stream → ``STREAM_PLAN_CHANGED`` terminal frame."""
    snap = InFlightSnapshot(
        available=True,
        age_s=0.0,
        kill_switch_engaged=False,
        key_revoked=False,
        plan_changed=True,
    )
    control, latch, _clk, _ctr = _build_control(snap)

    assert control.evaluate() == posture.STREAM_PLAN_CHANGED
    assert latch.reason() == posture.STREAM_PLAN_CHANGED

    frame = _finish_terminal_code(latch)
    _assert_terminal_error_frame(frame, posture.STREAM_PLAN_CHANGED)


def test_stale_snapshot_renders_stream_snapshot_stale_at_finish() -> None:
    """R10.6: ``age_s > max_snapshot_age_s()`` → fail-closed ``STREAM_SNAPSHOT_STALE`` frame."""
    contract = _contract()
    stale = InFlightSnapshot(
        available=True,
        age_s=contract.max_snapshot_age_s() * 2.0,  # strictly past the freshness bound
        kill_switch_engaged=False,
        key_revoked=False,
        plan_changed=False,
    )
    control, latch, _clk, _ctr = _build_control(stale, contract=contract)

    assert control.evaluate() == posture.STREAM_SNAPSHOT_STALE
    assert latch.reason() == posture.STREAM_SNAPSHOT_STALE

    frame = _finish_terminal_code(latch)
    _assert_terminal_error_frame(frame, posture.STREAM_SNAPSHOT_STALE)


def test_unavailable_snapshot_renders_stream_snapshot_stale_at_finish() -> None:
    """R10.6: an unreadable snapshot (``available=False``) fails closed → ``STREAM_SNAPSHOT_STALE``.

    A second stale-snapshot example covering the unavailability branch (absence ≠ "off"), the
    sibling fail-closed path to the age-bound case above.
    """
    unavailable = InFlightSnapshot(
        available=False,
        age_s=0.0,  # young but unreadable: still fails closed
        kill_switch_engaged=False,
        key_revoked=False,
        plan_changed=False,
    )
    control, latch, _clk, _ctr = _build_control(unavailable)

    assert control.evaluate() == posture.STREAM_SNAPSHOT_STALE
    assert latch.reason() == posture.STREAM_SNAPSHOT_STALE

    frame = _finish_terminal_code(latch)
    _assert_terminal_error_frame(frame, posture.STREAM_SNAPSHOT_STALE)


def test_clean_finish_renders_done_not_an_error_frame() -> None:
    """Baseline: no trigger fired → the single terminal site emits the clean ``[DONE]`` marker.

    The complement of the five cut examples: when the real ``InFlightControl`` over a fresh,
    available, all-clear snapshot fires nothing, the shared :class:`KillLatch` stays unset and
    ``_finish_stream`` renders the clean terminal ``[DONE]`` — NOT a declared ``Error_Frame`` — at
    the same single terminal site, so an in-flight cut is the only thing that can surface a
    terminal error code there.
    """
    control, latch, _clk, _ctr = _build_control(_clear_snapshot())

    assert control.evaluate() is None
    assert not latch.is_killed()
    assert latch.reason() is None

    frame = _finish_terminal_code(latch)
    assert frame == DONE_MARKER, (
        f"a clean (uncut) stream rendered {frame!r} instead of the terminal [DONE] marker"
    )
    assert b'"error"' not in frame
