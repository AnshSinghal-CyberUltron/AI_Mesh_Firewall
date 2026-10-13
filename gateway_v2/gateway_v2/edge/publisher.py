"""The Publisher — merge, type, and expose every gateway metric (GW14d, task 11.1).

This module is the KEYSTONE of GW14d. The raw-ASGI ``/metrics`` endpoint in
:mod:`gateway_v2.edge.app` already consumes a :class:`~gateway_v2.edge.app.MetricsRegistry` —
anything with ``exposition() -> str`` — but nothing in the package provides one yet. The
:class:`Publisher` IS that implementation. It reads the six already-shipped, label-free metric
PRODUCERS, merges their two reading shapes behind one explicit adapter each, types every family
with a single ``# TYPE`` line through the shipped :mod:`gateway_v2.edge.render` renderer, and owns
the three mechanisms that belong to no single producer: the bounded per-tenant
:class:`~gateway_v2.edge.cardinality.CardinalityDirectory` (R2-10 core), the
:class:`~gateway_v2.edge.windowed.WindowedHistogram` add-on + windowed-busy gauge (R2-11), and the
reason/posture counter families (R2-11 M2). The producers are NOT rebuilt — they are read as-is.

The two hazardous paths R2-10 / R2-11 measured are kept strictly apart:

* **The scrape path (:meth:`Publisher.exposition`) never raises (R1.3).** It is a six-step merge
  with ONE explicit, guarded adapter per producer. A producer that raises mid-read is OMITTED from
  the body and :data:`PRODUCER_READ_FAILURES_SERIES` is incremented, so a silently-dropped producer
  is itself observable — never a silent gap — while every other producer's families are returned
  and the body is complete. ``exposition()`` is a plain, CPU-only, synchronous method:
  ``edge/app.py`` already offloads it via ``asyncio.to_thread`` (R1.8), so it does no I/O and holds
  no lock.

* **The request path (``observe_*``) never raises (R2-10).** Every observation entry point —
  :meth:`observe_tenant`, :meth:`observe_addon_latency_ms`, :meth:`observe_rejection`,
  :meth:`observe_posture`, :meth:`observe_windowed_busy` — wraps its whole body so that ANY internal
  failure is swallowed and control returns to the serving request. A metric operation can never turn
  a successful request into an HTTP 500 (R2.1/R2.2). Tenant observation routes an over-capacity or
  internally-failed observation to the directory's Overflow_Counter (the directory already
  guarantees this never raises); the others swallow and return, because their failure modes are
  bounded-vocabulary counter bumps that cannot grow unbounded.

The exposed series count is a FIXED EXPRESSION (:data:`Publisher.SERIES_COUNT`), the sum of the six
producers' ``SERIES_COUNT`` values plus the bounded tenant set (``K + 1`` + the Overflow_Counter)
plus the reason / posture / windowed-busy families + the Publisher's own indicators (R15.1).
``exposition()`` asserts the rendered count equals it (R15.2); the count is invariant to the
observed tenant count because ``K + 1`` is fixed (R15.3, Property 2). The expression and its
per-term reconciliation are documented on :data:`Publisher.SERIES_COUNT`.

``edge`` is the top layer, so the Publisher may import ``admit`` / ``audit`` / ``runtime`` (all
below it) AND ``state_control`` (OUTSIDE ``root_packages`` — a deployment-wiring crossing the design
§Architecture justifies, exactly as ``edge/app.py`` already assembles the gateway from parts the
layer contract does not model). No HTTP object lives here (the renderer owns text); no
capacity-position literal lives here (``K`` and the directory capacity come from the contract via
the directory; the window from the contract via the histogram; the bucket bounds from
:data:`~gateway_v2.edge.windowed.DEFAULT_BOUNDS`); no module-level mutable (all state is instance
state).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from gateway_v2.admit.metrics import AdmissionMetrics, QuotaMetrics
from gateway_v2.audit import metrics as audit_metrics
from gateway_v2.edge import render
from gateway_v2.edge.cardinality import CardinalityDirectory
from gateway_v2.edge.counters import PostureCounters, RejectionCounters
from gateway_v2.edge.event_log import StructuredEventLog
from gateway_v2.edge.rates import ResetSafeRate
from gateway_v2.edge.render import Family
from gateway_v2.edge.series_count import HISTOGRAM_SERIES as _HISTOGRAM_SERIES
from gateway_v2.edge.series_count import OTHER_SERIES as _OTHER_SERIES
from gateway_v2.edge.series_count import OVERFLOW_SERIES as _OVERFLOW_SERIES
from gateway_v2.edge.series_count import READ_FAILURES_SERIES as _READ_FAILURES_SERIES
from gateway_v2.edge.series_count import WINDOWED_BUSY_SERIES_COUNT as _WINDOWED_BUSY_SERIES_COUNT
from gateway_v2.edge.series_count import series_count as _series_count
from gateway_v2.edge.windowed import DEFAULT_BOUNDS, WindowedBusyGauge, WindowedHistogram
from gateway_v2.runtime.holdback_metrics import HoldbackMetrics
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.state_metrics import StateMetrics
from gateway_v2.runtime.stream_metrics import PerRequestExports
from state_control.metrics import RehydrationMetrics

__all__ = (
    "ADDON_HISTOGRAM_BASE",
    "PRODUCER_READ_FAILURES_SERIES",
    "TENANT_OTHER_SERIES",
    "TENANT_PREFIX",
    "WINDOWED_BUSY_SERIES",
    "Publisher",
)

# --------------------------------------------------------------------------- #
# Fixed series names the Publisher itself owns (not a producer's). Protocol
# identifiers, not capacity literals.
# --------------------------------------------------------------------------- #

PRODUCER_READ_FAILURES_SERIES = "amf_publisher_producer_read_failures_total"
"""Incremented once per producer whose adapter raised during :meth:`Publisher.exposition` (R1.3).

