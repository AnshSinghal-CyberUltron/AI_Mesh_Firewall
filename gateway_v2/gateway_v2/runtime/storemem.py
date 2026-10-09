"""Store memory safety: the start-up self-check, the budget derivation, and the gauges.

This is the half of R2-05 that answers "how much of the store may audit use, and is the store
configured so that audit filling it would evict the things the fleet enforces from". The other
half -- actually trimming to the budget, and making loss visible -- is `audit/`.

Three deliberate shapes:

* **`INFO memory` + `INFO stats`, with `CONFIG GET` only as a fallback.** Memorystore permits
  `INFO` and restricts `CONFIG` to clients, so a policy check built on `CONFIG GET
  maxmemory-policy` reads empty exactly where it matters most. `INFO memory` carries
  `maxmemory_policy` directly.
* **The check is NEVER fatal.** A gateway that cannot reach the store at start-up has a worse
  problem than an unknown eviction policy, and refusing to start would turn a store blip into a
  fleet outage. It logs `store_memory_check_failed` and returns None; the budget then falls back
  to the per-org `stream_maxlen` with a line saying so.
* **No metrics import.** This module produces readings; `audit/metrics.py` decides how they are
  published, the same split `state_task.py` / `state_metrics.py` already uses. The sampler takes
  an `on_sample` callback, so nothing here has to know what a registry is.

The sampler runs on **worker 0 only**, so a node publishes one series rather than one per
worker, and it runs beside the audit writer in the same task group, so it is cancelled with it.
Two small round trips per node per period is the whole cost, and it is off the request path --
R2-10 and C31 are both about measurement work that ended up on a serving loop.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from gateway_v2.domain.audit_knobs import AuditMemoryKnobs

LOG = logging.getLogger("amf.audit.storemem")

EVICTING_PREFIXES = ("volatile-", "allkeys-")
"""Any policy that evicts can remove what the fleet needs.

`volatile-*` takes the guard-owner registrations first, because they are the only TTL keys.
`allkeys-*` can take the published state itself. Neither is safe under an audit flood, which is
why the self-check treats the whole family as one condition.
"""

BUDGET_HEADROOM = 0.9
"""An explicit budget above this share of `maxmemory` is an error, not a preference."""


@dataclass(frozen=True, slots=True)
class StoreMemory:
    """One reading of the store's memory posture."""

    used: int
    maxmemory: int
    """0 means no limit configured, which is its own warning: audit can then grow unbounded."""

    policy: str
    evicted_keys: int

    @property
    def used_ratio(self) -> float:
        """Used over the limit, or 0.0 when there is no limit to be a ratio of."""
        return self.used / self.maxmemory if self.maxmemory > 0 else 0.0

    @property
    def policy_unsafe(self) -> bool:
        return policy_unsafe(self.policy)


def policy_unsafe(policy: str) -> bool:
    """Does this `maxmemory-policy` evict? An unknown or empty policy is not called unsafe.

    Reporting "unsafe" for a policy we failed to read would make the gauge mean two things at
    once -- "this store evicts" and "we could not tell" -- and an alarm cannot act on that.
    Unreadable is reported separately, by the check returning None.
    """
    return policy.startswith(EVICTING_PREFIXES)


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    """How many bytes all audit streams may use, and where that number came from.

    `source` exists so the start-up log can be read back later. A budget silently derived from a
    `maxmemory` the operator did not know was set is how a shared instance ends up with several
    fleets each claiming half of it.
    """

    budget_bytes: int | None
    source: str
    warnings: tuple[str, ...] = ()

    @property
    def known(self) -> bool:
        return self.budget_bytes is not None


