"""Structured_Event_Log — a per-error-class rate-limited log emitter (GW14d, task 8.1).

Requirement 9 asks for the pairing an on-call engineer needs: *every* client error class gets
both a **counter** and a **log line**, so a reason code read off ``/metrics`` can be traced to a
concrete event — without a burst of identical events flooding the logs. This module is the log
half; the counter half (``RejectionCounters`` / ``PostureCounters``, task 7.1) is the one-to-one
companion the Publisher wires alongside it. The two halves share the SAME class vocabulary so the
correspondence is exact (R9.1 / R14.1).

### The class vocabulary — Admission_Reason ∪ Posture_Code

A "client error class" is one of the closed, finite codes the rest of the gateway already speaks:
the Admission_Reason vocabulary (``audit.record.SHED`` / ``audit.record.REJECTED``) UNION the
``domain.posture`` codes (the five base ``*_UNAVAILABLE`` / ``OVERLOAD_SHED`` postures plus the ten
GW12 ``STREAM_*`` codes). These are the exact single-spelled strings those modules own — reused,
never re-spelled, so this log can never disagree with the counters or the audit stream about what a
code is called (the C37 join failure). :data:`EVENT_CLASSES` is this union as a frozen tuple.

Task 7.1's ``edge/counters.py`` defines ``REASON_VOCAB`` / ``POSTURE_CODES`` for the counter sets,
and 7.1 runs CONCURRENTLY with this task. Rather than take a load-order dependency on a module that
may not yet be importable, this module derives the SAME vocabulary directly from the two
authorities (``audit.record`` + ``domain.posture``). The Publisher (task 11) injects the SHARED
counter instances into both this log and the counter sets, so there is still exactly one counter per
class fleet-wide — this module keeps its OWN per-class tally only to satisfy the "counter always
increments" invariant in isolation and for the property test; the Publisher reconciles them through
the shared instances.

### The never-raise, never-leak contract

* **One counter per class, always incremented (R9.1 / R9.4 / R14.1).** Every ``record`` call for a
  mapped class increments that class's counter by exactly one, whether or not a line is emitted. The
  counter is the complete event count; the log is a rate-limited SAMPLE of it. So
  ``counter(class) == events(class)`` holds even when lines are suppressed (Property 10).

* **Rate-limited emission (R9.2 / R9.3).** Under the per-class window limit ⇒ emit exactly one
  structured line. At the limit ⇒ suppress the line (but still increment the counter — above). The
  per-class per-window emitted-line volume therefore never exceeds ``contract.event_log_rate()``
  regardless of event rate (the Rate_Limited contract). A sliding window of emission timestamps,
  pruned against an INJECTED clock, bounds the count.

* **No raw content, no secret, ever (R9.6).** The structured line carries ONLY the error-class
  identifier and the Admission_Reason or Posture_Code (R9.5). :meth:`StructuredEventLog.record` does
  not even ACCEPT raw request content as a parameter — its signature is ``(error_class,
  reason_or_posture)`` and nothing else — so a request byte or a secret cannot reach the line by
  construction. This mirrors ``audit.record``'s discipline of never serializing
  ``Finding.evidence``: the safest way to not log a secret is to never hand it to the logger.

* **A coverage gap is surfaced, never dropped (R14.3).** An ``error_class`` outside
  :data:`EVENT_CLASSES` is a seam the operator must see: it increments a dedicated
  ``publisher_event_log_coverage_gap_total`` counter AND emits a distinguished coverage-gap line
  naming the unmapped class, rather than silently discarding the event.

### How a line is emitted — an injected sink

A "structured log line" here is an :class:`EventLogLine` value (frozen, slotted) handed to an
injected ``sink`` callable. The default sink renders the line through a stdlib
:class:`logging.Logger` at ``INFO``; a test injects a capturing callable instead. Choosing an
injected sink (over logging directly) keeps the module pure and deterministically testable — the
property test (task 8.2) captures every line and asserts the bound and the no-leak property without
touching global logging state — while the default keeps the production path a one-liner. The sink
is constructed per instance (no module-level mutable, satisfying the AST gate).

No HTTP object, no capacity literal (the rate and window derive from the injected
:class:`~gateway_v2.runtime.resources.ResourceContract`), no module-level mutable. ``edge`` (the top
layer) may freely import ``audit`` / ``domain`` / ``runtime`` below it.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from gateway_v2.audit import record
from gateway_v2.domain import posture

__all__ = (
    "COVERAGE_GAP_TOTAL",
    "EVENT_CLASSES",
    "POSTURE_CODES",
    "REASON_VOCAB",
    "EventLogLine",
    "EventLogSink",
    "StructuredEventLog",
    "logging_sink",
)

# --------------------------------------------------------------------------- #
# The closed class vocabulary — Admission_Reason ∪ Posture_Code.
# --------------------------------------------------------------------------- #

REASON_VOCAB: tuple[str, ...] = (record.SHED, record.REJECTED)
"""The Admission_Reason classes (``audit.record``). Reused spelling, never re-derived (R6/R9.1)."""

POSTURE_CODES: tuple[str, ...] = (
    posture.KILL_SWITCH_UNAVAILABLE,
    posture.SHARED_STATE_UNAVAILABLE,
    posture.PLAN_UNAVAILABLE,
    posture.BUDGET_UNAVAILABLE,
    posture.OVERLOAD_SHED,
    posture.STREAM_KILLED,
    posture.STREAM_INTER_CHUNK_TIMEOUT,
    posture.STREAM_IDLE_TIMEOUT,
    posture.STREAM_WRITE_TIMEOUT,
    posture.STREAM_MAX_DURATION,
    posture.STREAM_KEY_REVOKED,
    posture.STREAM_PLAN_CHANGED,
    posture.STREAM_SNAPSHOT_STALE,
    posture.STREAM_MALFORMED_UPSTREAM,
    posture.STREAM_BUFFER_UNAVAILABLE,
)
"""The Posture_Code classes (``domain.posture``). Exact single-spelled strings, no alt spelling."""

EVENT_CLASSES: tuple[str, ...] = REASON_VOCAB + POSTURE_CODES
"""Every client error class: the Admission_Reason vocabulary ∪ the Posture_Codes (R9.1 / R14.1).

