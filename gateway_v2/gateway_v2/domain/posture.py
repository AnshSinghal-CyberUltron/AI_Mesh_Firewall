"""ONE failure-posture vocabulary for shared state. Codes only; `edge` owns the HTTP mapping.

Every component that depends on published state maps an outage to the SAME code, so no two can
take opposite actions on one fault -- the v1 rate-limiter/breaker defect. RC2 kept this table in
`admit/posture.py`, which cannot work here: `plan` sits BELOW `admit` in the layer contract and
the plan snapshot needs the same vocabulary, so it lives in `domain` where every layer can reach
it.

These are codes, not responses. There is deliberately no status, no message and no `ErrorSpec`:
`HTTPException` and `JSONResponse` are forbidden outside `edge`/`resolve`, and GW06 owns the
rendering. What this module prevents is the C37 failure one level down -- two planes agreeing on
behaviour but disagreeing on spelling, so the join finds nothing.
"""

from __future__ import annotations

KILL_SWITCH_UNAVAILABLE = "kill_switch_unavailable"
"""The kill-switch snapshot is stale or unverified. Chat refuses; fail CLOSED, never "off"."""

SHARED_STATE_UNAVAILABLE = "shared_state_unavailable"
"""Identity could not be resolved from verified state. 503, never 401: the key may be valid."""

PLAN_UNAVAILABLE = "plan_unavailable"
"""Known tenant, plan missing or unverified. NEVER `plan_unknown_tenant` -- see C36."""

BUDGET_UNAVAILABLE = "budget_unavailable"
"""No producer yet.

Named here so R2-09 cannot invent a second spelling when GW06 builds the lease component. The
budget posture is deliberately NARROWER than the others: a budget-kind failure spends the
remaining lease and then refuses only quota, because treating it as global would turn a counter
problem into an outage.
"""

MIN_RETRY_AFTER_S = 1.0
"""R2-08: sheds carrying 6-11 ms of retry-after made the OpenAI SDK retry almost immediately."""


def gap_retry_after_s(*, rehydrate_period_ms: float, refresh_ms: float) -> float:
    """How long a client should wait out a shared-state gap (C36).

    One re-hydrator period plus one gateway refresh period: the first is how long until the
    store is compared with Postgres again, the second how long until this worker notices. The
    floor matters as much as the sum -- a sub-second Retry-After is retried so fast that the
    retries become the load (R2-08).
    """
    return max(MIN_RETRY_AFTER_S, (rehydrate_period_ms + refresh_ms) / 1000)
