"""The admission producer's exposition families (split out of ``edge/publisher.py``, GW35).

SERIES_COUNT = ``2·len(QUEUE_NAMES) + 3 + 1 + len(ShedReason) + 1 + 2`` = 22: the histogram term
is the ``+ 3``, every other term a scalar family. Pure ``edge``-layer formatting of a frozen
reading; no module-level mutable, no capacity literal.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from gateway_v2.admit.metrics import PREFIX as ADMIT_PREFIX
from gateway_v2.admit.metrics import QUEUE_NAMES
from gateway_v2.edge import render
from gateway_v2.edge.render import Family
from gateway_v2.edge.series_count import HISTOGRAM_SERIES
from gateway_v2.edge.windowed import DEFAULT_BOUNDS

if TYPE_CHECKING:
    from gateway_v2.admit.metrics import AdmissionReading

__all__ = ("admit_families",)

FlatFamilies = Callable[[Mapping[str, float]], tuple[list[Family], int]]


def admit_families(reading: AdmissionReading, flat: FlatFamilies) -> tuple[list[Family], int]:
    """The admission families for ``reading``; ``flat`` groups the scalar dict into families."""
    families: list[Family] = []
    scalars: dict[str, float] = {}
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
    scalars[f"{ADMIT_PREFIX}_org_limits_table_full_total"] = float(
        reading.org_limits_table_full_total
    )
    scalars[f"{ADMIT_PREFIX}_org_limits_slots_used"] = float(reading.org_limits_slots_used)
    scalar_families, scalar_count = flat(scalars)
    families.extend(scalar_families)
    families.append(
        render.render_histogram(
            f"{ADMIT_PREFIX}_oldest_age_ms", reading.oldest_age_s_hist, DEFAULT_BOUNDS
        )
    )
    return families, scalar_count + HISTOGRAM_SERIES
