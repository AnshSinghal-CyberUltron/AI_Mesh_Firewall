"""Rolling-window C4 add-on histogram + the windowed-busy gauge reuse (GW14d, task 5.1).

This module owns the ONE quantity R2-11 measured the shipped lifetime histogram getting wrong:
the server-side p99 under-read the synthetic client's p99 by 3.2-5.8 ms because it mixed lifetime
samples into a window the client computed fresh. :class:`WindowedHistogram` is a NEW rolling type,
DISTINCT from the shipped lifetime
:class:`~gateway_v2.runtime.holdback_metrics.Histogram`: it keeps every sample stamped with the
clock reading at which it was observed, prunes everything older than one window before reporting a
quantile, and computes p50/p99 from the in-window samples ONLY. It never retains, computes, or
falls back to a lifetime quantile (R4.3).

Three measured-reason rules, inherited from the producer idiom and the design:

* **Window length is DERIVED, never a literal.** ``_window_s`` comes from
  :meth:`~gateway_v2.runtime.resources.ResourceContract.metrics_window_s`, the single capacity
  authority (R4.2). The one tuple this module names, :data:`DEFAULT_BOUNDS`, is a
  *measurement-domain* boundary set (millisecond bucket edges spanning 0..>=1000 ms), NOT a
  capacity-position literal: it describes the resolution at which latency is bucketed, the same way
  the renderer in :mod:`gateway_v2.edge.render` takes ``bounds`` from its caller. Callers may
  override it; it is kept as a documented default so a bare ``WindowedHistogram`` still exposes the
  R4.4 bucket shape.

* **Injected clock drives every boundary.** ``_clock`` is a ``Callable[[], float]`` so a test can
  place samples deterministically on both sides of a window edge (R4.2). :meth:`prune` discards
  samples with ``observed_at < now - _window_s`` BEFORE any quantile is read, so an out-of-window
  sample can never influence p50/p99 (R4.6, Property 4). The nearest-rank quantile is computed over
  exactly the surviving multiset, which is what makes the L14d-1 comparison achievable: the client
  C4 synthetic computes the same nearest-rank over the same window, so the two agree within the
  0.5 ms tolerance by construction (task 5.2 asserts it).

* **Empty window is a DEFINED non-negative value, never stale.**
  :data:`WindowedHistogram.EMPTY_WINDOW` is reported when the window holds no samples, so a
  zero-sample window is a defined, non-negative indication rather than a lifetime leftover, a
  negative, or a zero-by-accident (R4.7, R13.4).

The native Prometheus bucket shape (:meth:`buckets`) is cumulative-monotone over the explicit
finite boundaries plus a single terminal ``+Inf`` overflow bucket -- no infinite boundary other
than that terminal (R4.4). :meth:`window_histogram` hands the in-window samples to the shipped
:func:`~gateway_v2.edge.render.render_histogram` helper as a plain
:class:`~gateway_v2.runtime.holdback_metrics.Histogram`, so the Publisher renders a windowed family
through the exact same code path as every lifetime family -- the renderer stays the one place a
``_bucket``/``_sum``/``_count`` family is produced.

**The windowed-busy gauge is a THIN WRAPPER, not a second instance of this class.**
:class:`WindowedBusyGauge` holds one :class:`WindowedHistogram` and exposes a gauge view of it. The
reuse is deliberate: the autoscaler's windowed-busy signal (R11.3/R11.4) is "the recent value over
a rolling window", which is exactly the pruning + in-window machinery :class:`WindowedHistogram`
already implements. A wrapper (rather than a subclass or a parallel copy) keeps the pruning logic
in ONE place while giving the gauge its own vocabulary -- :meth:`WindowedBusyGauge.observe` accepts
an EXTERNALLY supplied sample (no GPU source is fabricated here; GW20b/GW25 feed real samples
later), and :meth:`WindowedBusyGauge.gauge_value` reports the current rolling value or the defined
empty value. Crucially the gauge is reported through a ``# TYPE gauge`` family EVEN WHEN NO SAMPLE
HAS BEEN SUPPLIED (R11.4): the family is always present with the empty value, never absent and
never a synthesized reading.

No HTTP object, no capacity-position literal, no module-level mutable: the module is in-memory
rolling-window state that ``edge`` (the top layer) builds from a ``runtime`` contract below it.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import ClassVar

from gateway_v2.runtime.holdback_metrics import Histogram

__all__ = (
    "DEFAULT_BOUNDS",
    "WindowedBusyGauge",
    "WindowedHistogram",
)


DEFAULT_BOUNDS: tuple[int, ...] = (0, 5, 10, 25, 50, 100, 250, 500, 1000, 2000)
"""Default millisecond bucket edges for the windowed add-on histogram (R4.4).

