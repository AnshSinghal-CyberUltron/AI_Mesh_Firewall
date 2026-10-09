"""The audit memory budget, as a validated value type. R2-05's arithmetic lives here.

Audit shares the hot store with the state the gateways enforce from, and audit streams have no
TTL. Round 2 measured the consequence: audit grew 1.48 -> 6.42 GiB in 51 minutes on a shared
10.4 GiB instance. The per-org cap in force at the time (`MAXLEN 2,000,000`, about 2.7 KB per
record, so roughly 5.4 GiB *per org*) is a record count, not a memory bound, so it never
engaged. What happens when the store fills is not "audit is lost":

1. Under an evicting policy -- `volatile-lru` is Memorystore's default -- the guard-owner
   registrations are evicted FIRST, because they are the only keys with a TTL. Discovery then
   sees 0/N and new gateways never become ready.
2. Every write is refused after that: audit `XADD`, the lease counters, and the control plane's
   publishes.
3. So a kill switch or a key revocation commits to Postgres and is never published. The
   gateways keep serving the previous snapshot, which is still signed and still valid.

`noeviction` alone does not fix it. The heartbeats are refused, so the registrations expire
instead of being evicted, and the publishes are still refused. The bound is the fix;
`noeviction` is defence in depth on top of it.

Why the knobs are a value type with invariants rather than four floats read at the call site:
the relationships between them are the contract, and each one is a fail-at-START check for the
same reason `StateKnobs` is. A fraction of 1.0 is individually reasonable and leaves nothing for
the state, the registrations and the client buffers -- which is the original defect with extra
steps.

Knob names: the card and the reference patch (`rc3-audit-mem-v1`) name the `RV_AUDIT_*` family,
and operators have those in their runbooks, so `knobs_from_env` accepts them. This tree's own
convention is the `AMF_` prefix (`AMF_STATE_FRESH_MS`, `AMF_PG_LOCK_TIMEOUT_MS`), so the `AMF_`
spelling is preferred and wins when both are set.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

MIB = 1 << 20

DEFAULT_FRACTION = 0.5
"""Share of `maxmemory` audit may use when no explicit budget is set.