A silently-omitted producer is itself OBSERVABLE through this counter — never a silent gap. A real
zero from construction, present in every scrape, so "no producer failed" (value 0) is distinct from
"indicator absent".
"""

TENANT_PREFIX = "amf_tenant_magnitude"
"""Base name for the bounded per-tenant Top-K individual series (R3.1).

Each retained tenant is one ``amf_tenant_magnitude{tenant="<key>"}`` series. The ``tenant`` label
here is the ONE place per-tenant visibility is restored, and it is bounded at ``K`` individual
series by the directory — the renderer de-dups by this single base so all ``K`` share one
``# TYPE`` line (R3.2/R3.8).
"""

TENANT_OTHER_SERIES = "amf_tenant_magnitude_other"
"""The single ``other`` bucket collapsing every non-Top-K tenant (R3.5), a real zero from the first
exposition (R3.7). A distinct base from :data:`TENANT_PREFIX` so it is its own family."""

TENANT_OVERFLOW_SERIES = "amf_tenant_magnitude_overflow_total"
"""The Overflow_Counter the directory exports (R2.5) — a process-local ``_total``, real zero before
the first overflow, present in every scrape."""

ADDON_HISTOGRAM_BASE = "amf_addon_latency_ms"
"""Base name for the windowed C4 add-on latency histogram family (R4.1).

Rendered through the one histogram code path (:func:`gateway_v2.edge.render.render_histogram`) over
the in-window samples, so a fresh Publisher's first exposition carries ``_count = 0`` (Property 7).
"""

WINDOWED_BUSY_SERIES = "amf_windowed_busy"
"""The windowed-busy gauge family for the autoscaler (R11.3/R11.4).