def audit_budget(knobs: AuditMemoryKnobs, mem: StoreMemory | None) -> BudgetDecision:
    """Resolve the budget: explicit knob, else fraction x `maxmemory`, else unknown.

    "Unknown" is not "unlimited". It means the per-org `stream_maxlen` is the only bound left,
    which is the RC2 behaviour R2-05 measured, so it is returned with a warning rather than
    quietly.
    """
    explicit = knobs.explicit_budget_bytes
    if explicit is not None:
        warnings: tuple[str, ...] = ()
        if mem is not None and mem.maxmemory > 0 and explicit > mem.maxmemory * BUDGET_HEADROOM:
            warnings = (
                f"the audit budget ({explicit} bytes) is more than "
                f"{round(BUDGET_HEADROOM * 100)}% of the store's maxmemory "
                f"({mem.maxmemory} bytes), so it leaves almost nothing for the published state "
                "and the guard-owner registrations",
            )
        return BudgetDecision(explicit, "AMF_AUDIT_STORE_BUDGET_MB", warnings)
    if mem is not None and mem.maxmemory > 0:
        return BudgetDecision(
            int(mem.maxmemory * knobs.fraction),
            f"AMF_AUDIT_STORE_FRACTION={knobs.fraction} x maxmemory={mem.maxmemory}",
        )
    return BudgetDecision(
        None,
        "none: the store's maxmemory is 0 or unreadable",
        (
            "the audit memory budget is UNKNOWN, so audit is bounded only by the per-org "
            f"stream_maxlen={knobs.stream_maxlen}. That is the RC2 configuration R2-05 "
            "measured filling a 10.4 GiB store in 51 minutes: set a store maxmemory, or set "
            "AMF_AUDIT_STORE_BUDGET_MB",
        ),
    )


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        if isinstance(value, (bytes, str)):
            try:
                return int(value)
            except ValueError:
                return 0
        return 0
    return int(value)


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _section_get(section: Any, name: str) -> object:
    """Read one field out of an `INFO` section, tolerating bytes keys.

    `decode_responses=False` clients hand back bytes keys, and a check that silently reads
    nothing on half the client configurations is worse than no check.
    """
    if not hasattr(section, "get"):
        return None
    direct = section.get(name)
    if direct is not None:
        return direct
    return section.get(name.encode("ascii"))


async def _config_policy(client: Any) -> str:
    """`CONFIG GET maxmemory-policy`, or "" where `CONFIG` is restricted (Memorystore)."""
    try:
        got = await client.config_get("maxmemory-policy")
    except Exception as exc:  # the fallback of a fallback: never raise out of a posture check
        LOG.debug("CONFIG GET maxmemory-policy is unavailable: %s", exc)
        return ""
    return _as_text(_section_get(got, "maxmemory-policy"))


async def read_memory(client: Any) -> StoreMemory:
    """Two small round trips: `INFO memory` and `INFO stats`.

    Raises whatever the client raises when the store does not answer. Callers decide what that
    means; nothing in this module treats it as fatal.
    """
    memory = await client.info("memory")
    stats = await client.info("stats")
    policy = _as_text(_section_get(memory, "maxmemory_policy")) or await _config_policy(client)
    return StoreMemory(
        used=_as_int(_section_get(memory, "used_memory")),
        maxmemory=_as_int(_section_get(memory, "maxmemory")),
        policy=policy,
        evicted_keys=_as_int(_section_get(stats, "evicted_keys")),
    )


@dataclass(frozen=True, slots=True)
class StartupCheck:
    """What the start-up probe found. `memory is None` means the store did not answer."""

    memory: StoreMemory | None
    budget: BudgetDecision

    @property
    def reachable(self) -> bool:
        return self.memory is not None


