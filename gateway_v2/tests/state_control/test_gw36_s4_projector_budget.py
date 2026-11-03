"""GW36 S4 — the projector BUDGET write phase and the live pool-counter CAS seed.

The projector scenarios reuse the S2 `Lab` harness idiom exactly: the real `StateWriter` over
the transactional `MemoryControlDB` twin, a `MemorySource` driven console-style, `round_once`
as the unit of work, and the signed `amf_state_record` read back through `db.snapshot`. Seeding
is isolated behind the injected `PoolSeeder`: a `FakeSeeder` records every `seed(org, pool, gen)`
call and returns a controllable verdict, so the write/seed wiring is asserted without a store.

`ValkeyPoolSeeder` is exercised separately against a synchronous `fakeredis.FakeStrictRedis`
for the real WATCH/MULTI compare-and-set behaviour (Req 10.1/10.2/10.4/10.6).
"""

from __future__ import annotations

import json

import pytest

from gateway_v2.domain.state import StateKind
from gateway_v2.domain.state_knobs import ProjectorKnobs
from gateway_v2.runtime.store_keys import KEYS
from state_control.db import MemoryControlDB
from state_control.projector import _MAX_SEED_CAS_TRIES, Projector, ValkeyPoolSeeder
from state_control.publisher import MemoryStore
from state_control.source import MemorySource
from state_control.writer import StateWriter
from tests.state_control.test_lgw05c_writer import SECRET

KNOBS = ProjectorKnobs()


class Clock:
    def __init__(self) -> None:
        self.now = 10_000.0

    def __call__(self) -> float:
        return self.now


class FakeSeeder:
    """Records every `seed` call and returns a canned verdict. Stands in for the real CAS."""

    def __init__(self, *, result: bool = True) -> None:
        self.calls: list[tuple[str, int, int]] = []
        self.result = result
        self.raise_next = False

    def seed(self, org: str, *, pool_tokens: int, generation: int) -> bool:
        if self.raise_next:
            self.raise_next = False
            raise ConnectionError("injected seed store failure")
        self.calls.append((org, pool_tokens, generation))
        return self.result


class Lab:
    """The S2 projector harness, with a `FakeSeeder` wired into the projector."""

    def __init__(self, *, seeder: FakeSeeder | None = None, knobs: ProjectorKnobs = KNOBS) -> None:
        self.clock = Clock()
        self.db = MemoryControlDB(clock=self.clock)
        self.store = MemoryStore()
        self.source = MemorySource(clock=self.clock)
        self.writer = StateWriter(self.db, self.store, SECRET, actor="amf.projector:a")
        self.seeder = seeder if seeder is not None else FakeSeeder()
        self.projector = Projector(
            self.db, self.source, self.writer,
            holder="a", knobs=knobs, seeder=self.seeder,
        )

    def budgets(self) -> dict[str, dict[str, object]]:
        return {
            record.key: json.loads(record.body)
            for record in self.db.snapshot(StateKind.BUDGET)[1]
            if not record.deleted
        }

    def feed_seq_of(self, org: str) -> int:
        (record,) = [r for r in self.db.snapshot(StateKind.BUDGET)[1] if r.key == org]
        return record.feed_seq


# --- the signed BUDGET write phase ----------------------------------------------------------


def test_a_first_time_org_gets_a_record_with_an_initial_generation() -> None:
    """Req 9.1, 9.3: every active org with a stored budget has a record by round end, and a
    first-time org's record carries an initial generation (its own `feed_seq`)."""
    lab = Lab()
    lab.source.org("acme")
    lab.source.budget("acme", 100_000)

    summary = lab.projector.round_once()

    assert summary.heartbeat and summary.complete
    assert lab.budgets()["acme"] == {"org_id": "acme", "pool_tokens": 100_000}
    gen = lab.feed_seq_of("acme")
    assert gen > 0, "the initial generation is the record's own feed_seq"
    assert lab.seeder.calls == [("acme", 100_000, gen)]


def test_an_unchanged_budget_writes_nothing_on_the_second_round() -> None:
    """Req 9.5: `if_changed` leaves an unchanged budget untouched — no new amf_state_log row."""
    lab = Lab()
    lab.source.org("acme")
    lab.source.budget("acme", 100_000)
    lab.projector.round_once()
    gen = lab.feed_seq_of("acme")
    before = len(lab.db.log())

    lab.clock.now += 1.0
    again = lab.projector.round_once()

    assert again.writes == 0
    assert len(lab.db.log()) == before, "no write for an unchanged budget"
    assert lab.feed_seq_of("acme") == gen, "the generation did not advance"