Present with a ``# TYPE gauge`` line and the defined empty value even when no sample has been
supplied — no GPU source fabricated, no synthesized reading. A non-``_total`` name, so the renderer
types it ``gauge`` (R11.2).
"""

def _tenant_series_name(tenant_key: str) -> str:
    """``amf_tenant_magnitude{tenant="<key>"}`` — the ONE bounded per-tenant label (R3.1).

    The ``tenant`` label is the only tenant-derived value anywhere in the exposition, and it is
    bounded to the Top-K set by the directory, so the family carries at most ``K`` series under one
    base name (R3.2/R3.8).
    """
    return f'{TENANT_PREFIX}{{tenant="{tenant_key}"}}'


class Publisher:
    """Merge the six producers + the three mechanisms into one typed Prometheus exposition.

    Implements the :class:`gateway_v2.edge.app.MetricsRegistry` protocol (``exposition() -> str``).
    A slotted class holding INJECTED references to the six producers, the
    :class:`CardinalityDirectory`, the :class:`WindowedHistogram` (the C4 add-on), the
    :class:`WindowedBusyGauge`, the :class:`ResetSafeRate` book-keeper, the
    :class:`RejectionCounters`, the :class:`PostureCounters`, the :class:`StructuredEventLog`, the
    :class:`ResourceContract`, and the clock. The renderer is used as a module, not stored.

    Construct via :meth:`from_contract` in production (it builds the mechanisms from the contract +
    clock); the direct constructor is kept for testability so a test can inject scripted producers
    and mechanisms.
    """

    __slots__ = (
        "_admission",
        "_quota",
        "_audit",
        "_rehydration",
        "_state",
        "_stream",
        "_holdback",
        "_directory",
        "_addon",
        "_busy",
        "_rates",
        "_rejections",
        "_postures",
        "_event_log",
        "_contract",
        "_clock",
        "SERIES_COUNT",
        "_read_failures",
    )

    def __init__(
        self,
        *,
        admission: AdmissionMetrics,
        quota: QuotaMetrics,
        audit: audit_metrics.AuditMetrics,
        rehydration: RehydrationMetrics,
        state: StateMetrics,
        stream: PerRequestExports,
        holdback: HoldbackMetrics,
        directory: CardinalityDirectory,
        addon: WindowedHistogram,
        busy: WindowedBusyGauge,
        rates: ResetSafeRate,
        rejections: RejectionCounters,
        postures: PostureCounters,
        event_log: StructuredEventLog,
        contract: ResourceContract,
        clock: Callable[[], float],
    ) -> None:
        self._admission = admission
        self._quota = quota
        self._audit = audit
        self._rehydration = rehydration
        self._state = state
        self._stream = stream
        self._holdback = holdback
        self._directory = directory
        self._addon = addon
        self._busy = busy
        self._rates = rates
        self._rejections = rejections
        self._postures = postures
        self._event_log = event_log
        self._contract = contract
        self._clock = clock
        #: The fixed, by-construction series count for THIS Publisher (R15.1/R15.2). ``K`` derives
        #: from the injected contract, so this is computed once at construction; the request path
        #: never recomputes it and the scrape-path assertion reads a constant. Invariant to the
        #: observed tenant count because the tenant term is the fixed ``K + 1`` (R15.3, Property 2).
        #: The term-by-term reconciliation is documented on :func:`_series_count`.
        self.SERIES_COUNT: int = _series_count(contract)
        # The Publisher's own read-failure indicator (R1.3), a real zero from construction.
        self._read_failures: int = 0

    @classmethod
    def from_contract(
        cls,
        *,
        admission: AdmissionMetrics,
        quota: QuotaMetrics,
        audit: audit_metrics.AuditMetrics,
        rehydration: RehydrationMetrics,
        state: StateMetrics,
        stream: PerRequestExports,
        holdback: HoldbackMetrics,
        event_log: StructuredEventLog,
        contract: ResourceContract,
        clock: Callable[[], float],
    ) -> Publisher:
        """Build a Publisher whose three mechanisms derive from ``contract`` + ``clock`` (the
        production path).

        The directory capacity + ``K`` come from ``contract.metrics_directory_slots()`` /
        ``metrics_top_k()``; the add-on + windowed-busy windows from
        ``contract.metrics_window_s()``; all driven by the injected ``clock`` so the server view
        tracks one window and a test is deterministic. The reset-safe book-keeper, rejection and
        posture counters start empty with real zeros. The six producers and the shared
        :class:`StructuredEventLog` are injected (the
        event log is built from the contract by its own :meth:`from_contract`).
        """
        window_s = contract.metrics_window_s()
        return cls(
            admission=admission,
            quota=quota,
            audit=audit,
            rehydration=rehydration,
            state=state,
            stream=stream,
            holdback=holdback,
            directory=CardinalityDirectory.from_contract(contract),
            addon=WindowedHistogram.from_contract(window_s=window_s, clock=clock),
            busy=WindowedBusyGauge.from_contract(window_s=window_s, clock=clock),
            rates=ResetSafeRate(),
            rejections=RejectionCounters(),
            postures=PostureCounters(),
            event_log=event_log,
            contract=contract,
            clock=clock,
        )

    # ----------------------------------------------------------------------- #
    # exposition (scrape path, off the serving loop) — R1.1, the MetricsRegistry method.
    # ----------------------------------------------------------------------- #

    def exposition(self) -> str:
        """Return the full typed Prometheus text body (R1.1). NEVER raises (R1.3).

        A six-step merge, one explicit guarded adapter per producer:

        1. flat producers (``audit`` / ``state_control`` / ``state_metrics``) via ``series()``;
        2. Reading producers (``admit`` / ``stream`` / ``holdback``) via ``snapshot()`` + the
           histogram helper;
        3. reset-safe rates (currently none are derived — the hook is wired, see below);
        4. the bounded tenant families (Top-K + ``other`` + Overflow_Counter);
        5. the reason / posture / windowed-busy families as real zeros;
        6. assert the rendered series count equals :attr:`_series_count` (R15.2).

        Each adapter runs inside :meth:`_guarded`, so a producer that raises is OMITTED and
        :data:`PRODUCER_READ_FAILURES_SERIES` is incremented; the remaining families are still
        rendered and the body is complete (R1.3). The method does no I/O and is CPU-only — the
        ``asyncio.to_thread`` offload lives in ``edge/app.py`` (R1.8).
        """
        families: list[Family] = []
        rendered = 0

        # (1) flat producers — series() -> dict[str, float]. Each adapter returns its families and
        # the number of SERIES it rendered (labelled series within one family each count toward the
        # producer's SERIES_COUNT).
        for adapter in (self._audit_families, self._rehydration_families, self._state_families):
            fams, count = self._guarded_flat(adapter)
            families.extend(fams)
            rendered += count

        # (2) Reading producers — snapshot() -> Reading. admit/stream/holdback carry Histogram
        # objects; quota is three scalars but still a snapshot() producer, so it is adapted here.
        for adapter in (
            self._admit_families,
            self._quota_families,
            self._stream_families,
            self._holdback_families,
        ):
            fams, count = self._guarded_flat(adapter)
            families.extend(fams)
            rendered += count

        # (3) reset-safe rates: the book-keeper is wired for the Publisher to derive counter rates
        # from; no rate series is currently published (the producers expose cumulative counters that
        # Prometheus rates downstream), so this step contributes zero families. Keeping the step
        # explicit documents where a derived rate would plug in and be reset-safe (R5).

        # (4) bounded tenant families (Top-K + other + Overflow_Counter). These never raise (the
        # directory's reads are O(capacity), a bounded constant), so they are outside the producer
        # guard but inside exposition's own never-raise contract.
        tenant_fams, tenant_count = self._tenant_families()
        families.extend(tenant_fams)
        rendered += tenant_count

        # (5) reason / posture / windowed-busy families — real zeros from the first exposition.
        fixed_fams, fixed_count = self._fixed_families()
        families.extend(fixed_fams)
        rendered += fixed_count

        # The Publisher's own read-failure indicator — rendered last so a failure recorded by an
        # adapter above is reflected in THIS body.
        families.append(
            Family(
                base=PRODUCER_READ_FAILURES_SERIES,
                type=render.metric_type(PRODUCER_READ_FAILURES_SERIES),
                lines=(f"{PRODUCER_READ_FAILURES_SERIES} {_format(self._read_failures)}",),
            ),
        )
        rendered += _READ_FAILURES_SERIES

        # (6) count assertion (R15.2). The rendered series count equals the fixed expression ONLY
        # when every producer read succeeded; a failed producer legitimately omits its term, so the
        # assertion is relaxed by the omitted producers' contribution — recorded in _read_failures.
        if self._read_failures == 0:
            assert rendered == self.SERIES_COUNT, (
                f"rendered series {rendered} != fixed SERIES_COUNT {self.SERIES_COUNT}"
            )
        return render.render_families(families)

    # ----------------------------------------------------------------------- #
    # Guarded adapter wrappers (R1.3 — a raising producer is omitted + counted).
    # ----------------------------------------------------------------------- #

    def _guarded_flat(
        self, adapter: Callable[[], tuple[list[Family], int]]
    ) -> tuple[list[Family], int]:
        """Run one producer adapter; on ANY failure omit it and count the failure (R1.3).

        Returns the adapter's families + series count on success, or ``([], 0)`` plus a
        :data:`PRODUCER_READ_FAILURES_SERIES` increment on failure. ``exposition()`` therefore
        returns a complete body with the failing producer's families absent and the failure visible.
        """
        try:
            return adapter()
        except Exception:  # noqa: BLE001 — the never-raise contract: omit + count, never propagate.
            self._read_failures += 1
            return [], 0

    # ----------------------------------------------------------------------- #
    # Flat-producer adapters (series() -> dict[str, float]).
    # ----------------------------------------------------------------------- #

    def _audit_families(self) -> tuple[list[Family], int]:
        """The audit/store producer — all scalar families (R1.2). SERIES_COUNT = 29."""
        return self._flat_scalar_families(self._audit.series())

    def _rehydration_families(self) -> tuple[list[Family], int]:
        """The re-hydrator producer — all scalar families, read across the ``root_packages``
        boundary (the one deployment-wiring crossing, §Architecture). SERIES_COUNT as the module."""
        return self._flat_scalar_families(self._rehydration.series())

    def _state_families(self) -> tuple[list[Family], int]:
        """The state-propagation producer — all scalar families, incl. the ``ABSENT = -1.0``
        sentinel gauges which pass through unchanged (they are not rates — R5.5)."""
        return self._flat_scalar_families(self._state.series())

    def _flat_scalar_families(self, series: Mapping[str, float]) -> tuple[list[Family], int]:
        """Group a flat ``series()`` dict into typed scalar families, de-dup-safe (R1.4/1.5/1.7).

        A flat producer bakes any label into the key string
        (``amf_state_rounds_total{kind="plan"}``). Series sharing a base name (the key with its
        ``{...}`` label block stripped) are grouped into ONE :class:`Family` so the renderer emits a
        single ``# TYPE`` line for the base, typed by the suffix rule on the BASE (so a labelled
        ``_total`` is still a counter). The returned series count is the number of ``(name, value)``
        pairs — matching the producer's ``SERIES_COUNT``,
        which counts each labelled combination as one series.
        """
        by_base: dict[str, list[str]] = {}
        for name in sorted(series):
            base = _base_name(name)
            by_base.setdefault(base, []).append(f"{name} {_format(series[name])}")
        families = [
            Family(base=base, type=render.metric_type(base), lines=tuple(lines))
            for base, lines in by_base.items()
        ]
        return families, len(series)

    # ----------------------------------------------------------------------- #
    # Reading-producer adapters (snapshot() -> Reading with Histogram objects).
    # ----------------------------------------------------------------------- #

    def _admit_families(self) -> tuple[list[Family], int]:
        """The admission producer — per-queue scalars + the one oldest-age histogram (R1.6).

        SERIES_COUNT = ``2·len(QUEUE_NAMES) + 3 + 1 + len(ShedReason) + 1`` = 20: the histogram term
        is the ``+ 3``, every other term a scalar family.
        """
        reading = self._admission.snapshot()
        families: list[Family] = []
        scalars: dict[str, float] = {}
        from gateway_v2.admit.metrics import PREFIX as ADMIT_PREFIX
        from gateway_v2.admit.metrics import QUEUE_NAMES

        for name in QUEUE_NAMES:
            scalars[f'{ADMIT_PREFIX}_queue_depth{{queue="{name}"}}'] = float(
                reading.queues.depth(name)
            )
            scalars[f'{ADMIT_PREFIX}_queue_oldest_age_seconds{{queue="{name}"}}'] = (
                reading.queues.oldest_age_s(name)
            )
        scalars[f"{ADMIT_PREFIX}_admitted_total"] = float(reading.admitted_total)
        for reason, count in reading.shed_total.items():
            scalars[f'{ADMIT_PREFIX}_shed_total{{reason="{reason}"}}'] = float(count)
        scalars[f"{ADMIT_PREFIX}_fail_open_total"] = float(reading.fail_open_total)
        scalar_families, scalar_count = self._flat_scalar_families(scalars)
        families.extend(scalar_families)
        families.append(
            render.render_histogram(
                f"{ADMIT_PREFIX}_oldest_age_ms", reading.oldest_age_s_hist, DEFAULT_BOUNDS
            )
        )
        return families, scalar_count + _HISTOGRAM_SERIES

    def _quota_families(self) -> tuple[list[Family], int]:
        """The budget-lease quota producer — three label-free scalars (R1.2). SERIES_COUNT = 3.

        ``snapshot()`` returns a frozen :class:`~gateway_v2.admit.metrics.QuotaReading`; its three
        scalars are the lease overshoot gauge and the two ``_total`` counters, each a scalar family.
        """
        reading = self._quota.snapshot()
        from gateway_v2.admit.metrics import QUOTA_PREFIX

        scalars: dict[str, float] = {
            f"{QUOTA_PREFIX}_lease_overshoot": float(reading.lease_overshoot),
            f"{QUOTA_PREFIX}_async_refill_total": float(reading.async_refill_total),
            f"{QUOTA_PREFIX}_budget_unavailable_total": float(reading.budget_unavailable_total),
        }
        return self._flat_scalar_families(scalars)

    def _stream_families(self) -> tuple[list[Family], int]:
        """The per-request stream producer — five scalars + two histograms (R1.6).

        SERIES_COUNT = ``5 + 2·3`` = 11.
        """
        reading = self._stream.snapshot()
        from gateway_v2.runtime.stream_metrics import PREFIX as STREAM_PREFIX

        scalars: dict[str, float] = {
            f"{STREAM_PREFIX}_detector_invocations": float(reading.detector_invocations),
            f"{STREAM_PREFIX}_buffer_high_water": float(reading.buffer_high_water),
            f"{STREAM_PREFIX}_active_memory_bound": float(reading.active_stream_memory_bound),
            f"{STREAM_PREFIX}_withheld_total": float(reading.withheld_total),
            f"{STREAM_PREFIX}_fail_open_total": float(reading.fail_open_total),
        }
        families, scalar_count = self._flat_scalar_families(scalars)
        families.append(
            render.render_histogram(
                f"{STREAM_PREFIX}_release_lag_ms", reading.release_lag_ms, DEFAULT_BOUNDS
            )
        )
        families.append(
            render.render_histogram(
                f"{STREAM_PREFIX}_loop_lag_ms", reading.loop_lag_ms, DEFAULT_BOUNDS
            )
        )
        return families, scalar_count + 2 * _HISTOGRAM_SERIES

    def _holdback_families(self) -> tuple[list[Family], int]:
        """The holdback producer — four scalars + three histograms, incl. held-tokens (R8).

        SERIES_COUNT = ``4 + 3·3`` = 13. The held-tokens histogram is exposed with native
        ``_bucket``/``_sum``/``_count`` + a terminal ``+Inf`` bucket (R8.1/8.2) and no tenant label
        (R8.3), through the one histogram code path.
        """
        reading = self._holdback.snapshot()
        from gateway_v2.runtime.holdback_metrics import PREFIX as HOLDBACK_PREFIX

        scalars: dict[str, float] = {
            f"{HOLDBACK_PREFIX}_unbroken_run_bytes_max": float(reading.unbroken_run_bytes_max),
            f"{HOLDBACK_PREFIX}_incomplete_streams_total": float(reading.incomplete_streams_total),
            f"{HOLDBACK_PREFIX}_overflow_total": float(reading.overflow_total),
            f"{HOLDBACK_PREFIX}_forced_release_total": float(reading.forced_release_total),
        }
        families, scalar_count = self._flat_scalar_families(scalars)
        families.append(
            render.render_histogram(
                f"{HOLDBACK_PREFIX}_held_tokens", reading.held_tokens_hist, DEFAULT_BOUNDS
            )
        )
        families.append(
            render.render_histogram(
                f"{HOLDBACK_PREFIX}_release_processing_ms",
                reading.release_processing_ms,
                DEFAULT_BOUNDS,
            )
        )
        families.append(
            render.render_histogram(
                f"{HOLDBACK_PREFIX}_holdback_wait_ms", reading.holdback_wait_ms, DEFAULT_BOUNDS
            )
        )
        return families, scalar_count + 3 * _HISTOGRAM_SERIES

    # ----------------------------------------------------------------------- #
    # Mechanism families (tenant Top-K + reason/posture/windowed-busy).
    # ----------------------------------------------------------------------- #

    def _tenant_families(self) -> tuple[list[Family], int]:
        """The bounded Top-K individual series + ``other`` + the Overflow_Counter (R3, R2.5).

        One ``amf_tenant_magnitude{tenant}`` series per retained tenant (at most ``K``, under one
        base + ``# TYPE`` line), the single ``other`` bucket (real zero when nothing overflowed —
        R3.7), and the Overflow_Counter ``_total`` (real zero before the first overflow — R2.5).

        The rendered SERIES count is a FIXED ``K + 2`` regardless of how many tenants are observed:
        when fewer than ``K`` tenants exist the Top-K family is PADDED to exactly ``K`` series with
        explicit zero-magnitude placeholders, so the exposition family count is invariant to the
        tenant count (R15.3, Property 2) and the tenant-scale AST gate holds (no work proportional
        to the estate: the directory's reads are bounded by its capacity).
        """
        top_k = self._directory.top_k_series()
        k = self._contract.metrics_top_k()
        lines: list[str] = [
            f"{_tenant_series_name(key)} {_format(mag)}" for key, mag in top_k
        ]
        # Pad to exactly K series so the rendered count is invariant to the observed tenant count.
        # The placeholders use a reserved, non-tenant slot name so they never collide with a real
        # tenant key and are self-evidently synthetic.
        for i in range(len(top_k), k):
            lines.append(f'{TENANT_PREFIX}{{tenant="__slot_{i}__"}} {_format(0)}')
        tenant_family = Family(
            base=TENANT_PREFIX, type=render.metric_type(TENANT_PREFIX), lines=tuple(lines)
        )
        other_family = Family(
            base=TENANT_OTHER_SERIES,
            type=render.metric_type(TENANT_OTHER_SERIES),
            lines=(f"{TENANT_OTHER_SERIES} {_format(self._directory.other_value())}",),
        )
        overflow_family = Family(
            base=TENANT_OVERFLOW_SERIES,
            type=render.metric_type(TENANT_OVERFLOW_SERIES),
            lines=(f"{TENANT_OVERFLOW_SERIES} {_format(self._directory.overflow_total())}",),
        )
        return [tenant_family, other_family, overflow_family], k + _OTHER_SERIES + _OVERFLOW_SERIES

    def _fixed_families(self) -> tuple[list[Family], int]:
        """The reason / posture / add-on / windowed-busy families — real zeros from first scrape.

        * ``rejected{status,reason}`` one series per vocabulary reason + the vocab-error indicator
          (R6.4/6.5), grouped under the ``amf_rejected`` base so distinct reasons never merge.
        * one posture-refusal counter per :data:`POSTURE_CODES` member + the coverage-gap indicator
          (R7.5/14.3), each a distinct ``_total`` base from any Policy_BLOCK series.
        * the windowed C4 add-on histogram over the in-window samples (R4) — ``_count = 0`` on a
          fresh Publisher (Property 7).
        * the windowed-busy gauge, present with ``# TYPE gauge`` and the empty value even when no
          sample has been supplied (R11.3/11.4).
        """
        families: list[Family] = []
        count = 0

        # rejected{status,reason} + vocab-error indicator. The rejection series share one base.
        rejected = self._rejections.series()
        rej_families, rej_count = self._flat_scalar_families(rejected)
        families.extend(rej_families)
        count += rej_count

        # posture-refusal counters + coverage-gap indicator.
        postures = self._postures.series()
        post_families, post_count = self._flat_scalar_families(postures)
        families.extend(post_families)
        count += post_count

        # the windowed C4 add-on histogram (R4), rendered over the in-window samples.
        families.append(
            render.render_histogram(
                ADDON_HISTOGRAM_BASE, self._addon.window_histogram(), self._addon.bounds()
            )
        )
        count += _HISTOGRAM_SERIES

        # the windowed-busy gauge — present with the empty value even with no sample (R11.4).
        families.append(
            Family(
                base=WINDOWED_BUSY_SERIES,
                type=render.metric_type(WINDOWED_BUSY_SERIES),
                lines=(f"{WINDOWED_BUSY_SERIES} {_format(self._busy.gauge_value())}",),
            )
        )
        count += _WINDOWED_BUSY_SERIES_COUNT

        return families, count

    # ----------------------------------------------------------------------- #
    # Request path (serving path) — EVERY method NEVER raises (R2-10).
    # ----------------------------------------------------------------------- #

    def observe_tenant(self, tenant_key: str, magnitude: int) -> None:
        """Register or update a per-tenant magnitude. NEVER raises (R2.1/R2.2, R3, R12).

        Delegates to the directory, whose ``observe`` already routes any over-capacity or
        internally-failed observation to the Overflow_Counter without raising. The extra guard here
        is belt-and-braces: should the directory call itself fail for any reason, the serving
        request still returns.
        """
        try:
            self._directory.observe(tenant_key, magnitude)
        except Exception:  # noqa: BLE001 — never turn a serving request into a 500 (R2.2).
            return

    def observe_addon_latency_ms(self, ms: int) -> None:
        """Record one C4 add-on latency sample into the windowed histogram (R4.1). NEVER raises."""
        try:
            self._addon.observe(ms)
        except Exception:  # noqa: BLE001 — request-path never-raise contract (R2-10).
            return

    def observe_rejection(self, status: int, reason: str) -> None:
        """Record one admission rejection in the ``rejected{status,reason}`` family (R6). NEVER
        raises.

        An out-of-vocabulary ``reason`` is handled by :class:`RejectionCounters` itself (it bumps
        the vocab-error indicator and never merges into a default series — R6.5); the companion
        structured-event-log line records the class for traceability (R9). Both are best-effort: any
        internal failure is swallowed so the serving request returns.
        """
        try:
            self._rejections.observe(status, reason)
            self._event_log.record(reason, reason)
        except Exception:  # noqa: BLE001 — request-path never-raise contract (R2-10).
            return

    def observe_posture(self, code: str) -> None:
        """Record one fail-closed posture refusal under its own counter (R7). NEVER raises.

        Increments only the code's own posture counter (never any Policy_BLOCK series — R7.3/7.6);
        an unknown code bumps the coverage-gap indicator (R7.1/14.3). The companion event-log line
        records the class. Any internal failure is swallowed so the serving request returns.
        """
        try:
            self._postures.observe(code)
            self._event_log.record(code, code)
        except Exception:  # noqa: BLE001 — request-path never-raise contract (R2-10).
            return

    def observe_windowed_busy(self, sample: float) -> None:
        """Record one externally supplied windowed-busy sample (R11.3). NEVER raises.

        No GPU source is fabricated — the sample is caller-supplied (a future GPU-busy reading). Any
        internal failure is swallowed so the serving request returns.
        """
        try:
            self._busy.observe(sample)
        except Exception:  # noqa: BLE001 — request-path never-raise contract (R2-10).
            return

    # ----------------------------------------------------------------------- #
    # Readiness.
    # ----------------------------------------------------------------------- #

    def directory_available(self) -> tuple[bool, str | None]:
        """The Cardinality_Directory availability for the ``/readyz`` metrics gate (R10.1).

        Returns ``(True, None)`` when the directory is initialized/available, else
        ``(False, "metrics_directory")`` naming the metrics directory as the unavailable dependency
        — the shape ``edge/app.py``'s readiness seam consumes. The full ``/readyz`` composition
        (state gated first, then this) lives in
        :func:`gateway_v2.edge.readiness.make_metrics_readiness`.
        """
        if self._directory.available():
            return True, None
        return False, "metrics_directory"

    def directory(self) -> CardinalityDirectory:
        """The backing Cardinality_Directory — the metrics-directory availability gate (R10.1).

        Exposed so ``edge/app.py``'s :func:`build_app` can compose the ``/readyz`` metrics gate via
        :func:`gateway_v2.edge.readiness.make_metrics_readiness` directly from the injected
        Publisher (the concrete :class:`MetricsRegistry`) without reaching into private state. The
        returned directory satisfies the :class:`~gateway_v2.edge.readiness.DirectoryGate` protocol
        structurally (its ``available() -> bool``), so the wiring stays a pure composition over the
        injected registry.
        """
        return self._directory


# --------------------------------------------------------------------------- #
# Module helpers.
# --------------------------------------------------------------------------- #


def _base_name(series_name: str) -> str:
    """The family base name: a labelled series name with its ``{...}`` label block stripped.

    ``amf_state_rounds_total{kind="plan"}`` → ``amf_state_rounds_total``. The base is what the
    renderer types (by the suffix rule) and de-dups on, so a labelled ``_total`` series is correctly
    a counter and all label combinations of a base share ONE ``# TYPE`` line (R1.4/1.5/1.7).
    """
    brace = series_name.find("{")
    return series_name if brace < 0 else series_name[:brace]


def _format(value: float) -> str:
    """Render a numeric value canonically, matching the renderer's own spelling (Property 8).

    An integral float is spelled without a trailing ``.0`` so a second render through
    :func:`gateway_v2.edge.render.render_families` ⟷ :func:`parse` round-trip is byte-stable; a
    non-integral float uses ``repr`` (round-trip-exact for IEEE-754).
    """
    if value != value or value in (float("inf"), float("-inf")):
        return repr(value)
    as_int = int(value)
    return str(as_int) if value == as_int else repr(value)



