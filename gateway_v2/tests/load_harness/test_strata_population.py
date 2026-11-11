"""Key/org/tenant population floor tests (B6 / R2-21), task 4.8.

Covers the pure multi-key and tenant-scale stratum-population modules only (no I/O, no store, no
event loop): ``gateway_v2.load_harness.strata.keys`` and
``gateway_v2.load_harness.strata.tenants``.

* **Property 5 — key/org/tenant populations honor their floors** (task 4.8): for any enabled
  multi-key / tenant config, ``assign`` draws ONLY from the configured population
  (``key_id in [0, num_keys)``, ``org_id in [0, num_orgs)``, ``tenant_id in [0, num_tenants)``) and
  the distinct-count report never EXCEEDS the population and never falls below what the realized
  draws admitted — it equals the true distinct set of the draws. Over enough draws against a
  modest population the counts also approach the population (a sanity observation), but the
  invariant proved is the bounds. Driven by many seeds over many valid populations for >= 10,000
  total iterations.
* **Builder floor rejection** (examples): ``build_key_population`` raises ``ValueError`` below the
  key floor, below the org floor, when ``num_orgs > num_keys``, and above the key ceiling;
  ``build_tenant_population`` raises below the tenant floor.

House idiom: seeded ``random.Random`` loops of >= 10,000 iterations with the seed logged in the
assertion message; NO ``hypothesis``. Every draw is a pure function of ``(population, rng)`` keyed
by the injected ``random.Random``, so the machine is deterministic and offline-reproducible. Test
files are not under the import-linter layer contract.
"""

from __future__ import annotations

import random

import pytest

from load_harness.strata import keys, tenants

# Stratum-population floors/ceiling, read from the modules under test (NOT re-declared here) so the
# test stays pinned to the shipped policy constants rather than copying their numbers.
_MIN_KEYS = keys._MIN_KEYS
_MIN_ORGS = keys._MIN_ORGS
_MAX_KEYS = keys._MAX_KEYS
_MIN_TENANTS = tenants._MIN_TENANTS

# >= 10,000 total iterations across the two strata, split so each dimension carries the bulk of the
# budget on cheap at-floor-ish populations with a few boundary populations folded in.
_KEY_ITERATIONS = 6_000
_TENANT_ITERATIONS = 4_000


# --------------------------------------------------------------------------- #
# Task 4.8 — Property 5: key/org/tenant populations honor their floors
# Feature: load-harness-r2-21, Property 5: key/org/tenant populations honor their floors
# Validates: Requirements 3.1, 3.3, 3.4, 7.1, 7.3
# --------------------------------------------------------------------------- #


def _pick_key_population(rng: random.Random) -> tuple[int, int]:
    """A valid ``(num_keys, num_orgs)`` population honoring the floors/ceiling.

    The bulk of iterations use modest valid populations (cheap to draw against); a minority exercise
    the exact at-floor and at-ceiling boundaries so coverage includes the edges without making the
    draw loops large.
    """
    roll = rng.random()
    if roll < 0.1:
        # At the key floor, org floor exactly.
        return _MIN_KEYS, _MIN_ORGS
    if roll < 0.15:
        # At the key ceiling.
        return _MAX_KEYS, rng.randint(_MIN_ORGS, 1_000)
    if roll < 0.2:
        # Degenerate ownership edge: as many orgs as keys (every org owns exactly one key).
        n = rng.randint(_MIN_KEYS, 4_000)
        return n, n
    num_keys = rng.randint(_MIN_KEYS, 8_000)
    num_orgs = rng.randint(_MIN_ORGS, num_keys)
    return num_keys, num_orgs


