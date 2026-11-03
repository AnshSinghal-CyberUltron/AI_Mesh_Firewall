"""GW36 S4, task 8.2: unit tests for ``budget_applier`` (Req 7.2, 7.3, 7.4).

The applier is the synchroniser ``Applier`` for ``StateKind.BUDGET``. By the time a drained
``FeedRound`` reaches it the feed reader has already verified each record's signature, body hash,
``feed_seq`` against the index score, and the kind's anti-regress floor, so these example-based
checks exercise the one thing the applier owns:

- replace the served ``BudgetRecord`` for every org named in the round and leave every org NOT named
  untouched (Req 7.2);
- call ``registry_hook(org)`` exactly when a record moves an org's served generation to a different
  value -- never on a first record for a never-seen org, never on an unchanged generation (Req 7.3);
- on any record defect ``record_of`` rejects (a kind mismatch, a deleted record, a body whose
  ``org_id`` disagrees with the record key, a negative/non-int ``pool_tokens``) raise
  ``StoreDataUnavailable`` with the served set and the round's cursor left untouched, and with the
  re-acquire hook never fired -- the whole round decodes before anything is mutated (Req 7.4).

The ``registry_hook`` is a spy list recording the org arguments. ``SignedRecord`` carries a real
canonical body via ``make_record`` (``record_of`` reads the body JSON, not the signature, which the
feed reader already proved), and ``FeedRound`` is built directly around a plain ``Manifest`` -- the
applier reads only ``round_.records``.
"""

from __future__ import annotations

import pytest

from gateway_v2.admit.budget_state import BudgetRecord, BudgetSnapshot, budget_applier
from gateway_v2.domain.state import (
    Cursor,
    Manifest,
    SignedRecord,
    StateKind,
    StateOp,
    StoreDataUnavailable,
    Version,
)
from gateway_v2.runtime.state_feed import FeedRound
from gateway_v2.runtime.state_sig import make_record

SECRET = b"budget-applier-unit-secret"


def _budget_record(org: str, pool_tokens: int, feed_seq: int) -> SignedRecord:
    """A signed BUDGET record for ``org`` carrying ``pool_tokens`` at generation ``feed_seq``.

    ``make_record`` produces the real canonical body + signature; ``record_of`` reads the body only,
    so the signature is irrelevant to the applier but keeping it real matches the shipped decode
    path (the version seq tracks ``feed_seq`` exactly as the projector writes it).
    """
    return make_record(
        SECRET,
        StateKind.BUDGET,
        org,
        {"org_id": org, "pool_tokens": pool_tokens},
        Version(1, feed_seq),
        feed_seq,
    )


def _round(*records: SignedRecord, feed_seq: int = 0) -> FeedRound:
    """A drained ``FeedRound`` carrying ``records``. The applier reads only ``.records``.

    ``feed_seq`` defaults to the last record's generation; manifest/cursor fields are present only
    to satisfy the dataclass shape -- the applier never consults them.
    """
    position = feed_seq or (records[-1].feed_seq if records else 0)
    return FeedRound(
        kind=StateKind.BUDGET,
        manifest=Manifest(StateKind.BUDGET, Version(1, position), position, len(records), 0, "s"),
        records=records,
        cursor=Cursor(Version(1, position), position),
        truncated=False,
    )


def _seed(snapshot: BudgetSnapshot, *records: BudgetRecord) -> None:
    """Install served records directly, bypassing the applier (the arrange step for a round)."""
    for record in records:
        snapshot.replace(record)


# --- Req 7.2: replace named orgs, leave unnamed orgs untouched -----------------------------------


def test_round_replaces_only_the_named_org_and_leaves_the_unnamed_one_untouched() -> None:
    """A round naming only A updates A's served record and leaves B's entry exactly as it was."""
    snapshot = BudgetSnapshot()
    seeded_b = BudgetRecord(org="B", pool_tokens=500, generation=1)
    _seed(snapshot, BudgetRecord(org="A", pool_tokens=100, generation=1), seeded_b)
    hook_calls: list[str] = []
    apply = budget_applier(snapshot, hook_calls.append)

    apply(_round(_budget_record("A", pool_tokens=250, feed_seq=1)))

    served_a = snapshot.record_for("A")
    assert served_a == BudgetRecord(org="A", pool_tokens=250, generation=1)
    assert snapshot.record_for("B") is seeded_b, "B was not named, so its record is untouched"