The other half is not spare: it holds the published state, the guard-owner registrations, the
lease counters and the client buffers. This is the reference's default and the number the
measured 64 MB run passed at (20.8 MiB peak, 33%).
"""

DEFAULT_SAMPLE_S = 10.0
"""How often worker 0 reads `INFO memory` / `INFO stats`. Off the request path, two small round
trips per node per period, which is also what lets the budget follow a store resize."""

DEFAULT_STREAM_MAXLEN = 2_000_000
"""The per-org UPPER bound. Unchanged from RC2, but no longer the operative limit: the effective
MAXLEN derives from the memory budget and this only caps it."""

DEFAULT_MIN_RETAIN = 100
"""A per-org MAXLEN never goes below this. Approximate `XTRIM` removes whole stream nodes, so
trimming towards single digits buys nothing and costs the only recent history an operator has
while they are diagnosing whatever filled the store."""

MAX_FRACTION = 0.9
"""At least 10% of `maxmemory` must stay outside the audit budget."""


@dataclass(frozen=True, slots=True)
class AuditMemoryKnobs:
    """The audit memory budget configuration. Validated on construction, never re-validated."""

    budget_mb: float | None = None
    """Store memory ALL audit streams may use. None means derive it from `maxmemory`.

    Set this explicitly on a SHARED instance. The fraction below is per gateway fleet, so
    several namespaces or lanes on one Valkey would each claim the same half of it.
    """

    fraction: float = DEFAULT_FRACTION
    """Share of the store's `maxmemory` for audit when no explicit budget is set."""

    sample_s: float = DEFAULT_SAMPLE_S
    """`INFO memory` / `INFO stats` sampling period for the gauges and the budget re-read."""

    stream_maxlen: int = DEFAULT_STREAM_MAXLEN
    """Per-org upper bound on stream length, whatever the budget allows."""

    min_retain: int = DEFAULT_MIN_RETAIN
    """Per-org lower bound on the derived MAXLEN."""

    def __post_init__(self) -> None:
        if self.budget_mb is not None and self.budget_mb <= 0:
            raise ValueError(
                "budget_mb must be positive (None = derived from the store's maxmemory); a "
                "zero or negative budget would trim every stream to min_retain and call it a "
                "configuration",
            )
        if not 0 < self.fraction <= MAX_FRACTION:
            raise ValueError(
                f"fraction must be in (0, {MAX_FRACTION}]: at least "
                f"{round((1 - MAX_FRACTION) * 100)}% of maxmemory has to stay for the published "
                "state, the guard-owner registrations and the client buffers, which is the "
                "space R2-05 measured audit taking",
            )
        if self.sample_s <= 0:
            raise ValueError("sample_s must be positive")
        if self.stream_maxlen <= 0:
            raise ValueError("stream_maxlen must be positive")
        if self.min_retain <= 0:
            raise ValueError("min_retain must be positive")
        if self.min_retain > self.stream_maxlen:
            raise ValueError(
                f"min_retain={self.min_retain} must not exceed "
                f"stream_maxlen={self.stream_maxlen}, or the clamp has no ordering",
            )

    @property
    def explicit_budget_bytes(self) -> int | None:
        """The configured budget in bytes, or None when it is to be derived."""
        return None if self.budget_mb is None else int(self.budget_mb * MIB)

    @property
    def warnings(self) -> tuple[str, ...]:
        """What a start-up path must say out loud. Empty when the configuration is unremarkable."""
        out: list[str] = []
        if self.budget_mb is None:
            out.append(
                f"no explicit audit budget: using {self.fraction} x the store's maxmemory. On a "
                "SHARED store set AMF_AUDIT_STORE_BUDGET_MB per namespace so that all the "
                "budgets together stay inside maxmemory",
            )
        if self.fraction > DEFAULT_FRACTION and self.budget_mb is None:
            out.append(
                f"the audit budget fraction is {self.fraction}, above the default "
                f"{DEFAULT_FRACTION}: audit may claim more of the store than the state, the "
                "registrations and the client buffers are left",
            )
        return tuple(out)


DEFAULT_AUDIT_KNOBS = AuditMemoryKnobs()


_BUDGET_MB = ("AMF_AUDIT_STORE_BUDGET_MB", "RV_AUDIT_STORE_BUDGET_MB")
_FRACTION = ("AMF_AUDIT_STORE_FRACTION", "RV_AUDIT_STORE_FRACTION")
_SAMPLE_S = ("AMF_STORE_MEMORY_SAMPLE_S", "RV_STORE_MEMORY_SAMPLE_S")
_STREAM_MAXLEN = ("AMF_AUDIT_STREAM_MAXLEN", "RV_AUDIT_STREAM_MAXLEN")


def _first(env: Mapping[str, str], names: tuple[str, ...]) -> tuple[str, str] | None:
    """The first name that is set to something non-blank, with the value. `AMF_` wins."""
    for name in names:
        raw = (env.get(name) or "").strip()
        if raw:
            return name, raw
    return None


def _float(env: Mapping[str, str], names: tuple[str, ...], default: float) -> float:
    found = _first(env, names)
    if found is None:
        return default
    name, raw = found
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _int(env: Mapping[str, str], names: tuple[str, ...], default: int) -> int:
    found = _first(env, names)
    if found is None:
        return default
    name, raw = found
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def knobs_from_env(env: Mapping[str, str]) -> AuditMemoryKnobs:
    """Resolve the knobs, or raise naming what is wrong.

    A pure function of a mapping, not a reader of `os.environ`, for the reason R2-04 recorded:
    a start-up path that can only be exercised by mutating process state does not get exercised.
    """
    budget = _first(env, _BUDGET_MB)
    return AuditMemoryKnobs(
        budget_mb=None if budget is None else _float(env, _BUDGET_MB, 0.0),
        fraction=_float(env, _FRACTION, DEFAULT_FRACTION),
        sample_s=_float(env, _SAMPLE_S, DEFAULT_SAMPLE_S),
        stream_maxlen=_int(env, _STREAM_MAXLEN, DEFAULT_STREAM_MAXLEN),
    )
