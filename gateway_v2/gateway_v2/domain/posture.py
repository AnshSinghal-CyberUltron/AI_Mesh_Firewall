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

OVERLOAD_SHED = "overload_shed"
"""R2-07/R2-08: admission shed under overload. Code only -- `edge` owns the HTTP 503 mapping.

Emitted when the CoDel admission controller sheds a request at the door. Like the `*_UNAVAILABLE`
codes above, this carries no status, message or `ErrorSpec`: `admit` returns a `ShedVerdict`
bearing this code and `edge` renders the 503 + `Retry-After` + `x-should-retry: false`. Living in
the one shared vocabulary stops `admit` from inventing a second spelling (the C37 join failure).
"""

MIN_RETRY_AFTER_S = 1.0
"""R2-08: sheds carrying 6-11 ms of retry-after made the OpenAI SDK retry almost immediately."""


# -- GW12 SSE egress stream codes -------------------------------------------------------------
# Terminal codes for the streaming egress path. Codes only -- `edge` owns the HTTP mapping and
# the SSE `Error_Frame` rendering; `egress`/`dispatch` return these value-codes (R3.3). Timeouts
# (R9) and the fail-closed cut triggers (R10) each map to exactly one spelling so the two sides
# of the stream cannot disagree (the C37 join failure). The pre-existing terminal codes
# `stream_killed` (the `InFlightKill.CUT_NEXT_CHUNK` seam), `scan_failure` and `output_blocked`
# already live on the `egress/stream.py` pipeline and are REUSED unchanged -- not redefined here.

STREAM_KILLED = "stream_killed"
"""R10.3 / disconnect: the pre-existing `InFlightKill.CUT_NEXT_CHUNK` terminal code.

This is the SAME spelling the shipped `egress/stream.py` pipeline already emits for a kill
(`_cut_on_kill` -> `error="killed"` renders through here as `stream_killed`). It is named here
so the one shared vocabulary carries it and the kill-switch trigger (R10.3) and the
disconnect-driven latch reuse ONE spelling rather than inventing a second (the C37 join failure).
Not a new code -- the single-spelled home for the reused one.
"""

STREAM_INTER_CHUNK_TIMEOUT = "stream_inter_chunk_timeout"
"""R9.2: upstream stalled between chunks past `inter_chunk_timeout_s()`. Terminate + release."""

STREAM_IDLE_TIMEOUT = "stream_idle_timeout"
"""R9.3: downstream idle past `idle_timeout_s()`. Terminate + release resources."""

STREAM_WRITE_TIMEOUT = "stream_write_timeout"
"""R9.4: a single downstream write blocked past `write_timeout_s()`. Terminate + release."""

STREAM_MAX_DURATION = "stream_max_duration"
"""R10.2: wall-clock lifetime hit `max_stream_duration_s()`. `CUT_NEXT_CHUNK` + `Error_Frame`."""

STREAM_KEY_REVOKED = "stream_key_revoked"
"""R10: key revoked mid-stream. Drives `killed()` fail-closed through the one cut seam."""

STREAM_PLAN_CHANGED = "stream_plan_changed"
"""R10: plan snapshot changed mid-stream. Fail-closed cut via the same `killed()` seam."""

STREAM_SNAPSHOT_STALE = "stream_snapshot_stale"
"""R10: control-plane snapshot aged past `max_snapshot_age_s()`. Fail-closed cut, never "off"."""

STREAM_MALFORMED_UPSTREAM = "stream_malformed_upstream"
"""R11: upstream SSE could not be decoded into a valid frame. Terminate + `Error_Frame`."""

STREAM_BUFFER_UNAVAILABLE = "stream_buffer_unavailable"
"""R4.5: coalescer high-water < 1 byte (`stream_buffer_bytes` raised). Fail closed, buffer zero."""


def gap_retry_after_s(*, rehydrate_period_ms: float, refresh_ms: float) -> float:
    """How long a client should wait out a shared-state gap (C36).

    One re-hydrator period plus one gateway refresh period: the first is how long until the
    store is compared with Postgres again, the second how long until this worker notices. The
    floor matters as much as the sum -- a sub-second Retry-After is retried so fast that the
    retries become the load (R2-08).
    """
    return max(MIN_RETRY_AFTER_S, (rehydrate_period_ms + refresh_ms) / 1000)