def test_a_changed_budget_advances_the_generation_strictly() -> None:
    """Req 9.4: a changed budget writes a new record with a strictly greater feed_seq."""
    lab = Lab()
    lab.source.org("acme")
    lab.source.budget("acme", 100_000)
    lab.projector.round_once()
    first_gen = lab.feed_seq_of("acme")

    lab.clock.now += 1.0
    lab.source.budget("acme", 250_000)
    summary = lab.projector.round_once()

    assert summary.writes >= 1
    second_gen = lab.feed_seq_of("acme")
    assert second_gen > first_gen, "a changed budget bumps the generation upward"
    assert lab.budgets()["acme"]["pool_tokens"] == 250_000


def test_an_org_with_no_stored_budget_writes_nothing_and_leaves_a_prior_record() -> None:
    """Req 9.6: an org not in the source's budgets gets no record and any prior record stays —
    budgets have no offboard phase in S4."""
    lab = Lab()
    lab.source.org("acme")
    lab.source.budget("acme", 100_000)
    lab.projector.round_once()
    kept = dict(lab.budgets()["acme"])
    gen = lab.feed_seq_of("acme")

    lab.clock.now += 1.0
    lab.source.drop_budget("acme")
    lab.seeder.calls.clear()
    summary = lab.projector.round_once()

    assert summary.writes == 0, "no offboard write when the budget disappears"
    assert lab.budgets()["acme"] == kept, "the prior signed record is left in place"
    assert lab.feed_seq_of("acme") == gen
    assert lab.seeder.calls == [], "nothing to seed for an org with no stored budget"


def test_an_org_with_no_budget_but_a_plan_gets_no_budget_record() -> None:
    """Req 9.6: a budget record is written only for orgs with a stored control budget."""
    lab = Lab()
    lab.source.org("acme")  # active org, no budget row
    summary = lab.projector.round_once()
    assert summary.heartbeat
    assert "acme" not in lab.budgets()
    assert lab.seeder.calls == []


# --- the seed is driven off the right generation --------------------------------------------


def test_the_seed_receives_the_write_feed_seq_as_generation() -> None:
    """Req 10.1/10.2/10.4: after a BUDGET write the seeder is called with the record's feed_seq."""
    lab = Lab()
    lab.source.org("acme")
    lab.source.budget("acme", 100_000)
    lab.projector.round_once()
    assert lab.seeder.calls == [("acme", 100_000, lab.feed_seq_of("acme"))]


def test_the_seed_uses_the_held_records_feed_seq_when_unchanged() -> None:
    """Req 10.4: on an unchanged outcome the held record's own feed_seq is the generation, not
    the kind-level cursor (which may sit above it after another org was written this phase)."""
    lab = Lab()
    lab.source.org("acme")
    lab.source.budget("acme", 100_000)
    lab.projector.round_once()
    acme_gen = lab.feed_seq_of("acme")

    # A second org whose write advances the kind cursor above acme's record; acme stays unchanged.
    lab.clock.now += 1.0
    lab.source.org("beta")
    lab.source.budget("beta", 50_000)
    lab.seeder.calls.clear()
    lab.projector.round_once()

    beta_gen = lab.feed_seq_of("beta")
    assert beta_gen > acme_gen
    by_org = dict((org, gen) for org, _pool, gen in lab.seeder.calls)
    assert by_org["acme"] == acme_gen, "the held record's feed_seq, not the cursor"
    assert by_org["beta"] == beta_gen


def test_a_store_fault_in_the_seeder_never_aborts_the_round() -> None:
    """Req 10.6 robustness: a seed exception is swallowed; the signed record stays written."""
    lab = Lab()
    lab.source.org("acme")
    lab.source.budget("acme", 100_000)
    lab.seeder.raise_next = True

    summary = lab.projector.round_once()

    assert summary.heartbeat and summary.complete, "the round completes despite the seed fault"
    assert lab.budgets()["acme"] == {"org_id": "acme", "pool_tokens": 100_000}


def test_a_seed_cas_failure_does_not_stop_the_phase() -> None:
    """Req 10.6: a seeder returning False (bounded CAS exhaustion) leaves the record written and
    the round complete; the next round retries."""
    lab = Lab(seeder=FakeSeeder(result=False))
    lab.source.org("acme")
    lab.source.budget("acme", 100_000)

    summary = lab.projector.round_once()

    assert summary.heartbeat and summary.complete
    assert lab.budgets()["acme"]["pool_tokens"] == 100_000
    assert lab.seeder.calls == [("acme", 100_000, lab.feed_seq_of("acme"))]