async def startup_check(
    client: Any,
    knobs: AuditMemoryKnobs,
    *,
    worker: int = 0,
) -> StartupCheck:
    """Log the store's memory posture before the first audit write. Never fatal.

    Every worker runs this, because every worker derives its own budget and a single worker
    logging it would make a mismatch invisible. The eviction-policy finding is logged at ERROR
    with the remediation in the message: an operator reading `store_policy_unsafe` in isolation
    cannot tell that the fix is `noeviction` *plus* the bound, and that `noeviction` on its own
    does not work.
    """
    try:
        memory = await read_memory(client)
    except Exception as exc:  # a store that will not answer must not stop the gateway starting
        LOG.warning(
            "store_memory_check_failed worker=%d error=%s: the audit memory budget cannot be "
            "derived from maxmemory, so audit falls back to stream_maxlen=%d",
            worker,
            f"{type(exc).__name__}: {exc}"[:300],
            knobs.stream_maxlen,
            exc_info=False,
        )
        return StartupCheck(None, audit_budget(knobs, None))

    budget = audit_budget(knobs, memory)
    LOG.info(
        "store_memory_check worker=%d used_memory=%d maxmemory=%d maxmemory_policy=%s "
        "evicted_keys=%d audit_budget_bytes=%s audit_budget_source=%s",
        worker,
        memory.used,
        memory.maxmemory,
        memory.policy or "unknown",
        memory.evicted_keys,
        budget.budget_bytes,
        budget.source,
    )
    if memory.policy_unsafe:
        LOG.error(
            "store_policy_unsafe worker=%d maxmemory_policy=%s: an evicting policy removes the "
            "guard-owner registrations first (they are the only TTL keys), so discovery reads "
            "0/N and new gateways never become ready, and once the store is full every write is "
            "refused INCLUDING the control plane's publishes -- a kill switch then commits to "
            "Postgres and never reaches the fleet. Set maxmemory-policy=noeviction AND keep "
            "audit inside its budget; noeviction alone does not fix it, because the heartbeats "
            "are refused and the registrations expire instead",
            worker,
            memory.policy,
        )
    if memory.maxmemory == 0:
        LOG.warning(
            "store_memory_unbounded worker=%d: the store's maxmemory is 0 (no limit), so audit "
            "can grow until the host does. Set a maxmemory with noeviction, or set "
            "AMF_AUDIT_STORE_BUDGET_MB explicitly",
            worker,
        )
    for warning in (*knobs.warnings, *budget.warnings):
        LOG.warning("store_memory_check worker=%d %s", worker, warning)
    return StartupCheck(memory, budget)


class MemorySampler:
    """Read the store's memory posture every period, off the request path. Worker 0 only.

    Two responsibilities, deliberately not three: it publishes a reading through `on_sample`,
    and it hands the same reading to `on_memory` so the audit budget follows a store resize
    without anyone restarting the fleet. It does not trim, and it does not decide anything.

    A failing sample is counted and logged ONCE per run of failures, and never propagates: this
    task shares a task group with the audit writer, so an exception here would cancel the
    writer, and losing audit to protect a gauge is the wrong trade.
    """

    def __init__(
        self,
        client: Any,
        knobs: AuditMemoryKnobs,
        *,
        on_sample: Callable[[StoreMemory], None] | None = None,
        on_memory: Callable[[StoreMemory], None] | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._client = client
        self._period_s = knobs.sample_s
        self._on_sample = on_sample
        self._on_memory = on_memory
        self._sleep = sleep
        self._errors = 0
        self._failing = False

    @property
    def errors(self) -> int:
        """How many samples failed. A rising count with a healthy store is a client problem."""
        return self._errors

    @property
    def period_s(self) -> float:
        return self._period_s

    async def sample_once(self) -> StoreMemory | None:
        """One reading. Returns None on failure, having counted it."""
        try:
            memory = await read_memory(self._client)
        except Exception as exc:  # counted, logged once per run, never raised
            self._errors += 1
            if not self._failing:
                LOG.warning(
                    "store_memory_sample_failed error=%s (further failures in this run are "
                    "counted in store_memory_sample_errors without a line each)",
                    f"{type(exc).__name__}: {exc}"[:300],
                )
            self._failing = True
            return None
        if self._failing:
            LOG.info("store_memory_sample recovered after %d failed samples", self._errors)
        self._failing = False
        if self._on_sample is not None:
            self._on_sample(memory)
        if self._on_memory is not None:
            self._on_memory(memory)
        return memory

    async def run(self) -> None:
        """Sample forever. Sleeps FIRST, so start-up's own check is not immediately repeated."""
        while True:
            await self._sleep(self._period_s)
            try:
                await self.sample_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # a sampler bug must never stop the writer beside it
                LOG.warning(
                    "store_memory_sampler_error error=%s",
                    f"{type(exc).__name__}: {exc}"[:300],
                )
