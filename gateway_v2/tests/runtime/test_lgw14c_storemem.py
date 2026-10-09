"""GW14c phase 1: the store-memory posture check and the audit budget arithmetic.

What these assert, and why each one is not cosmetic:

* **Every evicting policy is reported, not just the default.** Round 2 measured `volatile-lru`
  (Memorystore's default) evicting the guard-owner registrations, and `noeviction` failing a
  different way. The check has to treat the whole `volatile-*` / `allkeys-*` family as one
  condition, so the test enumerates all eight policies rather than the one that was measured.
* **`INFO` is the source, `CONFIG GET` only the fallback.** Memorystore restricts `CONFIG` to
  clients. A policy check that only works on a local Redis is a check that is absent in
  production, so both paths are driven here.
* **An unreachable store is never fatal.** Refusing to start on a store blip converts it into a
  fleet outage; the budget falls back to the per-org cap with a warning instead.
* **"Unknown budget" is not "unlimited".** `maxmemory=0` returns no budget AND a warning,
  because that configuration is exactly what R2-05 measured filling a 10.4 GiB store in 51
  minutes.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Mapping
from typing import Any

import pytest

from gateway_v2.domain.audit_knobs import (
    DEFAULT_FRACTION,
    DEFAULT_SAMPLE_S,
    DEFAULT_STREAM_MAXLEN,
    MIB,
    AuditMemoryKnobs,
    knobs_from_env,
)
from gateway_v2.runtime.storemem import (
    BUDGET_HEADROOM,
    MemorySampler,
    StoreMemory,
    audit_budget,
    policy_unsafe,
    read_memory,
    startup_check,
)

EVICTING = (
    "volatile-lru",
    "volatile-lfu",
    "volatile-random",
    "volatile-ttl",
    "allkeys-lru",
    "allkeys-lfu",
    "allkeys-random",
)
NON_EVICTING = ("noeviction",)


def _run[T](coro: Awaitable[T]) -> T:
    return asyncio.run(coro)  # type: ignore[arg-type]


class FakeStore:
    """The two `INFO` sections and `CONFIG GET`, with the knobs a test needs to break them."""

    def __init__(
        self,
        *,
        used: int = 10 * MIB,
        maxmemory: int = 64 * MIB,
        policy: str | None = "noeviction",
        evicted_keys: int = 0,
        config_policy: str | None = None,
        byte_keys: bool = False,
        fail: Exception | None = None,
        config_fail: Exception | None = None,
    ) -> None:
        self.used = used
        self.maxmemory = maxmemory
        self.policy = policy
        self.evicted_keys = evicted_keys
        self.config_policy = config_policy
        self.byte_keys = byte_keys
        self.fail = fail
        self.config_fail = config_fail
        self.info_calls: list[str] = []
        self.config_calls: list[str] = []

    def _wrap(self, mapping: Mapping[str, object]) -> dict[Any, object]:
        if not self.byte_keys:
            return dict(mapping)
        return {key.encode("ascii"): value for key, value in mapping.items()}

    async def info(self, section: str) -> dict[Any, object]:
        self.info_calls.append(section)
        if self.fail is not None:
            raise self.fail
        if section == "memory":
            memory: dict[str, object] = {"used_memory": self.used, "maxmemory": self.maxmemory}
            if self.policy is not None:
                memory["maxmemory_policy"] = self.policy
            return self._wrap(memory)
        if section == "stats":
            return self._wrap({"evicted_keys": self.evicted_keys})
        return {}

    async def config_get(self, pattern: str) -> dict[Any, object]:
        self.config_calls.append(pattern)
        if self.config_fail is not None:
            raise self.config_fail
        if self.config_policy is None:
            return {}
        return self._wrap({"maxmemory-policy": self.config_policy})


# --- the policy self-check -----------------------------------------------------------------------


@pytest.mark.parametrize("policy", EVICTING)
def test_every_evicting_policy_is_unsafe(policy: str) -> None:
    """One condition, not one special case. `volatile-*` takes the registrations (the only TTL
    keys); `allkeys-*` can take the published state itself."""
    assert policy_unsafe(policy) is True


@pytest.mark.parametrize("policy", NON_EVICTING)
def test_noeviction_is_not_unsafe(policy: str) -> None:
    assert policy_unsafe(policy) is False


def test_unreadable_policy_is_not_reported_as_unsafe() -> None:
    """A gauge must not mean "evicts" and "could not tell" at once -- an alarm cannot act on it.

    Unreadable is reported by the check returning no memory reading, separately.
    """
    assert policy_unsafe("") is False


def test_startup_check_logs_error_and_remediation_for_an_evicting_policy(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeStore(policy="volatile-lru")
    with caplog.at_level(logging.INFO, logger="amf.audit.storemem"):
        found = _run(startup_check(store, AuditMemoryKnobs(), worker=3))

    assert found.reachable
    assert found.memory is not None
    assert found.memory.policy_unsafe is True
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert "store_policy_unsafe" in message
    assert "worker=3" in message
    # The remediation has to travel WITH the finding: noeviction alone does not fix it, and an
    # operator reading the gauge in isolation cannot know that.
    assert "noeviction" in message
    assert "noeviction alone does not fix it" in message


def test_startup_check_is_quiet_about_policy_when_the_store_is_safe(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeStore(policy="noeviction")
    with caplog.at_level(logging.INFO, logger="amf.audit.storemem"):
        found = _run(startup_check(store, AuditMemoryKnobs(budget_mb=8), worker=0))

    assert found.memory is not None
    assert found.memory.policy_unsafe is False
    assert [record for record in caplog.records if record.levelno >= logging.ERROR] == []
    assert any("store_memory_check" in record.getMessage() for record in caplog.records)


# --- INFO parsing, and the CONFIG fallback -------------------------------------------------------


def test_read_memory_uses_info_and_never_needs_config() -> None:
    """`INFO memory` carries `maxmemory_policy`, which is what makes this work on Memorystore."""
    store = FakeStore(used=5 * MIB, maxmemory=64 * MIB, policy="allkeys-lru", evicted_keys=17)
    memory = _run(read_memory(store))

    assert memory == StoreMemory(
        used=5 * MIB, maxmemory=64 * MIB, policy="allkeys-lru", evicted_keys=17,
    )
    assert store.info_calls == ["memory", "stats"]
    assert store.config_calls == []


def test_read_memory_falls_back_to_config_get_when_info_omits_the_policy() -> None:
    store = FakeStore(policy=None, config_policy="volatile-ttl")
    memory = _run(read_memory(store))

    assert memory.policy == "volatile-ttl"
    assert store.config_calls == ["maxmemory-policy"]


def test_read_memory_tolerates_a_restricted_config_command() -> None:
    """Memorystore refuses `CONFIG`. The reading is still useful without the policy."""
    store = FakeStore(policy=None, config_fail=RuntimeError("unknown command 'CONFIG'"))
    memory = _run(read_memory(store))

    assert memory.policy == ""
    assert memory.policy_unsafe is False
    assert memory.used > 0


def test_read_memory_tolerates_byte_keys() -> None:
    """A `decode_responses=False` client hands back bytes keys. A check that silently reads
    nothing on half the client configurations is worse than no check."""
    store = FakeStore(used=3 * MIB, maxmemory=64 * MIB, policy="volatile-lru", byte_keys=True)
    memory = _run(read_memory(store))

    assert memory.used == 3 * MIB
    assert memory.maxmemory == 64 * MIB
    assert memory.policy == "volatile-lru"


def test_used_ratio_is_zero_when_there_is_no_limit() -> None:
    assert StoreMemory(used=99, maxmemory=0, policy="noeviction", evicted_keys=0).used_ratio == 0.0


# --- an unreachable store is never fatal ---------------------------------------------------------


def test_startup_check_on_an_unreachable_store_warns_and_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeStore(fail=OSError("connection refused"))
    with caplog.at_level(logging.INFO, logger="amf.audit.storemem"):
        found = _run(startup_check(store, AuditMemoryKnobs(), worker=1))

    assert found.reachable is False
    assert found.memory is None
    assert found.budget.known is False
    assert any("store_memory_check_failed" in r.getMessage() for r in caplog.records)
    # Refusing to start here would turn a store blip into a fleet outage.
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


# --- the budget ----------------------------------------------------------------------------------


def test_explicit_budget_wins_over_the_fraction() -> None:
    knobs = AuditMemoryKnobs(budget_mb=32, fraction=0.5)
    decision = audit_budget(knobs, StoreMemory(0, 1024 * MIB, "noeviction", 0))

    assert decision.budget_bytes == 32 * MIB
    assert decision.source == "AMF_AUDIT_STORE_BUDGET_MB"
    assert decision.warnings == ()


def test_budget_derives_from_the_fraction_of_maxmemory() -> None:
    decision = audit_budget(
        AuditMemoryKnobs(fraction=0.5), StoreMemory(0, 64 * MIB, "noeviction", 0),
    )

    assert decision.budget_bytes == 32 * MIB
    assert "AMF_AUDIT_STORE_FRACTION=0.5" in decision.source
    assert decision.known is True


def test_an_unlimited_store_yields_no_budget_and_says_so() -> None:
    """Unknown is not unlimited: the per-org cap is then the only bound, which IS the RC2
    configuration that filled a 10.4 GiB store in 51 minutes."""
    decision = audit_budget(AuditMemoryKnobs(), StoreMemory(0, 0, "noeviction", 0))

    assert decision.budget_bytes is None
    assert decision.known is False
    assert decision.warnings
    assert "stream_maxlen" in decision.warnings[0]


def test_an_unreachable_store_yields_no_budget() -> None:
    assert audit_budget(AuditMemoryKnobs(), None).budget_bytes is None


def test_an_oversized_explicit_budget_warns() -> None:
    """Above 90% of maxmemory there is nothing left for the state or the registrations."""
    maxmemory = 64 * MIB
    over = int(maxmemory * BUDGET_HEADROOM / MIB) + 1
    decision = audit_budget(
        AuditMemoryKnobs(budget_mb=over), StoreMemory(0, maxmemory, "noeviction", 0),
    )

    assert decision.budget_bytes is not None
    assert decision.warnings
    assert "maxmemory" in decision.warnings[0]


def test_a_budget_inside_the_headroom_does_not_warn() -> None:
    decision = audit_budget(
        AuditMemoryKnobs(budget_mb=32), StoreMemory(0, 64 * MIB, "noeviction", 0),
    )

    assert decision.warnings == ()


# --- knob validation ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"budget_mb": 0}, "budget_mb must be positive"),
        ({"budget_mb": -1}, "budget_mb must be positive"),
        ({"fraction": 0}, "fraction must be in"),
        ({"fraction": 0.95}, "fraction must be in"),
        ({"fraction": 1.0}, "fraction must be in"),
        ({"sample_s": 0}, "sample_s must be positive"),
        ({"sample_s": -1}, "sample_s must be positive"),
        ({"stream_maxlen": 0}, "stream_maxlen must be positive"),
        ({"min_retain": 0}, "min_retain must be positive"),
        ({"min_retain": 10, "stream_maxlen": 5}, "must not exceed"),
    ],
)
def test_knob_relationships_fail_at_construction(
    kwargs: dict[str, float], expected: str,
) -> None:
    """Fail-at-START, like `StateKnobs`. Each value is individually reasonable; the pair is not."""
    with pytest.raises(ValueError, match=expected):
        AuditMemoryKnobs(**kwargs)  # type: ignore[arg-type]


def test_defaults_are_the_cards_defaults() -> None:
    knobs = AuditMemoryKnobs()

    assert knobs.budget_mb is None
    assert knobs.fraction == DEFAULT_FRACTION == 0.5
    assert knobs.sample_s == DEFAULT_SAMPLE_S == 10.0
    assert knobs.stream_maxlen == DEFAULT_STREAM_MAXLEN == 2_000_000
    assert knobs.explicit_budget_bytes is None


def test_no_explicit_budget_warns_about_shared_stores() -> None:
    """The fraction is per gateway fleet, so several lanes on one Valkey each claim half of it."""
    assert any("SHARED" in warning for warning in AuditMemoryKnobs().warnings)
    assert AuditMemoryKnobs(budget_mb=16).warnings == ()


# --- the environment ------------------------------------------------------------------------------


def test_knobs_from_env_reads_the_amf_names() -> None:
    knobs = knobs_from_env(
        {
            "AMF_AUDIT_STORE_BUDGET_MB": "48",
            "AMF_AUDIT_STORE_FRACTION": "0.25",
            "AMF_STORE_MEMORY_SAMPLE_S": "5",
            "AMF_AUDIT_STREAM_MAXLEN": "1000",
        },
    )

    assert knobs.budget_mb == 48
    assert knobs.fraction == 0.25
    assert knobs.sample_s == 5
    assert knobs.stream_maxlen == 1000


def test_knobs_from_env_accepts_the_runbooks_rv_names() -> None:
    """The card and the reference patch name `RV_AUDIT_*`, and operators have those written down."""
    knobs = knobs_from_env(
        {"RV_AUDIT_STORE_BUDGET_MB": "16", "RV_AUDIT_STORE_FRACTION": "0.4"},
    )

    assert knobs.budget_mb == 16
    assert knobs.fraction == 0.4


def test_the_amf_name_wins_when_both_are_set() -> None:
    knobs = knobs_from_env(
        {"AMF_AUDIT_STORE_BUDGET_MB": "64", "RV_AUDIT_STORE_BUDGET_MB": "8"},
    )

    assert knobs.budget_mb == 64


def test_blank_and_absent_are_the_same_as_unset() -> None:
    assert knobs_from_env({"AMF_AUDIT_STORE_BUDGET_MB": "   "}).budget_mb is None
    assert knobs_from_env({}).budget_mb is None


def test_knobs_from_env_names_the_variable_it_could_not_parse() -> None:
    with pytest.raises(ValueError, match="AMF_AUDIT_STORE_FRACTION must be a number"):
        knobs_from_env({"AMF_AUDIT_STORE_FRACTION": "half"})
    with pytest.raises(ValueError, match="AMF_AUDIT_STREAM_MAXLEN must be an integer"):
        knobs_from_env({"AMF_AUDIT_STREAM_MAXLEN": "lots"})


def test_an_env_budget_of_zero_is_refused_rather_than_treated_as_unset() -> None:
    """`=0` is a configuration mistake, not "derive it": it would trim everything to min_retain."""
    with pytest.raises(ValueError, match="budget_mb must be positive"):
        knobs_from_env({"AMF_AUDIT_STORE_BUDGET_MB": "0"})


# --- the sampler ----------------------------------------------------------------------------------


def test_sampler_publishes_a_reading_and_offers_it_to_the_budget() -> None:
    store = FakeStore(used=7 * MIB, maxmemory=64 * MIB, policy="noeviction")
    published: list[StoreMemory] = []
    rederived: list[StoreMemory] = []
    sampler = MemorySampler(
        store,
        AuditMemoryKnobs(),
        on_sample=published.append,
        on_memory=rederived.append,
    )

    memory = _run(sampler.sample_once())

    assert memory is not None
    assert published == [memory]
    # The same reading drives the budget, so a store resize is followed without a restart.
    assert rederived == [memory]
    assert sampler.errors == 0


def test_a_failing_sample_is_counted_and_logged_once_per_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FakeStore(fail=OSError("no route to host"))
    sampler = MemorySampler(store, AuditMemoryKnobs())

    with caplog.at_level(logging.INFO, logger="amf.audit.storemem"):
        assert _run(sampler.sample_once()) is None
        assert _run(sampler.sample_once()) is None
        assert _run(sampler.sample_once()) is None

    assert sampler.errors == 3
    failures = [r for r in caplog.records if "store_memory_sample_failed" in r.getMessage()]
    assert len(failures) == 1, "a run of failures is one line plus a counter, not one line each"


def test_recovery_after_failures_is_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    store = FakeStore(fail=OSError("down"))
    sampler = MemorySampler(store, AuditMemoryKnobs())
    _run(sampler.sample_once())
    store.fail = None

    with caplog.at_level(logging.INFO, logger="amf.audit.storemem"):
        assert _run(sampler.sample_once()) is not None

    assert any("recovered after 1 failed samples" in r.getMessage() for r in caplog.records)


def test_the_budget_follows_a_store_resize() -> None:
    store = FakeStore(maxmemory=64 * MIB, policy="noeviction")
    knobs = AuditMemoryKnobs(fraction=0.5)
    budgets: list[int | None] = []
    sampler = MemorySampler(
        store, knobs, on_memory=lambda mem: budgets.append(audit_budget(knobs, mem).budget_bytes),
    )

    _run(sampler.sample_once())
    store.maxmemory = 256 * MIB
    _run(sampler.sample_once())

    assert budgets == [32 * MIB, 128 * MIB]


def test_run_sleeps_before_the_first_sample_and_survives_a_sampler_bug() -> None:
    """Start-up already read the posture, so repeating it immediately buys nothing. And a bug in
    the callback must not cancel the audit writer sharing this task group."""
    store = FakeStore()
    order: list[str] = []

    def explode(_: StoreMemory) -> None:
        order.append("sampled")
        raise ZeroDivisionError("a bug in a gauge")

    async def sleep(_: float) -> None:
        order.append("slept")
        if len(order) > 3:
            raise asyncio.CancelledError

    sampler = MemorySampler(store, AuditMemoryKnobs(), on_sample=explode, sleep=sleep)

    with pytest.raises(asyncio.CancelledError):
        _run(sampler.run())

    assert order[0] == "slept"
    assert order.count("sampled") >= 1, "the loop kept running after the callback raised"


def test_cancellation_propagates_out_of_run() -> None:
    """The sampler lives in the writer's task group and must die with it."""
    store = FakeStore()

    async def drive() -> None:
        sampler = MemorySampler(store, AuditMemoryKnobs(sample_s=0.001))
        task = asyncio.create_task(sampler.run())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    _run(drive())
