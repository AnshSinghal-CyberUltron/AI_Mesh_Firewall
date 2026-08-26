"""Phase 0b: analytics period / days clamps and request_id shape (C-9)."""

from __future__ import annotations

import re

from rest_framework.response import Response

VALID_PERIODS = frozenset({"1h", "6h", "24h", "7d", "30d"})

# period → (total_hours, bucket_minutes)
PERIOD_SPEC: dict[str, tuple[int, int]] = {
    "1h": (1, 5),
    "6h": (6, 30),
    "24h": (24, 60),
    "7d": (24 * 7, 240),
    "30d": (24 * 30, 24 * 60),
}

MAX_DASHBOARD_DAYS = 30
REQUEST_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{7,}$")
REQUEST_ID_SQL = r"^[A-Za-z][A-Za-z0-9_.:-]{7,}$"


class InvalidAnalyticsPeriod(ValueError):
    def __init__(self, period: str):
        self.period = period
        super().__init__(
            f"Invalid period {period!r}. Use one of: {', '.join(sorted(VALID_PERIODS))}."
        )


def parse_period(raw, default: str = "24h") -> str:
    if raw is None or str(raw).strip() == "":
        return default
    period = str(raw).strip().lower()
    if period not in VALID_PERIODS:
        raise InvalidAnalyticsPeriod(period)
    return period


def period_from_request(request, default: str = "24h"):
    """Return (period, None) or (None, 400 Response)."""
    try:
        return parse_period(request.query_params.get("period"), default), None
    except InvalidAnalyticsPeriod as exc:
        return None, Response({"detail": str(exc), "code": "invalid_period"}, status=400)


def period_hours(period: str) -> int:
    return PERIOD_SPEC[period][0]


def period_bucket_minutes(period: str) -> int:
    return PERIOD_SPEC[period][1]


def clamp_days(raw, default: int = 30, maximum: int = MAX_DASHBOARD_DAYS) -> int:
    try:
        days = int(raw if raw is not None else default)
    except (TypeError, ValueError):
        return default
    if days < 1:
        return default
    return min(days, maximum)


def is_usable_request_id(value) -> bool:
    if not isinstance(value, str):
        return False
    return bool(REQUEST_ID_RE.match(value.strip()))
