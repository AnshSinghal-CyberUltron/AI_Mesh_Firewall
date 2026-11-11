# Feature: observability-contract, Property 11: Readiness composition gates state first
# Validates: Requirements 10.1, 10.2, 10.4, 10.5
"""Property 11 — ``make_metrics_readiness`` gates state FIRST, then the directory.

House idiom: a seeded ``random.Random``, no ``hypothesis``, >=10,000 iterations.

For any pair of (``state_ready(view)`` result, directory availability): the probe reports ready iff
BOTH gates are ready; when state is not ready the reason is the STATE reason regardless of the
directory; when state is ready but the directory is unavailable the reason names the metrics
directory; and a single reason is always returned.
"""

from __future__ import annotations

import random

from gateway_v2.edge.readiness import METRICS_DIRECTORY_REASON, make_metrics_readiness
from gateway_v2.runtime.state_stamp import StampView, state_ready

_SEED = 0x0B5E_000B
_ITERATIONS = 10_000


def _ctx(seed: int, i: int, **extra: object) -> str:
    body = " ".join(f"{k}={v!r}" for k, v in extra.items())
    return f"seed={seed:#x} iter={i} {body}"


class _StubDirectory:
    """A minimal :class:`DirectoryGate`: ``available()`` returns a fixed, caller-set flag."""

    __slots__ = ("_available",)

    def __init__(self, available: bool) -> None:
        self._available = available

    def available(self) -> bool:
        return self._available


def _ready_view() -> StampView | None:
    """A view that reports READY: ``None`` makes ``state_ready`` return ``(True, None)``."""
    return None


def _unready_view() -> StampView:
    """A view that reports NOT ready: a fresh StampView that has observed no stamp => not fresh.

    ``state_ready`` then returns ``(False, <state reason>)`` — the ``NO_STAMP_SEEN`` message.
    """
    return StampView(secret=b"test-secret", clock=lambda: 1000.0)


def test_property_11_readiness_gates_state_first() -> None:
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        state_is_ready = rng.random() < 0.5
        directory_available = rng.random() < 0.5

        view = _ready_view() if state_is_ready else _unready_view()
        directory = _StubDirectory(directory_available)
        probe = make_metrics_readiness(view, directory)

        ready, reason = probe()

        # The independent truth of the state gate.
        state_ready_flag, state_reason = state_ready(view)
        assert state_ready_flag == state_is_ready, _ctx(
            _SEED, i, note="state gate truth mismatch"
        )

        # ready iff BOTH gates ready (R10.2/R10.5).
        expected_ready = state_ready_flag and directory_available
        assert ready == expected_ready, _ctx(
            _SEED,
            i,
            note="ready != (state and directory)",
            ready=ready,
            state=state_ready_flag,
            dir=directory_available,
        )

        if not state_ready_flag:
            # State gated first: reason is the STATE reason regardless of the directory (R10.4).
            assert reason == state_reason, _ctx(
                _SEED, i, note="not the state reason when state unready", got=reason
            )
            assert reason is not None, _ctx(_SEED, i, note="unready with no reason")
        elif not directory_available:
            # State ready, directory down: reason names the metrics directory (R10.1/R10.3).
            assert reason == METRICS_DIRECTORY_REASON, _ctx(
                _SEED, i, note="not the directory reason", got=reason
            )
        else:
            # Both ready: no reason.
            assert ready is True and reason is None, _ctx(
                _SEED, i, note="both ready but a reason returned", reason=reason
            )

        # Exactly one reason (or None) is ever returned — the return is a single scalar.
        assert reason is None or isinstance(reason, str), _ctx(
            _SEED, i, note="reason is not a single string/None", reason=reason
        )
