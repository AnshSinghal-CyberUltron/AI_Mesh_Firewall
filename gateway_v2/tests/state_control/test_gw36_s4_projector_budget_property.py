"""GW36 S4 — property tests for the projector's BUDGET write + pool-seed path.

Two properties, one file (tasks 7.4 and 7.5), both in the house idiom: a seeded
``random.Random``, ``_ITERATIONS`` runs well above the >=100 floor, the seed echoed in every
assertion message, and no hypothesis.

Property 6 drives the REAL projector over the in-memory transactional twin (the exact ``Lab``
harness the S2 projector suite uses: ``MemoryControlDB`` + ``MemoryStore`` + ``MemorySource`` +
``StateWriter`` + ``round_once``) and reads each org's BUDGET record ``feed_seq`` -- the
Budget_Generation -- back out of the fake DB snapshot after every round.

Property 7 exercises the real ``ValkeyPoolSeeder`` against a sync ``fakeredis`` client, interleaving
seeds (matching and mismatched generations) with direct ``remaining`` decrements that stand in for a
live lease spending the pool.

The ``ValkeyPoolSeeder`` re-seed rule asserted here is read straight off the implementation
(``state_control/projector.py``): the seed compares the STORED ``:generation`` to the record's
``feed_seq`` for EQUALITY. ``stored == feed_seq`` is a no-op that returns ``True`` and leaves
``remaining`` untouched (so a live decrement is never clobbered -- Req 10.5); ANY inequality
(absent, higher, or lower/stale) re-seeds ``remaining = pool_tokens`` and ``generation = feed_seq``
and returns ``True``. It is an equality CAS, not a monotonic-advance CAS: a stale (lower) generation
still re-seeds because it is not equal. The property matches the code, not an idealised CAS.
"""

from __future__ import annotations

import json
import random

import fakeredis

from gateway_v2.domain.state import StateKind
from gateway_v2.domain.state_knobs import ProjectorKnobs
from gateway_v2.runtime.store_keys import KEYS
from state_control.db import MemoryControlDB
from state_control.projector import Projector, ValkeyPoolSeeder
from state_control.publisher import MemoryStore
from state_control.source import MemorySource
from state_control.writer import StateWriter
from tests.state_control.test_lgw05c_writer import SECRET

_ITERATIONS = 10_000
"""The house standard, above the >=100 floor the plan sets for a property test."""

KNOBS = ProjectorKnobs()
_MAX_BUDGET = 1_000_000_000


class _Clock:
    def __init__(self) -> None:
        self.now = 10_000.0

    def __call__(self) -> float:
        return self.now


def _budget_lab() -> tuple[Projector, MemorySource, MemoryControlDB, _Clock]:
    """The S2 ``Lab`` projector wiring, trimmed to what the budget phase needs."""
    clock = _Clock()
    db = MemoryControlDB(clock=clock)
    store = MemoryStore()
    source = MemorySource(clock=clock)
    writer = StateWriter(db, store, SECRET, actor="amf.projector:budget")
    projector = Projector(db, source, writer, holder="a", knobs=KNOBS)
    return projector, source, db, clock


def _budget_generation(db: MemoryControlDB, slug: str) -> int | None:
    """Read the org's BUDGET record ``feed_seq`` -- the Budget_Generation -- from the fake DB.

    Returns ``None`` when no BUDGET record exists for the org yet (no round has written one).
    """
    for record in db.snapshot(StateKind.BUDGET)[1]:
        if record.key == slug and not record.deleted:
            return record.feed_seq
    return None


def _budget_pool(db: MemoryControlDB, slug: str) -> int | None:
    for record in db.snapshot(StateKind.BUDGET)[1]:
        if record.key == slug and not record.deleted:
            body = json.loads(record.body)
            return int(body["pool_tokens"])
    return None


