"""Open-loop arrival generator for the load harness (B6 / R2-21).

Generates the request **arrival schedule** — the absolute start times the driver will dispatch at —
up front, from the arrival process alone (Req 1.1). Two modes:

- **Poisson** (the core capacity-sizing mode, R2-01 / Req 1.2): inter-arrival times drawn from an
  exponential distribution parameterized by the configured offered rate, via the inverse-CDF
  transform ``-ln(1 - u) / rate`` with ``u = rng.random()``.
- **Constant** (Req 1.6): fixed inter-arrival times ``1 / rate``; the ladder/report label the
  resulting limits as upper bounds.

Determinism spine (Req 13.1, 13.2, 13.3). The whole schedule is a **pure function** of
``(mode, offered_rate_per_s, count, rng, start_s)``: it threads the injected ``random.Random``
(never module-global ``random.*``) and an injected ``Clock`` for the start reference, and touches
**no** network or server, so a schedule is generated and verified offline and the same seed + same
start produce a byte-identical schedule. The driver's open-loop contract (dispatch at
``scheduled_s``, never wait on a prior response — Req 1.5) is layered on top of this schedule in
``driver.py``; this module only computes the schedule.

``ArrivalMode`` is imported from ``config`` (its one authoritative definition lives on the
``RunSpec`` value type that references it); it is NOT redefined here. ``config`` imports nothing
from ``arrivals``, so there is no circular edge. This module adds no capacity-position literal and
no module-level mutable; both value types are frozen + slots (Req 14.2, 14.3).
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass

from load_harness.config import ArrivalMode

__all__ = (
    "Arrival",
    "ArrivalSchedule",
    "Clock",
    "build_schedule",
)

#: The injected monotonic clock (seconds), same shape as ``serve.py`` / ``http_provider.py``. It
#: supplies the schedule's absolute start reference; it is never read from the wall-clock here, so a
#: schedule stays reproducible offline (Req 13.1, 13.3).
Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class Arrival:
    """One scheduled arrival: its ordinal ``index`` and absolute ``scheduled_s`` start time.

    ``scheduled_s`` is the absolute start produced by the arrival process (``start_s`` plus the
    accumulated inter-arrival times). It is the open-loop target the driver dispatches at, measured
    against the same injected clock (Req 1.1); a dispatch slipping past it by more than the
    Schedule_Drop tolerance is a drop (accounted in ``driver.py``, Req 1.3).
    """

    index: int
    scheduled_s: float


@dataclass(frozen=True, slots=True)
class ArrivalSchedule:
    """The full up-front arrival schedule for one offered run.

    ``mode`` is POISSON | CONSTANT; ``offered_rate_per_s`` is the configured offered rate the
    schedule was drawn against; ``arrivals`` is the ordered tuple of scheduled arrivals (ascending
    ``scheduled_s``, ``index`` ``0..count-1``). Frozen + slots so the schedule is an immutable,
    byte-reproducible value (Req 13.2, 14.3).
    """

    mode: ArrivalMode
    offered_rate_per_s: float
    arrivals: tuple[Arrival, ...]


def _inter_arrival_s(mode: ArrivalMode, offered_rate_per_s: float, rng: random.Random) -> float:
    """One inter-arrival gap (seconds) for ``mode`` at ``offered_rate_per_s``.

    Poisson: the inverse-CDF exponential draw ``-ln(1 - u) / rate`` with ``u = rng.random()`` in
    ``[0.0, 1.0)`` (so ``1 - u`` is in ``(0.0, 1.0]`` and the log is finite — Req 1.2). Constant:
    the fixed gap ``1 / rate`` (Req 1.6), consuming no randomness.
    """
    if mode is ArrivalMode.CONSTANT:
        return 1.0 / offered_rate_per_s
    return -math.log(1.0 - rng.random()) / offered_rate_per_s


def build_schedule(
    *,
    mode: ArrivalMode,
    offered_rate_per_s: float,
    count: int,
    rng: random.Random,
    clock: Clock,
) -> ArrivalSchedule:
    """Build the full open-loop arrival schedule up front (Req 1.1, 1.2, 1.6, 13.1, 13.3).

    ``offered_rate_per_s`` and ``count`` are the configured inputs (never capacity literals);
    ``rng`` is the injected, seeded ``random.Random`` (the only randomness source); ``clock`` is the
    injected monotonic clock, read **once** for the schedule's absolute ``start_s`` reference. The
    result is a pure function of these arguments — no network, no server, no module-global
    ``random.*`` or wall-clock — so the same seeded ``rng`` and ``clock`` yield a byte-identical
    ``ArrivalSchedule`` (Req 13.2).

    Each inter-arrival gap is accumulated onto ``start_s`` to give the absolute ``scheduled_s`` of
    the next arrival; a negative or zero ``count`` yields an empty schedule. ``offered_rate_per_s``
    is assumed already validated positive by ``config.load_run_spec`` (its warn-and-default guard
    rails reject ``<= 0``), so the ``1 / rate`` divisions are finite.
    """
    start_s = clock()
    arrivals: list[Arrival] = []
    cursor_s = start_s
    for index in range(max(count, 0)):
        cursor_s += _inter_arrival_s(mode, offered_rate_per_s, rng)
        arrivals.append(Arrival(index=index, scheduled_s=cursor_s))
    return ArrivalSchedule(
        mode=mode,
        offered_rate_per_s=offered_rate_per_s,
        arrivals=tuple(arrivals),
    )