A *measurement-domain* boundary set -- the resolution at which per-request add-on latency is
bucketed -- NOT a capacity-position literal (the capacity-literals gate exempts resolution/domain
sets the way it exempts the renderer's caller-supplied ``bounds``). The edges span 0 ms through
2000 ms (>= the R4.4-required 1000 ms), finite and ascending; the single terminal ``+Inf`` overflow
bucket is appended by :meth:`WindowedHistogram.buckets`, so no boundary here is infinite. Callers
may pass their own finite set; this default exists so a bare histogram still exposes the required
shape.
"""

_P50 = 0.50
_P99 = 0.99
_POSITIVE_INF = "+Inf"


def _finite_bounds(bounds: Sequence[int]) -> tuple[int, ...]:
    """Deduplicated, ascending finite boundaries (the explicit non-infinite set).

    Mirrors :func:`gateway_v2.edge.render._finite_bounds` so the bucket sequence this module emits
    is deterministic and monotone regardless of the order a caller supplies the edges in; the
    ``+Inf`` terminal is added by :meth:`WindowedHistogram.buckets`, so this returns finite values
    only. An empty caller set falls back to :data:`DEFAULT_BOUNDS` so a histogram always has at
    least the documented domain shape.
    """
    cleaned = sorted({int(b) for b in bounds})
    return tuple(cleaned) if cleaned else DEFAULT_BOUNDS


@dataclass(slots=True)
class WindowedHistogram:
    """A rolling-window integer-ms histogram whose quantiles reflect ONLY the current window.

    Distinct from the shipped lifetime :class:`~gateway_v2.runtime.holdback_metrics.Histogram`:
    every sample is stamped ``(observed_at, ms)`` with the injected clock, and :meth:`prune`
    discards out-of-window samples before any quantile is read. ``p50``/``p99`` are nearest-rank
    over the in-window multiset; an empty window reports :data:`EMPTY_WINDOW`.

    This is a slotted MUTABLE recorder (the shipped producer idiom -- producers like
    ``AdmissionMetrics`` are mutable slotted recorders; only their ``*Reading`` snapshots are
    frozen). The frozen-dataclasses AST gate scopes ``plan``/``detect``/``resolve`` only, so a
    mutable accumulator in ``edge`` is in-contract.
    """

    EMPTY_WINDOW: ClassVar[float] = 0.0
    """The defined, non-negative value reported when the window holds no samples (R4.7, R13.4).

    A class-level constant (not instance state): a zero-sample window is a defined indication, never
    a stale/lifetime/negative quantile. ``0.0`` is the documented empty signal -- distinguishable in
    context from a real reported quantile because the window is known-empty, and never ``< 0``.
    """

    _window_s: float = 0.0
    _clock: Callable[[], float] = field(default=lambda: 0.0)
    _samples: deque[tuple[float, int]] = field(default_factory=deque)
    _bounds: tuple[int, ...] = DEFAULT_BOUNDS

    @classmethod
    def from_contract(
        cls,
        *,
        window_s: float,
        clock: Callable[[], float],
        bounds: Sequence[int] = DEFAULT_BOUNDS,
    ) -> WindowedHistogram:
        """Build a histogram from a contract-derived window length and an injected clock.

        ``window_s`` is :meth:`ResourceContract.metrics_window_s` (R4.2) -- the single capacity
        authority, never a literal here. ``clock`` drives every window boundary so a test is fully
        deterministic. ``bounds`` default to :data:`DEFAULT_BOUNDS` (the measurement-domain set) and
        are normalised to a finite ascending tuple.
        """
        return cls(
            _window_s=window_s,
            _clock=clock,
            _samples=deque(),
            _bounds=_finite_bounds(bounds),
        )

    def observe(self, ms: int) -> None:
        """Record one C4 add-on latency sample, stamped with the current clock reading (R4.1).

        O(1): appends ``(now, ms)``. Negative inputs are clamped to zero so the add-on latency a
        request contributes can never be negative (a negative add-on would be a clock/measurement
        artefact, not a real latency). The quantile that reads this is computed on READ, in
        :meth:`p50`/:meth:`p99`, never here.
        """
        now = self._clock()
        self._samples.append((now, ms if ms > 0 else 0))

    def prune(self, now: float | None = None) -> None:
        """Discard samples older than ``now - _window_s`` before any quantile is reported (R4.6).

        The deque is time-ordered (samples are appended with a monotone-forward clock), so pruning
        pops the left end until the oldest surviving sample is inside the window. An
        ``observed_at`` strictly less than the cutoff is out of window and can never influence a
        reported quantile (Property 4). ``now`` defaults to the injected clock so callers normally
        just call ``prune()``.
        """
        cutoff = (self._clock() if now is None else now) - self._window_s
        samples = self._samples
        while samples and samples[0][0] < cutoff:
            samples.popleft()

    def _in_window(self, now: float | None = None) -> list[int]:
        """The ms values inside the current window (prunes first). Used by every read path."""
        self.prune(now)
        return [ms for _observed_at, ms in self._samples]

    def _nearest_rank(self, quantile: float, now: float | None = None) -> float:
        """Nearest-rank quantile over the IN-WINDOW samples only; :data:`EMPTY_WINDOW` if none.

        The same nearest-rank rule the shipped lifetime :class:`Histogram` uses (rank = ceil(q*N),
        clamped to [1, N], 1-indexed), computed over exactly the surviving in-window multiset. This
        is what makes the L14d-1 comparison hold: the client C4 synthetic computes the same
        nearest-rank over the same window, so server and client agree within tolerance (task 5.2).
        """
        values = self._in_window(now)
        if not values:
            return self.EMPTY_WINDOW
        ordered = sorted(values)
        rank = math.ceil(quantile * len(ordered))
        index = min(max(rank, 1), len(ordered)) - 1
        return float(ordered[index])

    def p50(self, now: float | None = None) -> float:
        """In-window p50; :data:`EMPTY_WINDOW` when the window is empty (R4.3, R4.7)."""
        return self._nearest_rank(_P50, now)

    def p99(self, now: float | None = None) -> float:
        """In-window p99; :data:`EMPTY_WINDOW` when the window is empty (R4.3, R4.7)."""
        return self._nearest_rank(_P99, now)

    def count(self, now: float | None = None) -> int:
        """How many samples are currently in-window (prunes first)."""
        return len(self._in_window(now))

    def latest(self, now: float | None = None) -> float | None:
        """The most-recent in-window sample value, or ``None`` when the window is empty.

        The "current" rolling value an autoscaler reads. ``None`` -- not a quantile or a zero -- is
        the empty signal, so a caller (the windowed-busy gauge) decides what empty maps to without
        this method inventing a value.
        """
        values = self._in_window(now)
        return float(values[-1]) if values else None

    def bounds(self) -> tuple[int, ...]:
        """The explicit finite boundaries this histogram buckets into (no ``+Inf``)."""
        return self._bounds

    def window_histogram(self, now: float | None = None) -> Histogram:
        """The in-window samples as a plain :class:`Histogram`, for the shipped renderer.

        Hands the current window's samples to the one histogram-rendering code path
        (:func:`gateway_v2.edge.render.render_histogram`) so the Publisher renders a windowed family
        exactly like every lifetime family -- the renderer stays the single place a
        ``_bucket``/``_sum``/``_count`` family is produced. Pruned first, so the returned histogram
        carries only in-window samples (R4.3).
        """
        values = self._in_window(now)
        hist = Histogram()
        for value in values:
            hist.observe(value)
        return hist

    def buckets(self, now: float | None = None) -> list[tuple[str, int]]:
        """Native Prometheus cumulative buckets over the IN-WINDOW samples (R4.4).

        For each finite boundary ``b`` in :meth:`bounds`, emit ``(le="b", count of in-window
        samples <= b)`` -- cumulative, so the sequence is non-decreasing by construction. A
        terminal ``(le="+Inf", total in-window count)`` closes the sequence; it is the ONLY
        infinite boundary. Returned as ``(le_label, cumulative_count)`` pairs so the Publisher can
        render them or compare them against the renderer's output; the empty window yields all-zero
        buckets (every count is 0) with the ``+Inf`` terminal also 0, never an absent family.
        """
        values = self._in_window(now)
        result: list[tuple[str, int]] = []
        for bound in self._bounds:
            cumulative = sum(1 for ms in values if ms <= bound)
            result.append((f'le="{bound}"', cumulative))
        result.append((f'le="{_POSITIVE_INF}"', len(values)))
        return result


@dataclass(slots=True)
class WindowedBusyGauge:
    """A rolling windowed-busy gauge -- a THIN WRAPPER over one :class:`WindowedHistogram`.

    Why a wrapper and not a second :class:`WindowedHistogram` instance or a subclass: the
    autoscaler's windowed-busy signal (R11.3/R11.4) is "the recent value over a rolling window",
    which is precisely the prune-then-read machinery :class:`WindowedHistogram` already owns. A
    wrapper keeps that machinery in ONE place while giving the gauge its own vocabulary
    (``observe``/``gauge_value``) and -- the point of R11.4 -- always reporting a ``# TYPE gauge``
    family, present with the empty value even when nothing has been supplied. A subclass would
    inherit the histogram's quantile vocabulary the gauge does not want; a parallel copy would
    duplicate the pruning. The wrapper is the minimal reuse.

    No GPU source is fabricated here: :meth:`observe` accepts an EXTERNALLY supplied sample, so
    GW20b/GW25 can feed real GPU-busy readings later. Until a sample is supplied the gauge reports
    the defined empty value and nothing is synthesized.
    """

    EMPTY: ClassVar[float] = WindowedHistogram.EMPTY_WINDOW
    """The gauge value reported when no sample is in-window -- the histogram empty value (R11.4)."""

    _window: WindowedHistogram

    @classmethod
    def from_contract(
        cls,
        *,
        window_s: float,
        clock: Callable[[], float],
    ) -> WindowedBusyGauge:
        """Build a windowed-busy gauge over a fresh contract-derived rolling window.

        Shares the exact window derivation (R4.2) and injected clock the add-on histogram uses, so
        the gauge's rolling window is deterministic in tests and derived -- never a literal.
        """
        return cls(
            _window=WindowedHistogram.from_contract(window_s=window_s, clock=clock)
        )

    def observe(self, sample: float) -> None:
        """Record one EXTERNALLY supplied windowed-busy sample (R11.3).

        The sample is a caller-supplied reading (e.g. a future GPU-busy fraction); the gauge
        fabricates none. It is stamped with the injected clock and pruned to the window like any
        other windowed sample. Rounded to the nearest whole unit because the backing store is the
        integer-sample histogram; the gauge reports the current rolling value from it.
        """
        self._window.observe(int(round(sample)))

    def gauge_value(self, now: float | None = None) -> float:
        """The current rolling windowed-busy value, or :data:`EMPTY` when no sample is in-window.

        Reports the most-recent in-window sample (the "current" value an autoscaler reads), or the
        defined empty value when the window holds nothing -- never a stale, synthesized, or
        negative reading (R11.4). Prunes first so an out-of-window sample never leaks in.
        """
        latest = self._window.latest(now)
        return self.EMPTY if latest is None else latest

    def is_empty(self, now: float | None = None) -> bool:
        """True when no sample is in-window; the ``# TYPE gauge`` family still emits (R11.4)."""
        return self._window.count(now) == 0
