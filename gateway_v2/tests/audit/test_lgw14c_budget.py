"""GW14c phase 3: one byte budget divided into per-org stream lengths.

The property under test is the one RC2's `MAXLEN 2,000,000` did not have: **each tenant holds an
equal share of the BYTES, not an equal number of records.** A record-count cap divided among
tenants whose records differ in size gives the tenant with the biggest records the biggest share
of the store, which is how a cap that looked present was absent.

Also asserted here, because each has a measured cost behind it:

* the clamps, so a huge estate cannot trim history to nothing and a tiny one cannot exceed the
  per-org upper bound;
* that a resolved budget always produces a cap, and an unresolved one still produces the per-org
  upper bound rather than "unbounded";
* that the size estimate is an over-estimate, since the direction of the error decides whether a
  mistake costs headroom or costs the store;
* that the per-org size table cannot grow without a ceiling.

The model's constants are checked against real Valkey 8 measurements in
`tests/runtime/test_lgw14c_live_docker.py`; nothing here takes 1.10 and 256 on trust.
"""

from __future__ import annotations

import pytest

from gateway_v2.audit.budget import (
    ENTRY_BYTES,
    ENTRY_FACTOR,
    MAX_TRACKED_ORGS,
    SIZE_ALPHA,
    BudgetReading,
    StreamBudget,
)
from gateway_v2.domain.audit_knobs import MIB, AuditMemoryKnobs
from gateway_v2.runtime.storemem import BudgetDecision, StoreMemory, audit_budget

RECORD = 2_900
"""The record size round 2 measured, in bytes. Used so the numbers here are the real ones."""


def _budget(
    *,
    budget_mb: float | None = 32,
    tenants: int = 1,
    stream_maxlen: int = 2_000_000,
    min_retain: int = 100,
) -> StreamBudget:
    knobs = AuditMemoryKnobs(
        budget_mb=budget_mb, stream_maxlen=stream_maxlen, min_retain=min_retain,
    )
    return StreamBudget(knobs, lambda: tenants)


def _settle(budget: StreamBudget, org: str, size: int, rounds: int = 2_000) -> None:
    """Drive the EWMA to a steady state, as a tenant's own traffic would."""
    for _ in range(rounds):
        budget.observe(org, size)


# --- the property that was missing ----------------------------------------------------------------


def test_each_tenant_gets_an_equal_byte_share_whatever_its_record_size() -> None:
    """The core of R2-05. A record-count cap gives the tenant with the largest records the
    largest share of the store; a byte budget must not."""
    budget = _budget(budget_mb=64, tenants=2)
    _settle(budget, "small", 300)
    _settle(budget, "large", 3_000)

    small_bytes = budget.maxlen("small") * budget.bytes_per_record("small")
    large_bytes = budget.maxlen("large") * budget.bytes_per_record("large")

    assert budget.maxlen("small") > budget.maxlen("large"), "more records, since they are smaller"
    # Equal byte shares, within the integer truncation of one record each.
    assert small_bytes == pytest.approx(large_bytes, rel=0.01)


def test_the_shares_add_up_to_the_budget() -> None:
    """Every tenant at its cap must fit inside the budget, which is the whole claim."""
    tenants = 4
    budget = _budget(budget_mb=64, tenants=tenants)
    for index in range(tenants):
        _settle(budget, f"org-{index}", 300 * (index + 1))

    total = sum(
        budget.maxlen(f"org-{index}") * budget.bytes_per_record(f"org-{index}")
        for index in range(tenants)
    )

    assert total <= 64 * MIB


def test_idle_tenants_reserve_a_share() -> None:
    """Conservative on purpose: a cap that expands while tenants are quiet has to contract -- by
    trimming -- exactly when traffic arrives and the store is under most pressure."""
    one = _budget(budget_mb=64, tenants=1)
    many = _budget(budget_mb=64, tenants=100)
    _settle(one, "org-a", RECORD)
    _settle(many, "org-a", RECORD)

    assert many.maxlen("org-a") * 100 == pytest.approx(one.maxlen("org-a"), rel=0.01)


def test_the_measured_case_lands_where_the_reference_measured_it() -> None:
    """64 MB budget, 2.9 KB records: the configuration the F-AUDIT-MEM run passed at."""
    budget = _budget(budget_mb=64, tenants=2)
    _settle(budget, "org-a", RECORD)
    _settle(budget, "org-b", RECORD)

    per_record = budget.bytes_per_record("org-a")

    # The reference's model charged 3,445 B here against a measured 3,092 B. This one charges
    # more, on purpose: re-measuring across a wider range of payload sizes showed 1.10 UNDER-
    # charging at 2 KB, 4 KB and 8 KB, where the allocator's size classes push the real cost to
    # ~1.25x the payload. See ENTRY_FACTOR for the measurements.
    assert per_record == pytest.approx(2_900 * ENTRY_FACTOR + ENTRY_BYTES, rel=0.001)
    assert per_record > 3_445, "the corrected model must not be below the one it replaces"
    assert budget.maxlen("org-a") == int(64 * MIB / (2 * per_record))


