"""Reset-safe rate book-keeping (GW14d, task 6.1 — design §5 ``ResetSafeRate``).

R2-11 measured a server-side view that published a NEGATIVE rate after a counter reset: a worker
restart (or any process that re-created a monotonic ``_total`` counter from zero) made
``current - baseline`` go negative, and the old registry divided it by the interval and published
the impossible value. A rate that can be negative cannot be alarmed on, and a rate that is zero on
the very first observation is a lie — nothing has been measured yet. GW14d's correction is this
one book-keeper: it either returns a NON-NEGATIVE rate or it OMITS the series. It never publishes a
negative, and it never publishes zero to stand in for "no baseline".

The contract (design §5, Property 5):

* **No prior baseline for ``name`` ⇒ adopt ``current`` as the baseline and OMIT** (return ``None``,
  never zero). The next interval has a baseline to difference against (R5.1, R13.3).
* **``current < baseline`` ⇒ reset.** Adopt ``current`` as the NEW baseline and OMIT this interval.
  The immediately following interval with a non-decreasing counter publishes a non-negative rate —
  the reset consumes exactly one interval, not an unbounded run (R5.2, R5.3, R13.2).
* **Otherwise** return ``(current - baseline) / interval_s`` — which is ``>= 0`` because
  ``current >= baseline`` — and advance the baseline to ``current`` (R5.4).

Every value this book-keeper ever emits is therefore ``>= 0`` (R5.4, R13.1); a negative is
structurally impossible because the only branch that computes a quotient has already established
``current >= baseline``.

**The omission rules apply to counter-derived RATE series ONLY.** The shipped
``runtime/state_metrics.py`` and ``state_control/metrics.py`` producers emit *sentinel gauges*
(``ABSENT = -1.0``) whose value — or whose reserved not-yet-observed value — is itself the signal:
``amf_state_stamp_age_seconds = -1.0`` means "no stamp has ever been seen", and that series must
appear from the first scrape so an alarm can fire on it. A sentinel gauge is NOT a counter and is
NOT a rate; routing one through :meth:`ResetSafeRate.rate_or_omit` would be a category error (it
would read ``-1.0`` as a counter that went backwards). The Publisher (task 11) is the component
that decides which series are counter-derived rates; the boundary helper :func:`is_sentinel_gauge`
documents the one value the Publisher must never hand to this book-keeper, and
:data:`SENTINEL_ABSENT` re-exports the shipped constant so the two surfaces cannot drift (R5.5).

Fail-closed on a non-positive ``interval_s``: a zero or negative publication interval is a
programming error in the caller (a mis-wired clock), never request- or tenant-supplied data, so it
raises :class:`ValueError` rather than silently omitting or dividing by zero. Omitting would hide a
broken caller behind the legitimate "no baseline" path; raising surfaces the wiring bug at the one
place it can be fixed. Because ``rate_or_omit`` is only ever called off the serving loop (from the
scrape-path ``exposition()``), this raise can never turn a serving request into an HTTP 500.

No HTTP object, no capacity literal, no module-level mutable — a pure value→value transform over an
instance-local baseline map that ``edge`` (the top layer) owns.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gateway_v2.runtime.state_metrics import ABSENT as SENTINEL_ABSENT

__all__ = (
    "SENTINEL_ABSENT",
    "ResetSafeRate",
    "is_sentinel_gauge",
)


def is_sentinel_gauge(value: float) -> bool:
    """Is ``value`` the reserved ``ABSENT`` sentinel of a not-yet-observed gauge?

    The boundary the Publisher consults so it never routes a sentinel gauge through
    :meth:`ResetSafeRate.rate_or_omit`. A sentinel gauge's reserved value is its signal (R5.5): it
    is emitted unchanged, never rate-processed and never omitted. This is a documentation-grade
    guard — the authority on which series are counter-derived rates is the Publisher's explicit
    per-series wiring, not a value test — but it names the one value that must never reach the rate
    book-keeper, and it is pinned to the shipped :data:`SENTINEL_ABSENT` so the two surfaces agree.
    """
    return value == SENTINEL_ABSENT


@dataclass(slots=True)
class ResetSafeRate:
    """Per-counter baseline book-keeping that emits a non-negative rate or omits.

    A mutable, slotted accumulator — matching the shipped producer idiom (``AdmissionMetrics`` and
    friends are mutable slotted recorders, their ``*Reading`` snapshots are the frozen values). The
    only state is ``_baselines``: counter name → the last value adopted as the baseline. It is
    instance-local (never a module-level mutable) and keyed by the Publisher's stable series name,
    so two counters never share a baseline.
    """

    _baselines: dict[str, int] = field(default_factory=dict)

    def rate_or_omit(self, name: str, current: int, interval_s: float) -> float | None:
        """Return a non-negative rate for ``name`` over ``interval_s``, or ``None`` to omit.

        Args:
            name: The counter's stable series name — the baseline key. Distinct names keep
                distinct baselines.
            current: The counter's current (cumulative, monotonic-in-the-absence-of-reset) value.
            interval_s: The publication interval in seconds. MUST be strictly positive; a
                non-positive interval is a caller wiring error and raises :class:`ValueError`.

        Returns:
            ``None`` to OMIT the series when there is no prior baseline (first observation) or when
            the counter went backwards (a reset). Otherwise ``(current - baseline) / interval_s``,
            which is guaranteed ``>= 0`` because the baseline is only differenced against a
            ``current`` that is ``>=`` it.

        Raises:
            ValueError: If ``interval_s`` is not strictly positive (fail-closed on a mis-wired
                clock; this is a programming error, not request data).
        """
        if interval_s <= 0.0:
            raise ValueError(
                f"interval_s must be strictly positive, got {interval_s!r} for rate {name!r}",
            )

        baseline = self._baselines.get(name)

        # R5.1 / R13.3 — no prior baseline: adopt current and OMIT (never zero). The next interval
        # has something to difference against; this one has measured nothing yet.
        if baseline is None:
            self._baselines[name] = current
            return None

        # R5.2 / R5.3 / R13.2 — counter went backwards: a reset (restart, or a re-created counter).
        # Adopt current as the new baseline and OMIT this interval only. The next non-decreasing
        # interval differences against this new baseline and publishes a non-negative rate.
        if current < baseline:
            self._baselines[name] = current
            return None

        # R5.4 — the normal path: current >= baseline, so the quotient is >= 0. Advance the
        # baseline so the next interval measures the delta since this one.
        rate = (current - baseline) / interval_s
        self._baselines[name] = current
        return rate