# Feature: gw36-s4-admission-quota, Property 6: Budget generation monotonicity
# Validates: Requirements 9.4, 9.5
def test_property_6_budget_generation_monotonicity() -> None:
    """For any sequence of control budget values an org passes through consecutive projector
    rounds, the written Budget_Generation (the record's ``feed_seq``) is non-decreasing across
    rounds and strictly increases on exactly the rounds where the budget body changed; an unchanged
    value writes no new record and does not advance the generation.

    **Validates: Requirements 9.4, 9.5**
    """
    seed = 0x3654_0006
    rng = random.Random(seed)
    for iteration in range(_ITERATIONS):
        projector, source, db, clock = _budget_lab()
        slug = "acme"
        source.org(slug)  # a budget is only projected for an ACTIVE org (desired_state gate)

        length = rng.randint(1, 8)
        # A sequence WITH repeats: draw over the full control range, then force some repeats so
        # the "unchanged round writes nothing" branch is exercised, not just the changing branch.
        values = [rng.randint(0, _MAX_BUDGET) for _ in range(length)]
        for i in range(1, length):
            if rng.random() < 0.4:
                values[i] = values[i - 1]

        where = f"seed={seed:#x} iteration={iteration} values={values}"
        last_gen: int | None = None
        last_value: int | None = None
        for value in values:
            source.budget(slug, value)
            clock.now += 1.0
            summary = projector.round_once()
            assert summary.complete, f"{where}: the round must complete"

            gen = _budget_generation(db, slug)
            assert gen is not None, f"{where}: a projected budget must have a generation"

            if last_gen is None:
                # First round: the org gets its first record and an initial generation (Req 9.3).
                assert _budget_pool(db, slug) == value, f"{where}: first pool must match"
            else:
                # Non-decreasing across rounds (Req 9.4).
                assert gen >= last_gen, (
                    f"{where}: generation went backwards {last_gen} -> {gen} for value={value}"
                )
                if value != last_value:
                    # A changed body strictly advances the generation (Req 9.4) and rewrites the
                    # stored pool.
                    assert gen > last_gen, (
                        f"{where}: generation did not advance on a changed budget "
                        f"{last_value} -> {value} (gen stayed {gen})"
                    )
                    assert _budget_pool(db, slug) == value, (
                        f"{where}: a changed budget must rewrite the stored pool to {value}"
                    )
                else:
                    # An unchanged value writes no new record: the generation is byte-identical and
                    # the round reports zero writes (Req 9.5).
                    assert gen == last_gen, (
                        f"{where}: an unchanged budget advanced the generation "
                        f"{last_gen} -> {gen}"
                    )
                    assert summary.writes == 0, (
                        f"{where}: an unchanged budget wrote a record (writes={summary.writes})"
                    )
            last_gen = gen
            last_value = value


def _decrement(client: fakeredis.FakeStrictRedis, org: str, n: int) -> None:
    """A live lease spending the pool: a direct DECRBY on ``{rv2}:budget:{org}:remaining``."""
    client.decrby(KEYS.budget_remaining(org), n)


def _remaining(client: fakeredis.FakeStrictRedis, org: str) -> int | None:
    raw = client.get(KEYS.budget_remaining(org))
    return None if raw is None else int(raw)


def _stored_generation(client: fakeredis.FakeStrictRedis, org: str) -> int | None:
    raw = client.get(KEYS.budget_generation(org))
    return None if raw is None else int(raw)


