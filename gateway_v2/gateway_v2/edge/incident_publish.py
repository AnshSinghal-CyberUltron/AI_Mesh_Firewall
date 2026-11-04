"""GW29 incident series for the GW14d :class:`~gateway_v2.edge.publisher.Publisher` (design 5.10).

Kept out of ``publisher.py`` (748 lines) so the keystone gains only registration lines. Two parts:

* the fixed, label-free :class:`~gateway_v2.admit.incident_metrics.IncidentMetrics` scalars
  (``IncidentMetrics.SERIES_COUNT`` = 27), refreshed from the read model on every scrape;
* the per-model risk family: the top-K models by score through the GW14d
  :class:`~gateway_v2.edge.cardinality.CardinalityDirectory` (deterministic tie-break), PADDED to
  exactly ``K`` series plus one ``other`` and one overflow counter, so the rendered series count is
  invariant to the number of models (AC29, Property 10). Model names are operator-known canonical
  names (``[a-z0-9._:/-]``), never tenant input, but are still treated as unbounded for cardinality.

The magnitude the directory ranks is the score in milli-units, so ties and ordering are integer
exact; the exported value converts back. ``other`` is the sum of the milli-scores beyond K.
"""
from __future__ import annotations

from collections.abc import Callable

from gateway_v2.admit.incident_metrics import (
    RISK_FAMILY,
    IncidentMetrics,
    risk_family_series_count,
)
from gateway_v2.admit.model_risk import ModelRiskTracker
from gateway_v2.edge import render
from gateway_v2.edge.cardinality import CardinalityDirectory
from gateway_v2.edge.render import Family
from gateway_v2.edge.series_count import OTHER_SERIES, OVERFLOW_SERIES
from gateway_v2.runtime.resources import ResourceContract

__all__ = ("IncidentPublication", "incident_series_count")

_MILLI = 1000
RISK_OTHER_SERIES = f"{RISK_FAMILY}_other"
RISK_OVERFLOW_SERIES = f"{RISK_FAMILY}_overflow_total"


def _fmt(value: float) -> str:
    """The renderer's canonical spelling: integral values bare, others ``repr`` (byte-stable)."""
    if value != value or value in (float("inf"), float("-inf")):
        return repr(value)
    as_int = int(value)
    return str(as_int) if value == as_int else repr(value)


def incident_series_count(contract: ResourceContract) -> int:
    """Fixed scalars + the padded top-K risk family. A function of the contract alone."""
    return IncidentMetrics.SERIES_COUNT + risk_family_series_count(
        contract.metrics_top_k(), OTHER_SERIES, OVERFLOW_SERIES,
    )


class IncidentPublication:
    """What the Publisher reads for the incident producer. ``families()`` may raise; the
    Publisher's guarded adapter omits and counts a failing producer."""

    __slots__ = ("_contract", "_metrics", "_refresh", "_risk")

    def __init__(
        self,
        *,
        metrics: IncidentMetrics,
        risk: ModelRiskTracker,
        contract: ResourceContract,
        refresh: Callable[[], None],
    ) -> None:
        self._metrics = metrics
        self._risk = risk
        self._contract = contract
        self._refresh = refresh

    def families(self) -> tuple[list[Family], int]:
        self._refresh()
        families: list[Family] = []
        scalars = self._metrics.series()
        for name in sorted(scalars):
            families.append(Family(
                base=name, type=render.metric_type(name),
                lines=(f"{name} {_fmt(scalars[name])}",),
            ))
        risk_families, risk_count = self._risk_families()
        families.extend(risk_families)
        return families, len(scalars) + risk_count

    def _risk_families(self) -> tuple[list[Family], int]:
        directory = CardinalityDirectory.from_contract(self._contract)
        for reading in self._risk.readings():
            directory.observe(reading.model, round(reading.score * _MILLI))
        k = self._contract.metrics_top_k()
        top = directory.top_k_series()
        lines = [
            f'{RISK_FAMILY}{{model="{model}"}} {_fmt(mag / _MILLI)}'
            for model, mag in top
        ]
        for slot in range(len(top), k):
            lines.append(f'{RISK_FAMILY}{{model="__slot_{slot}__"}} 0')
        other = directory.other_value() / _MILLI
        overflow = directory.overflow_total() + self._risk.untracked_total
        families = [
            Family(base=RISK_FAMILY, type=render.metric_type(RISK_FAMILY), lines=tuple(lines)),
            Family(
                base=RISK_OTHER_SERIES, type=render.metric_type(RISK_OTHER_SERIES),
                lines=(f"{RISK_OTHER_SERIES} {_fmt(other)}",),
            ),
            Family(
                base=RISK_OVERFLOW_SERIES, type=render.metric_type(RISK_OVERFLOW_SERIES),
                lines=(f"{RISK_OVERFLOW_SERIES} {_fmt(float(overflow))}",),
            ),
        ]
        return families, risk_family_series_count(k, OTHER_SERIES, OVERFLOW_SERIES)
