"""ONE failure-posture table for shared state (identity, quota, kill-switch, plan).

Every component maps a store outage to the same rejection, so no two components
can take opposite actions for the same fault (the v1 rate-limiter/breaker defect).
"""

from __future__ import annotations

from rvproto.domain.request import ErrorSpec

STORE_UNAVAILABLE = ErrorSpec(
    503, "service_unavailable", "shared_state_unavailable",
    "Gateway shared state is unavailable; failing closed.",
)
KILLSWITCH_ON = ErrorSpec(
    503, "service_unavailable", "kill_switch_engaged", "Service disabled by kill switch.",
)
KILLSWITCH_STALE = ErrorSpec(
    503, "service_unavailable", "kill_switch_unavailable",
    "Kill-switch snapshot is older than its staleness ceiling; failing closed.",
)
KILLSWITCH_ORG = ErrorSpec(
    503, "service_unavailable", "kill_switch_engaged", "Organization disabled by kill switch.",
)
KILLSWITCH_MODEL = ErrorSpec(
    503, "service_unavailable", "kill_switch_engaged", "Model disabled by kill switch.", "model",
)
INVALID_KEY = ErrorSpec(
    401, "invalid_request_error", "invalid_api_key", "Incorrect API key provided.",
)
MISSING_KEY = ErrorSpec(
    401, "invalid_request_error", "invalid_api_key",
    "You didn't provide an API key. Provide it as a Bearer token.",
)
PLAN_UNAVAILABLE = ErrorSpec(
    503, "service_unavailable", "plan_unavailable",
    "Organization execution plan is unavailable; failing closed.",
)
PLAN_UNKNOWN = ErrorSpec(
    403, "invalid_request_error", "plan_unknown_tenant",
    "Organization has no execution plan; complete onboarding.",
)


def rate_limited(retry_after_s: float) -> ErrorSpec:
    return ErrorSpec(429, "requests", "rate_limit_exceeded",
                     "Rate limit reached for organization.", None, retry_after_s)


def overloaded(reason: str, retry_after_s: float) -> ErrorSpec:
    """GW19 declared overload response: shed before any work, retry after the drain estimate."""
    return ErrorSpec(503, "server_overloaded", "overloaded",
                     f"Gateway at capacity ({reason}); retry later.", None, retry_after_s)


def quota_exhausted() -> ErrorSpec:
    return ErrorSpec(429, "insufficient_quota", "insufficient_quota",
                     "Organization token budget exhausted.", None, None)