# --- the clamps -----------------------------------------------------------------------------------


def test_a_huge_estate_cannot_trim_history_to_nothing() -> None:
    """Approximate XTRIM removes whole nodes, so trimming towards single digits buys nothing and
    costs the only recent history an operator has while diagnosing a full store."""
    budget = _budget(budget_mb=1, tenants=1_000_000, min_retain=100)
    _settle(budget, "org-a", RECORD)

    assert budget.maxlen("org-a") == 100


def test_a_tiny_estate_cannot_exceed_the_per_org_upper_bound() -> None:
    budget = _budget(budget_mb=100_000, tenants=1, stream_maxlen=5_000)
    _settle(budget, "org-a", 10)

    assert budget.maxlen("org-a") == 5_000


# --- a cap always exists --------------------------------------------------------------------------


def test_an_unresolved_budget_still_yields_the_per_org_upper_bound() -> None:
    """RC2's behaviour, and a bound in name only -- which is why `audit_budget` returns a warning
    next to it rather than letting it pass as a configuration."""
    budget = StreamBudget(AuditMemoryKnobs(stream_maxlen=2_000_000), lambda: 3)

    assert budget.budget_bytes is None
    assert budget.maxlen("org-a") == 2_000_000


def test_an_org_with_no_observed_records_still_gets_a_cap() -> None:
    """The first batch of a new tenant is appended and trimmed like any other."""
    budget = _budget(budget_mb=64, tenants=2)
    _settle(budget, "known", RECORD)

    assert budget.maxlen("unseen") >= 100
    assert budget.bytes_per_record("unseen") == budget.bytes_per_record("known")


def test_maxlens_answers_for_exactly_the_orgs_a_batch_touched() -> None:
    budget = _budget(budget_mb=64, tenants=3)

    caps = budget.maxlens(["org-a", "org-b"])

    assert sorted(caps) == ["org-a", "org-b"]
    assert all(value > 0 for value in caps.values())


def test_a_resolved_budget_is_adopted_so_a_store_resize_is_followed() -> None:
    knobs = AuditMemoryKnobs(fraction=0.5)
    budget = StreamBudget(knobs, lambda: 1)
    _settle(budget, "org-a", RECORD)
    assert budget.budget_bytes is None

    budget.adopt(audit_budget(knobs, StoreMemory(0, 64 * MIB, "noeviction", 0)))
    first = budget.maxlen("org-a")
    budget.adopt(audit_budget(knobs, StoreMemory(0, 256 * MIB, "noeviction", 0)))
    second = budget.maxlen("org-a")

    assert budget.budget_bytes == 128 * MIB
    assert second == pytest.approx(first * 4, rel=0.01)
    assert "maxmemory" in budget.budget_source


def test_an_explicit_budget_is_live_before_the_first_sample() -> None:
    """A worker must not write its first batch unbounded while waiting for a memory reading."""
    budget = StreamBudget(AuditMemoryKnobs(budget_mb=16), lambda: 1)

    assert budget.budget_bytes == 16 * MIB
    assert budget.maxlen("org-a") < 2_000_000


# --- the size estimate ----------------------------------------------------------------------------


def test_the_estimate_over_states_the_payload() -> None:
    """Over-estimate and the budget holds with headroom; under-estimate and the store fills while
    every gauge says the bound is being honoured."""
    budget = _budget()
    _settle(budget, "org-a", 1_000)

    estimate = budget.bytes_per_record("org-a")

    assert estimate > 1_000
    assert estimate == pytest.approx(1_000 * ENTRY_FACTOR + ENTRY_BYTES, rel=0.001)


def test_the_first_record_sets_the_estimate_outright() -> None:
    """An EWMA seeded at zero would make the first batch of a new tenant enormously over-capped."""
    budget = _budget()
    budget.observe("org-a", 4_000)

    assert budget.bytes_per_record("org-a") == pytest.approx(4_000 * ENTRY_FACTOR + ENTRY_BYTES)