# --- ValkeyPoolSeeder against fakeredis: the real WATCH/MULTI CAS ----------------------------


def _redis():  # type: ignore[no-untyped-def]
    fakeredis = pytest.importorskip("fakeredis")
    return fakeredis.FakeStrictRedis(decode_responses=True)


def test_seed_sets_remaining_and_generation_on_a_new_generation() -> None:
    """Req 10.1, 10.2: a new generation SETs remaining = pool_tokens and generation = feed_seq."""
    client = _redis()
    seeder = ValkeyPoolSeeder(client)

    assert seeder.seed("acme", pool_tokens=100_000, generation=7) is True

    assert client.get(KEYS.budget_remaining("acme")) == "100000"
    assert client.get(KEYS.budget_generation("acme")) == "7"


def test_seed_re_seeds_on_an_advanced_generation() -> None:
    """Req 10.4: a new, higher generation re-seeds the pool and advances the tag."""
    client = _redis()
    seeder = ValkeyPoolSeeder(client)
    seeder.seed("acme", pool_tokens=100_000, generation=7)
    client.set(KEYS.budget_remaining("acme"), 40_000)  # a live lease spent down to 40k

    assert seeder.seed("acme", pool_tokens=250_000, generation=9) is True

    assert client.get(KEYS.budget_remaining("acme")) == "250000"
    assert client.get(KEYS.budget_generation("acme")) == "9"


def test_seed_does_not_re_seed_an_already_equal_generation() -> None:
    """Req 10.5: an already-equal generation leaves a decremented remaining untouched."""
    client = _redis()
    seeder = ValkeyPoolSeeder(client)
    seeder.seed("acme", pool_tokens=100_000, generation=7)
    client.set(KEYS.budget_remaining("acme"), 40_000)  # live lease decrement

    assert seeder.seed("acme", pool_tokens=100_000, generation=7) is True

    assert client.get(KEYS.budget_remaining("acme")) == "40000", "the decrement was not clobbered"
    assert client.get(KEYS.budget_generation("acme")) == "7"


def test_a_forced_cas_failure_leaves_the_counter_unchanged_and_returns_false() -> None:
    """Req 10.6: when the WATCH keeps conflicting, the bounded retry exhausts, the counter is
    left exactly as it was, and `seed` returns False (the caller retries next round)."""
    client = _redis()
    seeder = ValkeyPoolSeeder(client)
    seeder.seed("acme", pool_tokens=100_000, generation=7)
    client.set(KEYS.budget_remaining("acme"), 40_000)

    from redis.exceptions import WatchError

    real_pipeline = client.pipeline

    class _AlwaysConflict:
        """A pipeline wrapper whose EXEC always raises WatchError, forcing every retry to fail."""

        def __init__(self) -> None:
            self._pipe = real_pipeline()

        def __enter__(self):  # type: ignore[no-untyped-def]
            self._pipe.__enter__()
            return self

        def __exit__(self, *exc):  # type: ignore[no-untyped-def]
            return self._pipe.__exit__(*exc)

        def watch(self, *keys):  # type: ignore[no-untyped-def]
            return self._pipe.watch(*keys)

        def get(self, key):  # type: ignore[no-untyped-def]
            return self._pipe.get(key)

        def unwatch(self):  # type: ignore[no-untyped-def]
            return self._pipe.unwatch()

        def multi(self):  # type: ignore[no-untyped-def]
            return self._pipe.multi()

        def set(self, key, value):  # type: ignore[no-untyped-def]
            return self._pipe.set(key, value)

        def execute(self):  # type: ignore[no-untyped-def]
            raise WatchError("forced conflict")

    attempts = 0

    def conflicting_pipeline():  # type: ignore[no-untyped-def]
        nonlocal attempts
        attempts += 1
        return _AlwaysConflict()

    client.pipeline = conflicting_pipeline  # type: ignore[method-assign]

    # A new generation would otherwise re-seed, but every EXEC conflicts.
    assert seeder.seed("acme", pool_tokens=999_999, generation=9) is False
    assert attempts == _MAX_SEED_CAS_TRIES, "the retry is bounded by _MAX_SEED_CAS_TRIES"

    client.pipeline = real_pipeline  # type: ignore[method-assign]
    assert client.get(KEYS.budget_remaining("acme")) == "40000", "counter left unchanged"
    assert client.get(KEYS.budget_generation("acme")) == "7"