def test_property5_key_population_honors_floors() -> None:
    """Key draws stay in range and distinct counts are bounded by population and == true set.

    For many seeds and many valid key populations: every drawn ``key_id`` lies in
    ``[0, num_keys)``, every ``org_id`` lies in ``[0, num_orgs)`` and matches the population's
    ownership rule, and ``distinct_counts`` equals the true distinct key/org sets of the realized
    draws while never exceeding the population.
    """
    seed = 0x05_1E
    rng = random.Random(seed)
    for i in range(_KEY_ITERATIONS):
        num_keys, num_orgs = _pick_key_population(rng)
        population = keys.build_key_population(num_keys, num_orgs)

        draws = rng.randint(1, 50)
        assignments = [keys.assign(population, rng) for _ in range(draws)]

        true_keys: set[int] = set()
        true_orgs: set[int] = set()
        for a in assignments:
            assert 0 <= a.key_id < num_keys, (
                f"seed={seed:#x} iter={i} key_id={a.key_id} outside [0,{num_keys})"
            )
            assert 0 <= a.org_id < num_orgs, (
                f"seed={seed:#x} iter={i} org_id={a.org_id} outside [0,{num_orgs})"
            )
            assert a.org_id == population.org_of(a.key_id), (
                f"seed={seed:#x} iter={i} org_id={a.org_id} != ownership "
                f"{population.org_of(a.key_id)} for key_id={a.key_id}"
            )
            true_keys.add(a.key_id)
            true_orgs.add(a.org_id)

        distinct_keys, distinct_orgs = keys.distinct_counts(assignments)
        # Report equals the true distinct set of the draws ...
        assert distinct_keys == len(true_keys), (
            f"seed={seed:#x} iter={i} distinct_keys={distinct_keys} != true {len(true_keys)}"
        )
        assert distinct_orgs == len(true_orgs), (
            f"seed={seed:#x} iter={i} distinct_orgs={distinct_orgs} != true {len(true_orgs)}"
        )
        # ... and never exceeds the configured population, never below what the draws admitted.
        assert 1 <= distinct_keys <= min(draws, num_keys), (
            f"seed={seed:#x} iter={i} distinct_keys={distinct_keys} outside "
            f"[1,{min(draws, num_keys)}]"
        )
        assert 1 <= distinct_orgs <= min(distinct_keys, num_orgs), (
            f"seed={seed:#x} iter={i} distinct_orgs={distinct_orgs} outside "
            f"[1,{min(distinct_keys, num_orgs)}]"
        )


def test_property5_key_population_approaches_population_with_many_draws() -> None:
    """Sanity: against a modest population, many draws exercise most of the key/org space.

    Not the core invariant (the bounds above are), but a convergence sanity check: at the key/org
    floor, drawing far more than the population size realizes nearly all distinct keys/orgs.
    """
    rng = random.Random(0x05_1E_C0)
    population = keys.build_key_population(_MIN_KEYS, _MIN_ORGS)
    assignments = [keys.assign(population, rng) for _ in range(20 * _MIN_KEYS)]
    distinct_keys, distinct_orgs = keys.distinct_counts(assignments)
    assert distinct_keys <= _MIN_KEYS and distinct_orgs <= _MIN_ORGS
    # Coupon-collector: 20x draws realizes the vast majority of the space.
    assert distinct_keys > int(0.9 * _MIN_KEYS), f"only {distinct_keys}/{_MIN_KEYS} keys exercised"
    assert distinct_orgs == _MIN_ORGS, f"only {distinct_orgs}/{_MIN_ORGS} orgs exercised"


def _pick_tenant_population(rng: random.Random) -> int:
    """A valid ``num_tenants`` honoring the tenant floor (with the exact floor folded in)."""
    if rng.random() < 0.15:
        return _MIN_TENANTS  # at the floor exactly
    return rng.randint(_MIN_TENANTS, 40_000)