def test_the_estimate_absorbs_only_its_share_of_an_outlier() -> None:
    """The quantity is this tenant's traffic shape, not its last request. One 100x record must
    move the estimate by its EWMA weight, not towards its own size: otherwise a single unusual
    record shrinks the org's MAXLEN by an order of magnitude and trims history that did not
    need to go."""
    budget = _budget()
    budget.observe("org-a", 1_000)
    budget.observe("org-a", 100_000)

    settled = budget.bytes_per_record("org-a")
    outlier_cost = 100_000 * ENTRY_FACTOR + ENTRY_BYTES

    assert SIZE_ALPHA == 0.02
    # It took exactly alpha of the jump...
    assert settled == pytest.approx(
        (1_000 + (100_000 - 1_000) * SIZE_ALPHA) * ENTRY_FACTOR + ENTRY_BYTES, rel=0.001,
    )
    # ...so it stays nowhere near the outlier's own cost.
    assert settled < outlier_cost * 0.05


def test_the_estimate_converges_on_a_changed_record_shape() -> None:
    budget = _budget()
    _settle(budget, "org-a", 500)
    _settle(budget, "org-a", 5_000, rounds=5_000)

    assert budget.bytes_per_record("org-a") == pytest.approx(
        5_000 * ENTRY_FACTOR + ENTRY_BYTES, rel=0.01,
    )


def test_a_negative_payload_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="negative size"):
        _budget().observe("org-a", -1)


# --- the table cannot grow without a ceiling ------------------------------------------------------


def test_the_per_org_table_stops_growing_at_the_ceiling() -> None:
    budget = StreamBudget(AuditMemoryKnobs(budget_mb=64), lambda: 4, max_tracked_orgs=4)
    for index in range(50):
        budget.observe(f"org-{index}", 1_000)

    assert budget.tracked_orgs == 4


def test_an_org_beyond_the_ceiling_is_sized_by_the_largest_estimate() -> None:
    """Fails towards a SMALLER cap, so an unknown tenant uses less of the store, not more."""
    budget = StreamBudget(AuditMemoryKnobs(budget_mb=64), lambda: 4, max_tracked_orgs=2)
    _settle(budget, "small", 200)
    _settle(budget, "big", 9_000)
    budget.observe("overflow", 50)

    assert budget.tracked_orgs == 2
    assert budget.bytes_per_record("overflow") == budget.bytes_per_record("big")
    assert budget.maxlen("overflow") <= budget.maxlen("small")


def test_the_ceiling_default_is_above_the_tenant_scale_target() -> None:
    """R2-02's gate is 25,000 tenants; the ceiling must not be the thing that engages first."""
    assert MAX_TRACKED_ORGS >= 50_000


# --- the reading a scrape takes -------------------------------------------------------------------


def test_the_reading_publishes_the_most_constrained_stream_and_the_largest_record() -> None:
    budget = _budget(budget_mb=64, tenants=2)
    _settle(budget, "small", 300)
    _settle(budget, "large", 3_000)

    reading = budget.reading(["small", "large"])

    assert reading.budget_bytes == 64 * MIB
    assert reading.tenants == 2
    assert reading.tracked_orgs == 2
    assert reading.min_stream_maxlen == budget.maxlen("large")
    assert reading.max_bytes_per_record == budget.bytes_per_record("large")


def test_the_reading_holds_no_tenant_identity() -> None:
    """R2-10: a full metric directory made 40% of new tenants' first requests return HTTP 500, so
    a per-tenant series must not be reachable from the metrics path at all."""
    budget = _budget(budget_mb=64, tenants=2)
    _settle(budget, "org-secret-name", 300)

    reading = budget.reading(["org-secret-name"])
    keys = set(reading.as_mapping())

    # A FIXED key set: five scalars, the same five on an estate of one tenant and of 25,000.
    assert keys == {
        "budget_bytes",
        "tenants",
        "tracked_orgs",
        "min_stream_maxlen",
        "max_bytes_per_record",
    }
    # And no tenant IDENTITY anywhere in it -- `tracked_orgs` is a count, not a name.
    assert not any("org-secret-name" in key for key in keys)
    crowded = _budget(budget_mb=64, tenants=2).reading([f"t{n}" for n in range(500)])
    assert set(crowded.as_mapping()) == keys


def test_an_empty_reading_is_zeroed_rather_than_absent() -> None:
    """A series that appears and disappears cannot be alarmed on or rate-computed."""
    reading = _budget().reading([])

    assert reading.min_stream_maxlen == 0
    assert reading.max_bytes_per_record == 0.0
    assert BudgetReading().as_mapping()["tenants"] == 1.0


def test_an_unresolved_budget_reads_as_zero_not_as_a_missing_series() -> None:
    budget = StreamBudget(AuditMemoryKnobs(), lambda: 1)

    assert budget.reading(["org-a"]).budget_bytes == 0


def test_adopting_an_unresolved_decision_clears_the_budget() -> None:
    budget = _budget(budget_mb=32)
    budget.adopt(BudgetDecision(None, "store went away"))

    assert budget.budget_bytes is None
    assert budget.maxlen("org-a") == 2_000_000
