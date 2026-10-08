"""The shared-state periods, and the relationships between them that must hold.

Every rule here is a fail-at-START check, not a runtime branch, and each one exists because
getting the relationship wrong has already cost something measurable:

* C36: RC2 set the background store timeout EQUAL to the staleness ceiling, so every half-open
  failover produced fleet-wide fail-closed. Nobody validated the relationship because each value
  looked reasonable on its own.
* GW05b: a freshness bound shorter than two re-hydrator periods makes stamps that are born
  stale -- a stamp is up to one period plus one round old before a gateway can even read it -- so
  the fleet would refuse continuously while every component reported healthy.
* GW05b: a Postgres grace shorter than the freshness bound cannot ride anything through, because
  the fleet fails closed before the window it exists to cover has ended.

`fresh_ms = 0` is permitted and is a BREAK-GLASS, not a mode: it reinstates R2-03's exposure.
`warnings` is how a start-up path is told to say so out loud, without this module needing a
logger -- it stays a pure value type with invariants.
"""

from __future__ import annotations

from dataclasses import dataclass

from gateway_v2.domain.locks import FRESH_MS, PG_GRACE_MS

DEFAULT_REHYDRATE_PERIOD_MS = 1_000
DEFAULT_REFRESH_PERIOD_MS = 500
DEFAULT_DEEP_EVERY = 60


@dataclass(frozen=True, slots=True)
class StateKnobs:
    """Periods for the state subsystem. Validated on construction; never re-validated later."""

    fresh_ms: int = FRESH_MS
    """Refuse state not verified against Postgres within this long. 0 = not enforced."""

    pg_grace_ms: int = PG_GRACE_MS
    """How long a Postgres outage may be ridden out on the last verified cursors. 0 = never."""

    rehydrate_period_ms: int = DEFAULT_REHYDRATE_PERIOD_MS
    """How often a re-hydrator compares the store with Postgres and stamps."""

    refresh_period_ms: int = DEFAULT_REFRESH_PERIOD_MS
    """How often a gateway worker runs a state cycle."""

    deep_every: int = DEFAULT_DEEP_EVERY
    """Run the O(records) index comparison every Nth re-hydrator round."""

    def __post_init__(self) -> None:
        if self.rehydrate_period_ms <= 0:
            raise ValueError("rehydrate_period_ms must be positive")
        if self.refresh_period_ms <= 0:
            raise ValueError("refresh_period_ms must be positive")
        if self.deep_every <= 0:
            raise ValueError("deep_every must be positive")
        if self.fresh_ms < 0:
            raise ValueError("fresh_ms must not be negative")
        if self.pg_grace_ms < 0:
            raise ValueError("pg_grace_ms must not be negative")
        if self.fresh_ms == 0:
            return  # freshness off: the relationships below are about enforcing it
        if self.fresh_ms < 2 * self.rehydrate_period_ms:
            raise ValueError(
                f"fresh_ms={self.fresh_ms} must be at least 2 x "
                f"rehydrate_period_ms={self.rehydrate_period_ms}: a stamp is up to one period "
                "plus one round old before a gateway can read it, so a tighter bound refuses "
                "continuously while every component is healthy",
            )
        if self.refresh_period_ms * 2 > self.fresh_ms:
            raise ValueError(
                f"refresh_period_ms={self.refresh_period_ms} must be at most half of "
                f"fresh_ms={self.fresh_ms}, or one slow cycle outlives the window it protects",
            )
        if self.pg_grace_ms != 0 and self.pg_grace_ms < self.fresh_ms:
            raise ValueError(
                f"pg_grace_ms={self.pg_grace_ms} must be 0 (off) or at least "
                f"fresh_ms={self.fresh_ms}: a shorter grace cannot ride anything through",
            )

    @property
    def enforced(self) -> bool:
        return self.fresh_ms > 0

    @property
    def store_timeout_ceiling_s(self) -> float:
        """What `require_bounded_client` must be given. C36's relationship, made explicit.

        A store operation that can block longer than half the refresh period means a refresh
        round can outlive the staleness window it is supposed to keep closed: the snapshot never
        ages, `state()` never reports STALE, and the fail-closed posture silently does not exist.
        """
        return self.refresh_period_ms / 2 / 1000

    @property
    def enforcement_bound_s(self) -> float:
        """Worst case from a committed write to enforcement-or-refusal, when all is healthy.

        One re-hydrator period to notice, one gateway cycle to read it. This is the number
        R2-03's invariant is stated in, so it is derived here rather than written down twice.
        """
        return (self.rehydrate_period_ms + self.refresh_period_ms) / 1000

    def is_deep_round(self, round_number: int) -> bool:
        """Is this 1-based re-hydrator round the one that also compares the whole index?

        Round 1 is deliberately NOT deep. The deep pass is O(records), and making start-up pay
        for it would add the whole estate to the time before the first stamp -- which is exactly
        the latency a new process is already waiting on.
        """
        return round_number > 0 and round_number % self.deep_every == 0

    @property
    def warnings(self) -> tuple[str, ...]:
        """What a start-up path must log. Empty when the configuration is unremarkable."""
        out: list[str] = []
        if not self.enforced:
            out.append(
                "state freshness is NOT enforced (fresh_ms=0): this reinstates R2-03 -- a "
                "process on a lagging replica can serve revoked or killed state",
            )
        if self.pg_grace_ms == 0 and self.enforced:
            out.append(
                "the Postgres ride-through is disabled (pg_grace_ms=0): a routine Cloud SQL "
                "failover will fail the fleet closed for its duration",
            )
        return tuple(out)


DEFAULT_KNOBS = StateKnobs()