def test_property5_tenant_population_honors_floor() -> None:
    """Tenant draws stay in range and distinct counts are bounded by population and == true set.

    For many seeds and many valid tenant populations: every drawn ``tenant_id`` lies in
    ``[0, num_tenants)`` and ``distinct_tenants`` / ``distinct_counts`` equals the true distinct
    tenant set of the realized draws while never exceeding the population.
    """
    seed = 0x05_7E
    rng = random.Random(seed)
    for i in range(_TENANT_ITERATIONS):
        num_tenants = _pick_tenant_population(rng)
        population = tenants.build_tenant_population(num_tenants)

        draws = rng.randint(1, 50)
        assignments = [tenants.assign(population, rng) for _ in range(draws)]

        true_tenants: set[int] = set()
        for a in assignments:
            assert 0 <= a.tenant_id < num_tenants, (
                f"seed={seed:#x} iter={i} tenant_id={a.tenant_id} outside [0,{num_tenants})"
            )
            true_tenants.add(a.tenant_id)

        reported = tenants.distinct_tenants(assignments)
        assert reported == len(true_tenants), (
            f"seed={seed:#x} iter={i} distinct_tenants={reported} != true {len(true_tenants)}"
        )
        # The two names agree (distinct_counts is an alias).
        assert tenants.distinct_counts(assignments) == reported, (
            f"seed={seed:#x} iter={i} distinct_counts != distinct_tenants"
        )
        assert 1 <= reported <= min(draws, num_tenants), (
            f"seed={seed:#x} iter={i} distinct_tenants={reported} outside "
            f"[1,{min(draws, num_tenants)}]"
        )


# --------------------------------------------------------------------------- #
# Task 4.8 — builder floor rejection (representative examples, not PBT)
# Feature: load-harness-r2-21, Property 5: key/org/tenant populations honor their floors
# Validates: Requirements 3.1, 3.3, 3.4, 7.1, 7.3
# --------------------------------------------------------------------------- #


def test_build_key_population_rejects_below_key_floor() -> None:
    """``num_keys`` below the ≥ 2,000 key floor is a hard ``ValueError``."""
    with pytest.raises(ValueError, match="multi-key stratum population range"):
        keys.build_key_population(_MIN_KEYS - 1, _MIN_ORGS)


def test_build_key_population_rejects_above_key_ceiling() -> None:
    """``num_keys`` above the ≤ 50,000 key ceiling is a hard ``ValueError``."""
    with pytest.raises(ValueError, match="multi-key stratum population range"):
        keys.build_key_population(_MAX_KEYS + 1, _MIN_ORGS)


def test_build_key_population_rejects_below_org_floor() -> None:
    """``num_orgs`` below the ≥ 200 org floor is a hard ``ValueError``."""
    with pytest.raises(ValueError, match="org floor"):
        keys.build_key_population(_MIN_KEYS, _MIN_ORGS - 1)


def test_build_key_population_rejects_more_orgs_than_keys() -> None:
    """``num_orgs > num_keys`` (an org owning no key) is a hard ``ValueError``."""
    with pytest.raises(ValueError, match="exceeds num_keys"):
        keys.build_key_population(_MIN_KEYS, _MIN_KEYS + 1)


def test_build_key_population_accepts_at_floor_and_ceiling() -> None:
    """The exact floor and ceiling populations build successfully (boundary inclusivity)."""
    at_floor = keys.build_key_population(_MIN_KEYS, _MIN_ORGS)
    assert at_floor.num_keys == _MIN_KEYS and at_floor.num_orgs == _MIN_ORGS
    at_ceiling = keys.build_key_population(_MAX_KEYS, _MIN_ORGS)
    assert at_ceiling.num_keys == _MAX_KEYS


def test_build_tenant_population_rejects_below_tenant_floor() -> None:
    """``num_tenants`` below the ≥ 10,000 tenant floor is a hard ``ValueError``."""
    with pytest.raises(ValueError, match="tenant-scale stratum population floor"):
        tenants.build_tenant_population(_MIN_TENANTS - 1)


def test_build_tenant_population_accepts_at_floor() -> None:
    """The exact tenant floor population builds successfully (boundary inclusivity)."""
    population = tenants.build_tenant_population(_MIN_TENANTS)
    assert population.num_tenants == _MIN_TENANTS