def test_an_empty_round_changes_no_served_record_and_fires_no_hook() -> None:
    """The steady-state short circuit: a round with no BUDGET records applies nothing."""
    snapshot = BudgetSnapshot()
    seeded = BudgetRecord(org="A", pool_tokens=100, generation=1)
    _seed(snapshot, seeded)
    hook_calls: list[str] = []
    apply = budget_applier(snapshot, hook_calls.append)

    apply(_round())

    assert snapshot.record_for("A") is seeded
    assert hook_calls == []


def test_a_round_naming_several_orgs_replaces_each_of_them() -> None:
    """Every org named in the round advances; the map holds one record per org."""
    snapshot = BudgetSnapshot()
    _seed(
        snapshot,
        BudgetRecord(org="A", pool_tokens=100, generation=1),
        BudgetRecord(org="B", pool_tokens=200, generation=1),
    )
    hook_calls: list[str] = []
    apply = budget_applier(snapshot, hook_calls.append)

    apply(
        _round(
            _budget_record("A", pool_tokens=150, feed_seq=2),
            _budget_record("B", pool_tokens=250, feed_seq=3),
        ),
    )

    assert snapshot.record_for("A") == BudgetRecord(org="A", pool_tokens=150, generation=2)
    assert snapshot.record_for("B") == BudgetRecord(org="B", pool_tokens=250, generation=3)


# --- Req 7.3: a generation change fires the re-acquire hook ---------------------------------------


def test_a_generation_change_fires_the_hook_once_for_that_org() -> None:
    """A at g1, then a record at g2 != g1: the hook fires for ``A`` exactly once."""
    snapshot = BudgetSnapshot()
    _seed(snapshot, BudgetRecord(org="A", pool_tokens=100, generation=1))
    hook_calls: list[str] = []
    apply = budget_applier(snapshot, hook_calls.append)

    apply(_round(_budget_record("A", pool_tokens=100, feed_seq=2)))

    assert hook_calls == ["A"]


def test_a_first_record_for_a_never_seen_org_does_not_fire_the_hook() -> None:
    """No previous generation means no re-acquire: a first record installs without a hook call."""
    snapshot = BudgetSnapshot()
    hook_calls: list[str] = []
    apply = budget_applier(snapshot, hook_calls.append)

    apply(_round(_budget_record("A", pool_tokens=100, feed_seq=1)))

    assert snapshot.record_for("A") == BudgetRecord(org="A", pool_tokens=100, generation=1)
    assert hook_calls == []


def test_an_unchanged_generation_does_not_fire_the_hook() -> None:
    """A re-published record at the SAME generation replaces the value but re-acquires nothing."""
    snapshot = BudgetSnapshot()
    _seed(snapshot, BudgetRecord(org="A", pool_tokens=100, generation=1))
    hook_calls: list[str] = []
    apply = budget_applier(snapshot, hook_calls.append)

    apply(_round(_budget_record("A", pool_tokens=250, feed_seq=1)))

    assert snapshot.record_for("A") == BudgetRecord(org="A", pool_tokens=250, generation=1)
    assert hook_calls == []


def test_only_the_org_whose_generation_moved_is_re_acquired() -> None:
    """A moves g1->g2 (hook), B stays at g1 (no hook): the hook names only A."""
    snapshot = BudgetSnapshot()
    _seed(
        snapshot,
        BudgetRecord(org="A", pool_tokens=100, generation=1),
        BudgetRecord(org="B", pool_tokens=200, generation=1),
    )
    hook_calls: list[str] = []
    apply = budget_applier(snapshot, hook_calls.append)

    apply(
        _round(
            _budget_record("A", pool_tokens=100, feed_seq=2),
            _budget_record("B", pool_tokens=200, feed_seq=1),
        ),
    )

    assert hook_calls == ["A"]


# --- Req 7.4: a refused record leaves the cursor + served set untouched, hook never fires ---------