A frozen tuple (not a module-level mutable). The set of per-class counters is in one-to-one
correspondence with this tuple; an ``error_class`` not in it is a coverage gap (R14.3).
"""

COVERAGE_GAP_TOTAL = "publisher_event_log_coverage_gap_total"
"""The counter name for an ``error_class`` outside :data:`EVENT_CLASSES` (R14.3).

Named so a coverage gap is a VISIBLE, exported ``_total`` series — never a silent drop. The
companion coverage-gap line names the unmapped class too, so an operator sees both rate and example.
"""

_LOGGER_NAME = "gateway_v2.edge.event_log"


# --------------------------------------------------------------------------- #
# The structured line + the sink seam.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class EventLogLine:
    """One structured event-log line. Frozen, slotted, and carrying NO raw content (R9.5 / R9.6).

    The line's whole payload is three safe fields:

    * ``error_class`` — the originating client error-class identifier (R9.5).
    * ``reason_or_posture`` — the Admission_Reason or Posture_Code for that error (R9.5).
    * ``coverage_gap`` — ``True`` only for the distinguished line emitted when ``error_class`` is
      outside :data:`EVENT_CLASSES` (R14.3); ``False`` for every normal line.

    There is deliberately no field that could hold a request byte or a secret. Both string fields
    are members of (or, for a gap line, compared against) the closed :data:`EVENT_CLASSES`
    vocabulary, so the line is safe to retain and export by construction — the R9.6 no-leak property
    is structural, not a redaction applied after the fact.
    """

    error_class: str
    reason_or_posture: str
    coverage_gap: bool = False


EventLogSink = Callable[[EventLogLine], None]
"""Where an emitted :class:`EventLogLine` goes. Injected so a test can capture lines directly."""


def logging_sink(logger: logging.Logger | None = None) -> EventLogSink:
    """The default sink: render an :class:`EventLogLine` through a stdlib logger at ``INFO``.

    Constructed per :class:`StructuredEventLog` (no module-level mutable). The rendered record
    carries only the two closed-vocabulary strings and the gap flag — the same three safe fields —
    so the production log path cannot leak a request byte or a secret any more than the value can.
    """
    log = logger if logger is not None else logging.getLogger(_LOGGER_NAME)

    def _emit(line: EventLogLine) -> None:
        log.info(
            "event_log class=%s code=%s coverage_gap=%s",
            line.error_class,
            line.reason_or_posture,
            line.coverage_gap,
        )

    return _emit


# --------------------------------------------------------------------------- #
# The rate-limited, never-leaking event log.
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class StructuredEventLog:
    """Per-class rate-limited structured event log (R9). A mutable slotted recorder.

    One counter per class, always incremented; a sliding INJECTED-clock window bounds emitted lines
    per class per window at ``_max_per_window``; the line carries only safe closed-vocabulary
    strings; an unmapped class surfaces a coverage gap rather than a silent drop.

    Construct via :meth:`from_contract`, which reads ``event_log_rate()`` and ``metrics_window_s()``
    from the :class:`~gateway_v2.runtime.resources.ResourceContract` (no capacity literal here).
    """

    _max_per_window: int
    _window_s: float
    _clock: Callable[[], float]
    _sink: EventLogSink
    _emitted: dict[str, deque[float]] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)
    _coverage_gap_total: int = 0

    def __post_init__(self) -> None:
        if self._max_per_window < 1:
            raise ValueError("event log rate must be at least one line per class per window")
        if self._window_s <= 0:
            raise ValueError("event log window must be positive")
        # Real zeros from construction: one counter + one window buffer per class, so "no events
        # of this class" (counter present, 0) is distinguishable from "class not instrumented"
        # (absent), matching the real-zeros-never-absence discipline the producers keep (R6.4/R7.5).
        for cls in EVENT_CLASSES:
            self._counters[cls] = 0
            self._emitted[cls] = deque()

    @classmethod
    def from_contract(
        cls,
        contract: object,
        *,
        clock: Callable[[], float],
        sink: EventLogSink | None = None,
        logger: logging.Logger | None = None,
    ) -> StructuredEventLog:
        """Build from a :class:`ResourceContract`: rate + window derive from it, not a literal.

        ``contract`` is typed structurally (``object``; the two methods are called directly) so this
        module takes no import on ``runtime`` beyond what the Publisher already injects.
        ``event_log_rate()`` is the per-class per-window line budget (R9.3) and
        ``metrics_window_s()`` is the window length (shared with the windowed histogram so the
        server view tracks one window). A test may pass ``sink`` to capture lines; prod defaults.
        """
        rate: int = contract.event_log_rate()  # type: ignore[attr-defined]
        window_s: float = contract.metrics_window_s()  # type: ignore[attr-defined]
        return cls(
            _max_per_window=rate,
            _window_s=window_s,
            _clock=clock,
            _sink=sink if sink is not None else logging_sink(logger),
        )

    # -- the one request-path entry point ----------------------------------- #

    def record(self, error_class: str, reason_or_posture: str) -> None:
        """Record one client error of ``error_class`` carrying ``reason_or_posture`` (R9.2–9.6).

        The ONLY two parameters are the class identifier and its reason/posture code — there is no
        parameter through which raw request content or a secret could arrive, so R9.6 (no request
        byte, no secret in any emitted line) holds by construction (the signature IS the guarantee).

        Behaviour:

        * Unmapped class ⇒ a coverage gap (R14.3): increment the coverage-gap counter
          (:data:`COVERAGE_GAP_TOTAL`) and emit the distinguished coverage-gap line naming the
          class. Never a silent drop.
        * Mapped class ⇒ increment the class counter by exactly one ALWAYS (R9.4). Then, if fewer
          than ``_max_per_window`` lines were emitted for this class inside the current window, emit
          exactly one line (R9.2) and record its timestamp; otherwise suppress the line (R9.4). The
          counter increment is independent of the emit/suppress decision, so
          ``counter(class) == events(class)`` regardless of suppression (Property 10).
        """
        if error_class not in self._counters:
            self._record_coverage_gap(error_class, reason_or_posture)
            return

        # The counter is the complete event count and ALWAYS advances — before any rate decision.
        self._counters[error_class] += 1

        now = self._clock()
        window = self._emitted[error_class]
        self._prune(window, now)
        if len(window) >= self._max_per_window:
            return  # at the per-class window limit: suppress the line, counter already incremented.
        window.append(now)
        self._sink(
            EventLogLine(error_class=error_class, reason_or_posture=reason_or_posture),
        )

    # -- read surface (the Publisher renders these as counter series) ------- #

    def counter(self, error_class: str) -> int:
        """The complete event count for ``error_class`` (every ``record`` call, emitted or not).

        A class outside :data:`EVENT_CLASSES` has no counter; its events are tallied in
        :meth:`coverage_gap_total` instead (R14.3).
        """
        return self._counters.get(error_class, 0)

    def counters(self) -> dict[str, int]:
        """A copy of every class counter — real zeros from construction, one entry per class."""
        return dict(self._counters)

    def coverage_gap_total(self) -> int:
        """How many ``record`` calls named a class outside :data:`EVENT_CLASSES` (R14.3)."""
        return self._coverage_gap_total

    # -- internals ---------------------------------------------------------- #

    def _record_coverage_gap(self, error_class: str, reason_or_posture: str) -> None:
        """Surface an unmapped class: count it and emit a distinguished line (never drop it)."""
        self._coverage_gap_total += 1
        self._sink(
            EventLogLine(
                error_class=error_class,
                reason_or_posture=reason_or_posture,
                coverage_gap=True,
            ),
        )

    def _prune(self, window: deque[float], now: float) -> None:
        """Drop emission timestamps older than one window from ``now`` (the sliding bound).

        A timestamp ``t`` is in-window while ``now - t < _window_s``; equal-or-older timestamps fall
        out of the left edge. Pruning before the count means the limit is enforced over exactly the
        trailing window, so a burst in one window cannot borrow against a quieter neighbour (R9.3).
        """
        cutoff = now - self._window_s
        while window and window[0] <= cutoff:
            window.popleft()
