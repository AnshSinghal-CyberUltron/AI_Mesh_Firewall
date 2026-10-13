"""The Publisher's fixed SERIES_COUNT arithmetic (GW14d — split from ``edge/publisher.py``).

:func:`series_count` is the by-construction exposition series count a :class:`Publisher` asserts
against on every scrape (R15.1/R15.2): a pure function of the six producers' ``SERIES_COUNT`` plus
the contract-derived Top-K ``K`` plus the fixed indicator terms, never the observed tenant count
(R15.3). It and its fixed component terms are kept here, apart from the Publisher class, so the
keystone module stays under the module-size gate; the arithmetic is byte-for-byte the shipped
computation and :mod:`gateway_v2.edge.publisher` imports it back.

Everything here is pure ``edge``-layer arithmetic over the producers' published ``SERIES_COUNT``
class/module attributes — no HTTP object, no module-level mutable, no capacity-position literal
(``K`` comes from the contract via :meth:`ResourceContract.metrics_top_k`; the component terms are
protocol-shape counts — a histogram renders as its three native series — not capacity bounds).
"""

from __future__ import annotations

from gateway_v2.admit.metrics import AdmissionMetrics, QuotaMetrics
from gateway_v2.audit import metrics as audit_metrics
from gateway_v2.edge.counters import POSTURE_CODES, REASON_VOCAB
from gateway_v2.runtime import holdback_metrics as holdback_module
from gateway_v2.runtime import state_metrics as state_module
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.stream_metrics import PerRequestExports
from state_control import metrics as rehydration_metrics

# --------------------------------------------------------------------------- #
# SERIES_COUNT fixed-term components (series, not families: a histogram = 3).
# --------------------------------------------------------------------------- #

# A histogram family counts as its three native-exposition series (`_bucket` + `_sum` + `_count`),
# matching every producer's SERIES_COUNT convention.
HISTOGRAM_SERIES = 3
# The bounded tenant set: `other` (1) + the Overflow_Counter (1); the K individual series are added
# per-contract below. The reason/posture families each add one indicator series beyond the vocab.
OTHER_SERIES = 1
OVERFLOW_SERIES = 1
REASON_VOCAB_ERROR_SERIES = 1
POSTURE_COVERAGE_GAP_SERIES = 1
WINDOWED_BUSY_SERIES_COUNT = 1
READ_FAILURES_SERIES = 1


def series_count(contract: ResourceContract) -> int:
    """The fixed by-construction series count for a Publisher built on ``contract`` (R15.1).

    A function of the six producers' ``SERIES_COUNT`` plus the contract-derived ``K`` plus the
    fixed indicator terms — never the observed tenant count (R15.3). Term by term (a histogram
    family counts as its three native series, matching every producer's own ``SERIES_COUNT``):

    * ``AdmissionMetrics.SERIES_COUNT`` (= ``2·5 + 3 + 1 + 5 + 1`` = 20): per-queue depth/age
      scalars, the one ``oldest_age_s_hist`` histogram (as 3), ``admitted_total``, the five
      ``shed_total`` buckets, ``fail_open_total``.
    * ``QuotaMetrics.SERIES_COUNT`` (= 3): the three label-free quota scalars.
    * ``audit_metrics.SERIES_COUNT`` (= ``8 + 6 + 8 + 7`` = 29): all scalar audit/store series.
    * ``state_control.metrics.SERIES_COUNT`` (= ``13 + 4·len(StateKind) + len(FAULT_CATEGORIES)
      + len(ALL_REASONS)``): the re-hydrator's process/per-kind/fault/reason scalars (read across
      the ``root_packages`` boundary, exactly as ``edge/app.py`` already assembles).
    * ``runtime.state_metrics.SERIES_COUNT`` (= ``9·len(StateKind) + 6 + 3 + 6 + 8``): per-kind +
      identity + killswitch + nudge + freshness scalars (``ABSENT`` sentinel gauges included).
    * ``PerRequestExports.SERIES_COUNT`` (= ``5 + 2·3`` = 11): five stream scalars + two
      histograms (as 3 each).
    * ``runtime.holdback_metrics.SERIES_COUNT`` (= ``4 + 3·3`` = 13): four holdback scalars + three
      histograms (as 3 each; the held-tokens histogram among them — R8).
    * ``contract.metrics_top_k()`` individual tenant series + 1 ``other`` + 1 Overflow_Counter:
      the bounded Top-K set (R3.8: ``≤ K + 1``) plus the overflow ``_total`` (R2.5). The Top-K
      family is padded to exactly ``K`` series so the count is invariant to the tenant count.
    * ``len(REASON_VOCAB)`` ``rejected{status,reason}`` series + 1 vocab-error indicator (R6.4/6.5).
    * ``len(POSTURE_CODES)`` posture-refusal counters + 1 coverage-gap indicator (R7.5/14.3).
    * 3 for the windowed C4 add-on histogram (R4) + 1 windowed-busy gauge (R11.3/11.4).
    * 1 for the Publisher's own ``producer_read_failures_total`` indicator (R1.3).
    """
    return (
        AdmissionMetrics.SERIES_COUNT
        + QuotaMetrics.SERIES_COUNT
        + audit_metrics.SERIES_COUNT
        + rehydration_metrics.SERIES_COUNT
        + state_module.SERIES_COUNT
        + PerRequestExports.SERIES_COUNT
        + holdback_module.SERIES_COUNT
        + contract.metrics_top_k()
        + OTHER_SERIES
        + OVERFLOW_SERIES
        + len(REASON_VOCAB)
        + REASON_VOCAB_ERROR_SERIES
        + len(POSTURE_CODES)
        + POSTURE_COVERAGE_GAP_SERIES
        + HISTOGRAM_SERIES
        + WINDOWED_BUSY_SERIES_COUNT
        + READ_FAILURES_SERIES
    )