def test_a_record_keyed_for_one_org_carrying_another_body_refuses_the_round() -> None:
    """A completeness defect ``record_of`` catches: the applier raises and mutates nothing."""
    snapshot = BudgetSnapshot()
    seeded = BudgetRecord(org="A", pool_tokens=100, generation=1)
    _seed(snapshot, seeded)
    hook_calls: list[str] = []
    apply = budget_applier(snapshot, hook_calls.append)
    # A record keyed "A" whose body names org "B": record_of refuses it.
    mismatched = make_record(
        SECRET,
        StateKind.BUDGET,
        "A",
        {"org_id": "B", "pool_tokens": 999},
        Version(1, 2),
        2,
    )

    with pytest.raises(StoreDataUnavailable):
        apply(_round(mismatched))

    assert snapshot.record_for("A") is seeded, "a refused round leaves the served record put"
    assert hook_calls == []


def test_a_negative_pool_tokens_body_refuses_the_round() -> None:
    """A body defect ``record_of`` catches: negative pool tokens is a store-outage, not a value."""
    snapshot = BudgetSnapshot()
    seeded = BudgetRecord(org="A", pool_tokens=100, generation=1)
    _seed(snapshot, seeded)
    hook_calls: list[str] = []
    apply = budget_applier(snapshot, hook_calls.append)
    negative = make_record(
        SECRET,
        StateKind.BUDGET,
        "A",
        {"org_id": "A", "pool_tokens": -1},
        Version(1, 2),
        2,
    )

    with pytest.raises(StoreDataUnavailable):
        apply(_round(negative))

    assert snapshot.record_for("A") is seeded
    assert hook_calls == []


def test_a_deleted_budget_record_refuses_the_round() -> None:
    """A deleted BUDGET record is an undefined shape: the applier refuses the whole round."""
    snapshot = BudgetSnapshot()
    seeded = BudgetRecord(org="A", pool_tokens=100, generation=1)
    _seed(snapshot, seeded)
    hook_calls: list[str] = []
    apply = budget_applier(snapshot, hook_calls.append)
    deleted = make_record(
        SECRET,
        StateKind.BUDGET,
        "A",
        {"org_id": "A", "pool_tokens": 100},
        Version(1, 2),
        2,
        deleted=True,
        op=StateOp.OFFBOARD,
    )

    with pytest.raises(StoreDataUnavailable):
        apply(_round(deleted))

    assert snapshot.record_for("A") is seeded
    assert hook_calls == []


def test_a_wrong_kind_record_refuses_the_round() -> None:
    """A KS record reaching the BUDGET applier is a kind mismatch: refuse, mutate nothing."""
    snapshot = BudgetSnapshot()
    hook_calls: list[str] = []
    apply = budget_applier(snapshot, hook_calls.append)
    wrong_kind = SignedRecord(
        StateKind.KS, "A", Version(1, 1), 1, "h", False, StateOp.PUT, "s", b'{"on": true}',
    )

    with pytest.raises(StoreDataUnavailable):
        apply(_round(wrong_kind))

    assert snapshot.record_for("A") is None
    assert hook_calls == []


def test_a_bad_record_late_in_a_round_leaves_no_partial_update() -> None:
    """The whole round decodes before any served record is touched (Req 7.4): all-or-nothing.

    A good record for A precedes a refused record for B. The applier decodes the entire round up
    front, so the raise on B's record leaves A's served entry exactly as it was seeded -- no partial
    update, and the re-acquire hook never fires.
    """
    snapshot = BudgetSnapshot()
    seeded_a = BudgetRecord(org="A", pool_tokens=100, generation=1)
    _seed(snapshot, seeded_a)
    hook_calls: list[str] = []
    apply = budget_applier(snapshot, hook_calls.append)
    good_a = _budget_record("A", pool_tokens=999, feed_seq=2)  # would move A's generation
    bad_b = make_record(
        SECRET,
        StateKind.BUDGET,
        "B",
        {"org_id": "WRONG", "pool_tokens": 10},
        Version(1, 3),
        3,
    )

    with pytest.raises(StoreDataUnavailable):
        apply(_round(good_a, bad_b))

    assert snapshot.record_for("A") is seeded_a, "no partial update: A is untouched on a refusal"
    assert snapshot.record_for("B") is None
    assert hook_calls == [], "a refused round never marks an org for re-acquire"
