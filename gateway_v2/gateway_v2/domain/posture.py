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

__all__ = (
    "KILL_SWITCH_UNAVAILABLE",
    "SHARED_STATE_UNAVAILABLE",
    "PLAN_UNAVAILABLE",
    "BUDGET_UNAVAILABLE",
    "RATE_LIMITED",
    "POLICY_REGEX_TIMEOUT",
    "OVERLOAD_SHED",
    "MIN_RETRY_AFTER_S",
    "STREAM_KILLED",
    "STREAM_INTER_CHUNK_TIMEOUT",
    "STREAM_IDLE_TIMEOUT",
    "STREAM_WRITE_TIMEOUT",
    "STREAM_MAX_DURATION",
    "STREAM_KEY_REVOKED",
    "STREAM_PLAN_CHANGED",
    "STREAM_SNAPSHOT_STALE",
    "STREAM_MALFORMED_UPSTREAM",
    "STREAM_BUFFER_UNAVAILABLE",
    "STREAM_STRICT_BUFFER_OVERFLOW",
    "VECTOR_OK",
    "UNSUPPORTED_OPERATION",
    "PROVIDER_UNREACHABLE",
    "CREDENTIAL_UNAVAILABLE",
    "STORE_NOT_CONFIGURED",
    "CREDENTIAL_SCOPE_MISMATCH",
    "ORG_IDENTITY_INVALID",
    "VECTOR_CAPACITY_EXCEEDED",
    "VECTOR_CAPACITY_UNSET",
    "CREDENTIAL_LEAK_WITHHELD",
    "COLLECTION_EXISTS",
    "INVALID_API_KEY",
    "gap_retry_after_s",
)

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

RATE_LIMITED = "rate_limited"
"""GW36 S4: the per-key GCRA reject -- the per-worker burst/sustained rate was exceeded.

Named here so the composed ``QuotaGate`` (``edge/quota_gate.py``) does not invent a second spelling
when it surfaces a ``LocalGCRA`` refusal. Like the other codes it carries no status, message or
``ErrorSpec``: ``admit`` returns a value carrying this code and ``edge`` owns the HTTP 429 +
``rate_limit_error`` mapping. The TAT is left untouched on reject, so the allowance is unchanged and
the next legitimately-timed arrival is still served; the refusal makes zero provider calls.

This is the GATEWAY-side per-key rate limit and is deliberately DISTINCT from
``upstream_rate_limited`` (the provider's own 429 relayed by ``dispatch``): the first is the gateway
refusing before any upstream call, the second is the upstream refusing the gateway's call."""

POLICY_REGEX_TIMEOUT = "policy_regex_timeout"
"""A customer regex exceeded the guard-wait budget or was refused before it ran. Fail closed."""

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

STREAM_STRICT_BUFFER_OVERFLOW = "strict_buffer_overflow"
"""R9.5: STRICT accumulation exceeded `ResourceContract.stream_buffer_bytes`.

Fail closed, zero content; `edge` owns the HTTP/SSE rendering.
"""


# -- GW30 Vector_Posture_Codes ---------------------------------------------------------------
# Codes only for the vector-store adapter layer. `edge` owns any HTTP mapping (GW31) and the
# rendering; `dispatch` returns these value-codes on a frozen `AdapterResult` /
# `CredentialResolution`, never an unhandled exception and never an empty success. Each code has
# exactly one spelling so the adapter, registry and credential seam cannot disagree about a fault
# (the C37 join failure).

VECTOR_OK = "vector_ok"
"""R2/R5.6/R6.6: the single success code. A zero-record success is this code with `records==()`."""

UNSUPPORTED_OPERATION = "unsupported_operation"
"""R5.1: a capability the connector does not support. Distinct from `VECTOR_OK` and every error.

A caller distinguishes an unsupported operation from a clean zero-record search by `code` alone,
without inspecting the record count: `records is None` (absent), never an empty success.
"""

PROVIDER_UNREACHABLE = "provider_unreachable"
"""R6.1: transport failure (refusal / connect-or-read timeout / transport error / upstream 5xx).

Never an empty result labelled a clean search; `records is None`, distinguishable by `code`.
"""

CREDENTIAL_UNAVAILABLE = "credential_unavailable"
"""R8: the org credential is unresolvable, unverified, or the control plane was unreachable."""

STORE_NOT_CONFIGURED = "store_not_configured"
"""R7.2: no connector is registered for the requested `store_id`. Routes to no connector."""

CREDENTIAL_SCOPE_MISMATCH = "credential_scope_mismatch"
"""R10.2: the resolved credential's `org_id` != the request's. Refused before any store call."""

ORG_IDENTITY_INVALID = "org_identity_invalid"
"""R10.3: the request carried a missing/empty/unresolvable `org_id`. Fail closed, no store call."""

VECTOR_CAPACITY_EXCEEDED = "vector_capacity_exceeded"
"""R11.3: a `ResourceContract` bound was exceeded mid-op. Abort, discard the partial result."""

VECTOR_CAPACITY_UNSET = "vector_capacity_unset"
"""R11.6: a required bound was unset at connector init. Refuse to start, no default literal."""

CREDENTIAL_LEAK_WITHHELD = "credential_leak_withheld"
"""R9.6: the pre-emission leak guard withheld the whole emission. Detail carries a ref id only."""

COLLECTION_EXISTS = "collection_exists"
"""R2.9: `create_collection` named an existing collection. Nothing was created or reset."""

INVALID_API_KEY = "invalid_api_key"
"""GW36 S3: no credential, or VERIFIED state holds no live key for it. HTTP 401, OpenAI's code.

The opposite of `SHARED_STATE_UNAVAILABLE`, which means "could not verify" (503, the key may be
valid). A client fault, not a gateway posture, so it is not in the posture counters."""


def gap_retry_after_s(*, rehydrate_period_ms: float, refresh_ms: float) -> float:
    """How long a client should wait out a shared-state gap (C36).

    One re-hydrator period plus one gateway refresh period: the first is how long until the
    store is compared with Postgres again, the second how long until this worker notices. The
    floor matters as much as the sum -- a sub-second Retry-After is retried so fast that the
    retries become the load (R2-08).
    """
    return max(MIN_RETRY_AFTER_S, (rehydrate_period_ms + refresh_ms) / 1000)
