"""GW36 S4, task 3.2: unit tests for ``QuotaRegistry`` (Req 4.1, 4.2, 4.3, 7.3).

The registry builds one ``QuotaComponent`` per org on first use from the org's served
``BudgetRecord``, caches it, reuses it with no request-path record read, returns ``None`` when no
record is served, and rebuilds under a new generation after ``on_generation_change``. These are
example-based checks (the full per-org isolation property is task 4.3); the component itself needs a
store, so a counting factory returning a sentinel stands in for the real ``QuotaComponent`` — the
registry only caches and returns it, never calling methods on it in ``component_for``.
"""

from __future__ import annotations

from typing import cast

from gateway_v2.admit.budget_state import BudgetRecord
from gateway_v2.admit.quota import QuotaComponent
from gateway_v2.admit.quota_registry import QuotaRegistry


class _FakeSnapshot:
    """A served-budget snapshot backed by a dict the test controls, counting ``record_for`` reads.

    Satisfies ``BudgetSnapshotLike`` structurally. ``reads`` counts every ``record_for`` call so a
    test can prove the reuse path performs no request-path record read (Req 4.3).
    """

    def __init__(self, records: dict[str, BudgetRecord] | None = None) -> None:
        self.records: dict[str, BudgetRecord] = dict(records or {})
        self.reads = 0

    def record_for(self, org: str) -> BudgetRecord | None:
        self.reads += 1
        return self.records.get(org)


class _CountingFactory:
    """A ``make_component`` factory recording each ``(org, record)`` call and returning a sentinel.

    Each call returns a fresh unique sentinel object (cast to ``QuotaComponent`` for typing) so a
    test can assert build-once by identity and count invocations to prove reuse vs. rebuild.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, BudgetRecord]] = []

    def __call__(self, org: str, record: BudgetRecord) -> QuotaComponent:
        self.calls.append((org, record))
        return cast(QuotaComponent, object())


def _record(org: str, *, pool: int = 100, generation: int = 1) -> BudgetRecord:
    return BudgetRecord(org=org, pool_tokens=pool, generation=generation)


def test_first_use_builds_once_from_served_record_and_returns_it() -> None:
    """First ``component_for`` for a served org builds once and returns it (Req 4.1 / 4.2)."""
    record = _record("acme")
    snapshot = _FakeSnapshot({"acme": record})
    factory = _CountingFactory()
    registry = QuotaRegistry(snapshot, make_component=factory)

    component = registry.component_for("acme")

    assert component is not None
    assert factory.calls == [("acme", record)]  # built exactly once from the served record
    assert len(factory.calls) == 1


def test_reuse_returns_cached_component_without_rebuilding_or_reading_snapshot() -> None:
    """A second ``component_for`` reuses the cache: no factory call, no record read (Req 4.3)."""
    snapshot = _FakeSnapshot({"acme": _record("acme")})
    factory = _CountingFactory()
    registry = QuotaRegistry(snapshot, make_component=factory)

    first = registry.component_for("acme")
    reads_after_build = snapshot.reads
    assert len(factory.calls) == 1

    second = registry.component_for("acme")

    assert second is first  # same cached component
    assert len(factory.calls) == 1  # factory not called again
    assert snapshot.reads == reads_after_build  # no request-path record read on reuse


def test_no_served_record_returns_none_and_never_builds() -> None:
    """With no served ``BudgetRecord`` the registry returns ``None`` and never calls the factory."""
    snapshot = _FakeSnapshot({})  # nothing served for "ghost"
    factory = _CountingFactory()
    registry = QuotaRegistry(snapshot, make_component=factory)

    component = registry.component_for("ghost")

    assert component is None
    assert factory.calls == []  # never built; caller refuses budget_unavailable above this layer


def test_on_generation_change_rebuilds_from_current_record() -> None:
    """``on_generation_change`` forces the next build to rebuild under the new gen (Req 7.3)."""
    gen1 = _record("acme", generation=1)
    snapshot = _FakeSnapshot({"acme": gen1})
    factory = _CountingFactory()
    registry = QuotaRegistry(snapshot, make_component=factory)

    first = registry.component_for("acme")
    assert factory.calls == [("acme", gen1)]

    # A drained delta advances the org's generation; the applier updates the served record and
    # marks the org for re-acquire.
    gen2 = _record("acme", generation=2)
    snapshot.records["acme"] = gen2
    registry.on_generation_change("acme")

    rebuilt = registry.component_for("acme")

    assert rebuilt is not first  # the cached component was replaced
    assert len(factory.calls) == 2  # rebuilt once more
    assert factory.calls[1] == ("acme", gen2)  # from the CURRENT served record (new generation)


def test_on_generation_change_for_unseen_org_is_harmless() -> None:
    """Marking an org never seen is harmless: the next build just reads the snapshot."""
    snapshot = _FakeSnapshot({"acme": _record("acme")})
    factory = _CountingFactory()
    registry = QuotaRegistry(snapshot, make_component=factory)

    # No prior component_for for "acme"; marking is harmless.
    registry.on_generation_change("acme")

    component = registry.component_for("acme")

    assert component is not None
    assert len(factory.calls) == 1  # built on first use as normal, from the served record


def test_distinct_orgs_build_and_cache_independent_components() -> None:
    """Two orgs get two factory calls and two distinct cached components; neither couples."""
    acme_rec = _record("acme", pool=100, generation=1)
    globex_rec = _record("globex", pool=200, generation=1)
    snapshot = _FakeSnapshot({"acme": acme_rec, "globex": globex_rec})
    factory = _CountingFactory()
    registry = QuotaRegistry(snapshot, make_component=factory)

    acme = registry.component_for("acme")
    globex = registry.component_for("globex")

    assert acme is not None
    assert globex is not None
    assert acme is not globex
    assert factory.calls == [("acme", acme_rec), ("globex", globex_rec)]

    # Re-acquiring one org leaves the other's cached entry untouched: its reuse still returns the
    # same component with no rebuild (basic isolation example; Property 1 is task 4.3).
    registry.on_generation_change("acme")
    snapshot.records["acme"] = _record("acme", pool=100, generation=2)

    assert registry.component_for("globex") is globex  # unaffected by acme's re-acquire
    assert registry.component_for("acme") is not acme  # acme rebuilt
