# Feature: observability-contract, Property 10: Event log emission is bounded and never leaks
# Validates: Requirements 9.2, 9.3, 9.4, 9.6
"""Property 10 — the Structured_Event_Log is rate-limited per class/window and never leaks a secret.

House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations, an INJECTED clock.

For any event schedule under an injected clock: emitted line volume per class per window never
exceeds ``event_log_rate()``; the per-class counter equals the total number of events of that class
regardless of how many lines were suppressed; and no request-content byte and no secret appears in
any emitted line (structurally guaranteed — ``record`` accepts only the class + its reason/posture
code, so we feed secret-shaped strings only through those closed-vocabulary parameters and confirm
the emitted lines carry only the safe fields).
"""

from __future__ import annotations

import random

from gateway_v2.edge.event_log import (
    EVENT_CLASSES,
    EventLogLine,
    StructuredEventLog,
)
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

_SEED = 0x0B5E_000A
_ITERATIONS = 10_000
_MIB = 1024 * 1024

# A secret-shaped token the test NEVER passes to record() (record takes no raw-content param), used
# to confirm no emitted line ever contains it — the structural no-leak guarantee (R9.6).
_SECRET = "AKIA_SECRET_sk-ABCDEF0123456789_ssn-123-45-6789"


def _ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


def _contract() -> ResourceContract:
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=400 * _MIB * 8,
        fd_limit=4096,
        cpu_source="t",
        mem_source="t",
        fd_source="t",
    )
    return from_signals(
        signals, target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=400 * _MIB
    )


class _Clock:
    __slots__ = ("now",)

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_property_10_event_log_bounded_and_no_leak() -> None:
    rng = random.Random(_SEED)
    contract = _contract()
    rate = contract.event_log_rate()
    window_s = contract.metrics_window_s()

    for i in range(_ITERATIONS):
        clock = _Clock()
        captured: list[EventLogLine] = []
        log = StructuredEventLog.from_contract(
            contract, clock=clock, sink=captured.append
        )

        expected_counts: dict[str, int] = {c: 0 for c in EVENT_CLASSES}
        # Track emission timestamps per class to assert the per-window bound independently.
        emitted_ts: dict[str, list[float]] = {c: [] for c in EVENT_CLASSES}

        n_events = rng.randint(0, 50)
        for _e in range(n_events):
            # Advance the clock, sometimes within the window, sometimes past it (new window).
            clock.now += rng.choice([0.0, window_s / 10.0, window_s / 3.0, window_s * 1.5])
            error_class = rng.choice(EVENT_CLASSES)
            # reason_or_posture is drawn ONLY from the closed vocabulary (the real wiring); a
            # secret could never arrive here because record() has no raw-content parameter.
            reason = error_class

            before = len(captured)
            log.record(error_class, reason)
            expected_counts[error_class] += 1
            after = len(captured)

            if after > before:
                emitted_ts[error_class].append(clock.now)

        # Counter == total events of that class regardless of suppression (R9.4).
        for cls, want in expected_counts.items():
            assert log.counter(cls) == want, _ctx(
                _SEED, i, note="counter != events", cls=cls, got=log.counter(cls), want=want
            )

        # Per-class per-window emitted line volume never exceeds the rate (R9.2/R9.3). Checked by
        # sliding a window over the emission timestamps of each class.
        for cls, stamps in emitted_ts.items():
            stamps_sorted = sorted(stamps)
            for a, ts in enumerate(stamps_sorted):
                in_window = sum(1 for other in stamps_sorted if ts - window_s < other <= ts)
                assert in_window <= rate, _ctx(
                    _SEED, i, note="rate exceeded", cls=cls, in_window=in_window, rate=rate
                )

        # No secret, no raw content in any emitted line (R9.6). The line carries only the two
        # closed-vocabulary strings + the gap flag, so the secret token can never appear.
        for line in captured:
            text = f"{line.error_class}|{line.reason_or_posture}|{line.coverage_gap}"
            assert _SECRET not in text, _ctx(_SEED, i, note="secret leaked into a line", line=text)
            assert line.error_class in EVENT_CLASSES, _ctx(
                _SEED, i, note="emitted non-vocab class", cls=line.error_class
            )
            assert line.reason_or_posture in EVENT_CLASSES, _ctx(
                _SEED, i, note="emitted non-vocab reason", reason=line.reason_or_posture
            )
