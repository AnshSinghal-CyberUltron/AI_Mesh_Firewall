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


def feed_category(meta: Mapping[str, Any] | None, policy_name: str | None) -> str:
    """Category for a threat-feed row. A v2 row with none stays blank."""
    meta = meta or {}
    named = meta.get("threat_category") or policy_name or ""
    if meta.get("source") == SOURCE:
        return str(named)
    return str(named or "Policy")


def feed_severity(meta: Mapping[str, Any] | None) -> Any:
    """Severity for a threat-feed row. A v2 row with none stays blank."""
    meta = meta or {}
    if meta.get("source") == SOURCE:
        named = meta.get("security_risk_score")
        if named in (None, ""):
            named = meta.get("severity")
        return "" if named in (None, "") else named
    return meta.get("security_risk_score") or meta.get("severity") or "medium"


_OPERATIONAL_REFUSALS = ["auth", "rate_limit"]


def v2_totals(qs: Any) -> dict[str, int]:
    """Counts for the gateway-v2 rows in ``qs``, kept apart from the v1 totals.

    A *threat* is a flag, redact or block that was not an operational refusal. A refusal at the
    ``auth`` or ``rate_limit`` stage (a rejected credential, a shed, a rate limit) is listed in the
    feed and counted in ``refused_total``, not as a threat. An ``error`` row is an upstream or
    internal fault, counted in ``error_total``. Rows written before ``refused_at`` existed have no
    such key and count as they always did.
    """
    from django.db.models import Count, Q

    operational = Q(metadata__has_key="refused_at") & Q(
        metadata__refused_at__in=_OPERATIONAL_REFUSALS
    )
    threat = ~operational
    agg = qs.aggregate(
        total_threats=Count("id", filter=Q(action__in=["flag", "redact", "block"]) & threat),
        blocked=Count("id", filter=Q(action="block") & threat),
        redacted=Count("id", filter=Q(action="redact") & threat),
        refused_total=Count("id", filter=operational),
        error_total=Count("id", filter=Q(action="error")),
    )
    return {key: int(value or 0) for key, value in agg.items()}