# Feature: gw36-s4-admission-quota, Property 7: CAS seed safety
# Validates: Requirements 10.3, 10.5
def test_property_7_cas_seed_safety() -> None:
    """For any interleaving of projector seeds and live lease decrements over
    ``{rv2}:budget:{org}:remaining`` / ``:generation``, a seed re-seeds the pool only when the
    stored generation does NOT equal the record's ``feed_seq``; a seed at the already-stored
    generation never overwrites ``remaining`` (so a live decrement survives -- Req 10.5), and the
    counter otherwise reflects every decrement.

    The ``ValkeyPoolSeeder`` rule asserted (read off the implementation): the seed compares the
    stored ``:generation`` to ``generation`` for EQUALITY. ``stored == generation`` -> no-op,
    returns ``True``, ``remaining`` untouched (Req 10.5). ANY inequality (absent / higher /
    lower-stale) -> SET ``remaining = pool_tokens`` + ``generation = generation``, returns
    ``True`` (Req 10.1, 10.2, 10.4). It is an equality CAS, so a stale (lower) generation re-seeds
    too -- the property tracks the code, not an idealised monotonic CAS.

    **Validates: Requirements 10.3, 10.5**
    """
    seed = 0x3654_0007
    rng = random.Random(seed)
    for iteration in range(_ITERATIONS):
        org = "acme"
        client = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer())
        seeder = ValkeyPoolSeeder(client, keys=KEYS)

        # A model of the ground truth the store SHOULD reflect, advanced in lockstep with the ops.
        model_remaining: int | None = None
        model_generation: int | None = None

        # Start from a seed so the first decrement has a pool to spend (and to cover the "seed then
        # live traffic" ordering explicitly).
        start_gen = rng.randint(1, 50)
        start_pool = rng.randint(0, _MAX_BUDGET)
        assert seeder.seed(org, pool_tokens=start_pool, generation=start_gen), (
            f"seed={seed:#x} iteration={iteration}: the initial seed must take"
        )
        model_remaining, model_generation = start_pool, start_gen

        ops = rng.randint(1, 10)
        trace: list[str] = []
        for _ in range(ops):
            where = (
                f"seed={seed:#x} iteration={iteration} start=({start_gen},{start_pool}) "
                f"trace={trace}"
            )
            if rng.random() < 0.5:
                # A seed. Pick a generation that is sometimes the stored one (no-op), sometimes a
                # genuine advance, sometimes a STALE (lower) generation.
                roll = rng.random()
                if roll < 0.34:
                    gen = model_generation  # matching -> must be a no-op
                elif roll < 0.67:
                    gen = model_generation + rng.randint(1, 20)  # advance
                else:
                    gen = max(0, model_generation - rng.randint(1, 20))  # stale / lower
                pool = rng.randint(0, _MAX_BUDGET)
                trace.append(f"seed(gen={gen},pool={pool})")

                result = seeder.seed(org, pool_tokens=pool, generation=gen)
                assert result is True, f"{where}: an uncontended seed always returns True"

                if gen == model_generation:
                    # Matching generation: no re-seed. remaining and generation unchanged, so the
                    # live decrements accumulated so far survive (Req 10.5).
                    pass
                else:
                    # Any inequality (advance OR stale): the equality CAS re-seeds the pool and
                    # stamps the generation (Req 10.1, 10.2, 10.4).
                    model_remaining = pool
                    model_generation = gen
            else:
                # A live lease decrement against `remaining`.
                if model_remaining is None or model_remaining <= 0:
                    continue
                n = rng.randint(1, max(1, model_remaining))
                trace.append(f"decr({n})")
                _decrement(client, org, n)
                model_remaining -= n

            assert _remaining(client, org) == model_remaining, (
                f"{where}: remaining diverged from the model (store="
                f"{_remaining(client, org)} model={model_remaining})"
            )
            assert _stored_generation(client, org) == model_generation, (
                f"{where}: stored generation diverged from the model (store="
                f"{_stored_generation(client, org)} model={model_generation})"
            )

        # Final witness for Req 10.5: a seed at the already-stored generation leaves a decremented
        # pool exactly where the decrements left it.
        where = f"seed={seed:#x} iteration={iteration} final trace={trace}"
        if model_remaining is not None and model_remaining > 0:
            spend = rng.randint(1, model_remaining)
            _decrement(client, org, spend)
            model_remaining -= spend
            assert seeder.seed(org, pool_tokens=_MAX_BUDGET, generation=model_generation) is True, (
                f"{where}: a same-generation seed must return True"
            )
            assert _remaining(client, org) == model_remaining, (
                f"{where}: a same-generation seed clobbered a live decrement "
                f"(store={_remaining(client, org)} expected={model_remaining})"
            )
