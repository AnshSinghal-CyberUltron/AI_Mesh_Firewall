"""Keep gateway-v2 rows out of the v1 dashboard totals.

The rows stay in the threat feed, with ``metadata.source`` set, so Scan Detail
can open them. KPI and chart totals use :func:`exclude_shadow` and report the
v2 count separately. Adding the two numbers is a shadow double-count.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SOURCE = "gateway_v2"


def split_source(
    rows: list[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """Partition records. The caller must not add the two lengths together."""
    primary: list[Mapping[str, Any]] = []
    shadow: list[Mapping[str, Any]] = []
    for row in rows:
        if row.get("source") == SOURCE:
            shadow.append(row)
        else:
            primary.append(row)
    return primary, shadow


def exclude_shadow(qs: Any) -> Any:
    return qs.exclude(metadata__source=SOURCE)


def only_shadow(qs: Any) -> Any:
    return qs.filter(metadata__source=SOURCE)
