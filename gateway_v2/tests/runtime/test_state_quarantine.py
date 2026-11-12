"""PR #136 review findings, pinned: the guards a mutant could remove without a test failing.

Every test here drives the REAL reader, synchroniser and plan applier over the feed suite's
in-memory store (the same lab as `test_state_applier_isolation`). Each names the reviewer finding
and, where there is one, the mutant it kills (reviewer A's MA*, reviewer B's BM*).
"""

from __future__ import annotations

import json
import logging
import random
import re
from itertools import accumulate

import pytest

from gateway_v2.admit.revocation import RevocationLedger
from gateway_v2.domain.plan import ExecutionPlan, PlanUnavailable
from gateway_v2.domain.state import (
    Cursor,
    StateKind,
    StoreDataUnavailable,
    Version,
)
from gateway_v2.plan.delta import plan_applier
from gateway_v2.plan.document import nesting_depth
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PLAN_QUARANTINED, QUARANTINED, REJECTED, PlanStore
from gateway_v2.runtime import state_feed
from gateway_v2.runtime.state_feed import FeedReader
from gateway_v2.runtime.state_metrics import StateMetricsRecorder
from gateway_v2.runtime.state_sig import (
    decode_manifest,
    decode_record,
    decode_stamp,
    encode_manifest,
    encode_record,
    encode_stamp,
    make_manifest,
    make_record,
    make_stamp,
)
from gateway_v2.runtime.state_stamp import StampView
from gateway_v2.runtime.state_task import DeltaBudget, StateSynchroniser
from tests.runtime.test_lgw05c_feed import SECRET, FakeStore
from tests.runtime.test_state_applier_isolation import (
    HOSTILE,
    LOGGER,
    Lab,
    _bad_signature,
    _body,
    _compile_error,
    _run,
)

QUARANTINED_TOTAL = 'amf_state_records_quarantined_total{kind="plan"}'
REJECTED_TOTAL = 'amf_state_records_rejected_total{kind="plan"}'


class SmallLab(Lab):
    """The isolation lab with a delta budget small enough that one drain takes several rounds."""

    def __init__(self, *, records: int) -> None:
        self.store = FakeStore()
        self.plans = PlanStore()
        self.snapshot = ReplicaSnapshot(self.plans, clock=lambda: 1.0)
        self.sync = StateSynchroniser(
            FeedReader(self.store, SECRET),
            {StateKind.PLAN: plan_applier(self.plans, self.snapshot, clock=lambda: 1.0)},
            budget=DeltaBudget(records=records, offload_above=1_000),
            isolate_kinds=frozenset({StateKind.PLAN}),
        )
        self.metrics = StateMetricsRecorder()
        self.position = 0
        self.publish(HOSTILE, _body(HOSTILE))
        assert self.drain().ok
        self.previous = self.served(HOSTILE).sequence


# --- S1 (A and B): a drain's counts are summed over every round ---------------------------------


@pytest.mark.parametrize(
    "hostile,counter,verified",
    [(_bad_signature, QUARANTINED_TOTAL, 0), (_compile_error, REJECTED_TOTAL, 1)],
    ids=["quarantined", "rejected"],
)
def test_a_fault_in_an_early_round_of_a_drain_is_counted(hostile, counter, verified) -> None:  # type: ignore[no-untyped-def]
    """20 good, the hostile record, 20 good: 41 entries in 6 rounds of 8, hostile in round 3.

    A rejected record verified, so it is among the round's records; a quarantined one is not.
    """
    lab = SmallLab(records=8)
    lab.queue_delta(hostile)

    report = lab.drain()

    assert report.ok and not report.truncated
    assert report.applied == 40 + verified, "applied is summed over the rounds too"
    series = lab.metrics.snapshot().series()
    assert series[counter] == 1, "the one hostile record is counted, whichever round it was in"
    assert series['amf_state_records_applied_total{kind="plan"}'] == 1 + 40 + verified


def test_the_held_back_gauges_say_who_is_held_back_now() -> None:
    lab = Lab(offload_above=1_000)
    lab.queue_delta(_bad_signature)
    lab.drain()
    lab.publish("org-r", _body("org-r"))
    lab.drain()
    lab.publish("org-r", {"org_id": "org-r"})  # verified, no rules: the compiler refuses it
    lab.drain()

    assert lab.plans.held_counts() == (1, 1)
    lab.metrics.observe_held_back(StateKind.PLAN, rejected=1, quarantined=1)
    series = lab.metrics.snapshot().series()
    assert series['amf_state_held_back_quarantined{kind="plan"}'] == 1
    assert series['amf_state_held_back_rejected{kind="plan"}'] == 1

    lab.publish(HOSTILE, _body(HOSTILE))
    lab.publish("org-r", _body("org-r"))
    lab.drain()
    assert lab.plans.held_counts() == (0, 0), "both lifted by a verified record that compiles"


# --- S3 (A's MA1/MA2, B's BM5): the in-flight skip is exactly as wide as it must be -------------


def test_a_newer_record_under_an_older_score_inside_the_manifest_is_a_fault_not_a_skip() -> None:
    """MA1 / BM5: the record IS newer than its entry, but not above the attested manifest.

    That is not a publish landing mid-round; a store writer moved the index entry back. Skipping
    it would leave a running worker on its old plan with no signal and a restarted one
    answering 403 (the C36 wedge). It is a fault: the tenant is quarantined.
    """
    lab = Lab(offload_above=1_000)
    lab.publish(HOSTILE, _body(HOSTILE))  # the entry the round selects, at s
    s = lab.position
    newer = make_record(SECRET, StateKind.PLAN, HOSTILE, _body(HOSTILE), Version(1, s + 1), s + 1)
    lab.publish("org-filler", _body("org-filler"))  # the manifest attests s + 1
    lab.set_raw(HOSTILE, encode_record(newer))

    report = lab.drain()

    assert report.ok and report.quarantined == 1 and report.in_flight == 0
    held = lab.snapshot.held_back(HOSTILE)
    assert held is not None and (held.state, held.feed_seq) == (QUARANTINED, s)


def test_another_keys_genuine_record_above_the_manifest_is_a_fault_not_a_skip() -> None:
    """MA2: only the SAME key's newer record is an in-flight publish."""
    lab = Lab(offload_above=1_000)
    lab.publish(HOSTILE, _body(HOSTILE))
    other = make_record(
        SECRET, StateKind.PLAN, "org-other", _body("org-other"), Version(1, 999), 999,
    )
    lab.set_raw(HOSTILE, encode_record(other))

    report = lab.drain()

    assert report.ok and report.quarantined == 1 and report.in_flight == 0


def test_an_in_flight_skip_is_counted() -> None:
    """B N2: the skip used to leave no trace; a writer stuck mid-publish is now visible."""
    lab = Lab(offload_above=1_000)
    newer = make_record(SECRET, StateKind.PLAN, HOSTILE, _body(HOSTILE), Version(1, 999), 999)
    lab.publish(HOSTILE, _body(HOSTILE))
    lab.set_raw(HOSTILE, encode_record(newer))

    report = lab.drain()

    assert report.ok and report.in_flight == 1 and report.quarantined == 0
    series = lab.metrics.snapshot().series()
    assert series['amf_state_records_in_flight_total{kind="plan"}'] == 1


# --- S3 (A's MA5): ANY exception verifying one record is that record's fault --------------------


def test_any_exception_verifying_one_record_is_a_fault_not_a_failed_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MA5: the isolating reader's catch-all, for a decoder defect nobody has found yet."""
    real = state_feed.decode_record

    def decode(secret: bytes, kind: StateKind, raw: bytes | str | None):  # type: ignore[no-untyped-def]
        record = real(secret, kind, raw)
        if record.key == HOSTILE:
            raise RuntimeError("a decoder defect")
        return record

    lab = Lab(offload_above=1_000)
    monkeypatch.setattr(state_feed, "decode_record", decode)
    good = lab.queue_delta(lambda lab: lab.publish(HOSTILE, _body(HOSTILE)))

    report = lab.drain()

    assert report.ok and report.quarantined == 1
    held = lab.snapshot.held_back(HOSTILE)
    assert held is not None and (held.state, held.error) == (QUARANTINED, "RuntimeError")
    assert all(isinstance(lab.snapshot.lookup(org), ExecutionPlan) for org in good)


# --- A N2: every decoder defect is a counted, code-described StoreDataUnavailable ----------------


def _surrogate(raw: bytes, field: str) -> bytes:
    env = json.loads(raw)
    env[field] = "\ud800"
    return json.dumps(env).encode()


@pytest.mark.parametrize("field", ["body", "key", "sig"])
def test_a_record_the_decoder_cannot_encode_is_store_data_unavailable(field: str) -> None:
    """A lone surrogate raised `UnicodeEncodeError`, a non-ASCII signature `TypeError`."""
    raw = encode_record(make_record(SECRET, StateKind.PLAN, "org", {"a": 1}, Version(1, 1), 1))
    hostile = (
        raw.replace(b'"sig":"', '"sig":"é'.encode(), 1) if field == "sig"
        else _surrogate(raw, field)
    )

    with pytest.raises(StoreDataUnavailable):
        decode_record(SECRET, StateKind.PLAN, hostile)


def test_a_manifest_with_a_non_ascii_signature_is_store_data_unavailable() -> None:
    raw = encode_manifest(make_manifest(SECRET, StateKind.PLAN, Version(1, 1), 1, 1, 0))

    with pytest.raises(StoreDataUnavailable, match="fails its signature"):
        decode_manifest(
            SECRET, StateKind.PLAN, raw.replace(b'"sig":"', '"sig":"é'.encode(), 1),
            not_before=Version(0, 0),
        )


def test_a_non_ascii_record_signature_is_a_quarantine_with_a_store_fault() -> None:
    """A's P8: before, the fault's error was `TypeError`; now it is the decoder's own refusal."""
    lab = Lab(offload_above=1_000)
    record = lab.publish(HOSTILE, _body(HOSTILE))
    lab.set_raw(HOSTILE, encode_record(record).replace(b'"sig":"', '"sig":"é'.encode(), 1))
    lab.publish("org-after", _body("org-after"))

    report = lab.drain()

    assert report.ok and report.quarantined == 1
    held = lab.snapshot.held_back(HOSTILE)
    assert held is not None and held.error == "StoreDataUnavailable"


def _stamped_store() -> FakeStore:
    store = FakeStore()
    store.publish(
        make_record(SECRET, StateKind.PLAN, "org-a", _body("org-a"), Version(1, 1), 1), count=1,
    )
    return store


def test_a_stamp_with_a_non_ascii_signature_is_counted_invalid_and_never_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A N2 / P9: it used to raise `TypeError`, logged on every cycle and never counted."""
    store = _stamped_store()
    stamp = encode_stamp(
        make_stamp(SECRET, 100.0, {kind: Cursor(Version(1, 1), 1) for kind in StateKind}, "rh"),
    )
    store.stamp_raw = stamp.replace(b'"sig":"', '"sig":"é'.encode(), 1)
    with pytest.raises(StoreDataUnavailable):
        decode_stamp(SECRET, store.stamp_raw)
    plans, view = PlanStore(), StampView(SECRET)
    sync = StateSynchroniser(
        FeedReader(store, SECRET), {StateKind.PLAN: plan_applier(plans, clock=lambda: 1.0)},
        stamp=view,
    )

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        for _ in range(3):
            reports = _run(sync.drain_all())

    assert all(report.ok for report in reports)
    assert view.invalid == 3 and isinstance(plans.read("org-a"), ExecutionPlan)
    assert "freshness stamp read failed" not in caplog.text


def test_a_stamp_read_raising_anything_never_stops_the_drains_and_logs_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MA6: the stamp read's catch-all (a store `ResponseError` such as WRONGTYPE propagates
    from the adapter by design), logged once per change rather than on every cycle."""
    store = _stamped_store()

    async def wrong_type() -> bytes | None:
        raise RuntimeError("WRONGTYPE Operation against a key holding the wrong kind of value")

    store.stamp = wrong_type  # type: ignore[method-assign]
    plans = PlanStore()
    sync = StateSynchroniser(
        FeedReader(store, SECRET), {StateKind.PLAN: plan_applier(plans, clock=lambda: 1.0)},
        stamp=StampView(SECRET),
    )

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        for _ in range(3):
            reports = _run(sync.drain_all())

    assert [report.ok for report in reports] == [True]
    assert isinstance(plans.read("org-a"), ExecutionPlan)
    assert caplog.text.count("freshness stamp read failed: error=RuntimeError") == 1
    assert "WRONGTYPE" not in caplog.text


# --- A S2 / B N3: what a rejection may and may not lift -----------------------------------------


def test_a_quarantined_tenant_orders_a_new_record_against_the_plan_it_holds() -> None:
    """MA4: a verified record that is not newer than the HELD plan must not reach `put`.

    `read()` answers PLAN_UNAVAILABLE for a quarantined tenant, so ordering against it would
    call `put` with a regress, which raises, which fails the whole PLAN round for every tenant.
    """
    lab = Lab(offload_above=1_000)
    held_version = lab.served(HOSTILE).sequence
    lab.queue_delta(_bad_signature)
    lab.drain()
    lab.position += 1
    same = make_record(
        SECRET, StateKind.PLAN, HOSTILE, _body(HOSTILE), Version(1, held_version), lab.position,
    )
    lab.publish_record(same)
    lab.publish("org-after", _body("org-after"))

    report = lab.drain()

    assert report.ok, report.error
    assert isinstance(lab.snapshot.lookup("org-after"), ExecutionPlan)
    assert lab.served(HOSTILE).sequence == held_version, "the newest verified record says so"
    assert lab.snapshot.held_back(HOSTILE) is None


def test_a_verified_record_that_changes_nothing_clears_a_rejection() -> None:
    """MA10: v1 served, v2 rejected, then a verified record of v1 again: nothing is held."""
    lab = Lab(offload_above=1_000)
    v1 = lab.served(HOSTILE).sequence
    _compile_error(lab)
    lab.drain()
    assert lab.snapshot.held_back(HOSTILE) is not None
    lab.position += 1
    lab.publish_record(
        make_record(SECRET, StateKind.PLAN, HOSTILE, _body(HOSTILE), Version(1, v1), lab.position),
    )
    lab.publish("org-after", _body("org-after"))  # the manifest's version moves on again

    report = lab.drain()

    assert report.ok and lab.served(HOSTILE).sequence == v1
    assert lab.snapshot.held_back(HOSTILE) is None


def test_plan_store_hold_transitions() -> None:
    """The quarantine is lifted only from at or above its position, by a record that compiles."""
    store = PlanStore()
    store.quarantine("org", feed_seq=10, error="StoreDataUnavailable")

    store.reject("org", feed_seq=11, error="CompileError")  # newer, verified, uncompilable
    assert (store.held_back("org").state, store.held_counts()) == (QUARANTINED, (0, 1))  # type: ignore[union-attr]
    store.clear_held("org", feed_seq=9)  # older than the quarantined record: proves nothing
    assert store.held_back("org").state == QUARANTINED  # type: ignore[union-attr]
    assert isinstance(store.read("org"), PlanUnavailable)

    store.reject("org", feed_seq=10, error="CompileError")  # the quarantined record, now verified
    assert (store.held_back("org").state, store.held_counts()) == (REJECTED, (1, 0))  # type: ignore[union-attr]

    store.quarantine("org", feed_seq=12, error="StoreDataUnavailable")
    store.clear_held("org", feed_seq=12)
    assert store.held_back("org") is None and store.held_counts() == (0, 0)

    store.quarantine("org", feed_seq=13, error="StoreDataUnavailable")
    store.offboard("org")
    assert store.held_counts() == (0, 0) and store.quarantined_for_recheck(8) == ()


def test_the_re_check_visits_quarantined_tenants_round_robin() -> None:
    store = PlanStore()
    for n in range(5):
        store.quarantine(f"org-{n}", feed_seq=n, error="StoreDataUnavailable")
    store.reject("org-r", feed_seq=9, error="CompileError")

    first, second, third = (store.quarantined_for_recheck(2) for _ in range(3))

    assert [org for org, _ in first] == ["org-0", "org-1"]
    assert [org for org, _ in second] == ["org-2", "org-3"]
    assert [org for org, _ in third] == ["org-4", "org-0"], "a rejected tenant is never visited"
    assert dict(first) == {"org-0": 0, "org-1": 1}


# --- B S4 / A N1: a standing whole-kind refusal is logged, once per change ----------------------


def test_a_standing_refusal_is_logged_once_with_a_bounded_detail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """KS and BUDGET refuse their whole kind for one bad record, by design; it was never logged.

    The kind here is PLAN read WITHOUT isolation, which is exactly the KS/BUDGET contract. The
    index key is unauthenticated and as long as a store writer likes, so the detail is cut.
    """
    lab = Lab(offload_above=1_000)
    sync = StateSynchroniser(
        FeedReader(lab.store, SECRET),
        {StateKind.PLAN: plan_applier(PlanStore(), clock=lambda: 1.0)},
    )
    giant = "org-" + "x" * 5_000
    record = lab.publish(giant, _body(giant))
    lab.set_raw(giant, encode_record(record).replace(b'"sig":"', b'"sig":"0', 1))

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        reports = [_run(sync.drain(StateKind.PLAN)) for _ in range(3)]
        lab.publish(giant, _body(giant))  # the store is repaired by a newer record
        recovered = _run(sync.drain(StateKind.PLAN))

    assert [report.ok for report in reports] == [False, False, False] and recovered.ok
    refused = [r.getMessage() for r in caplog.records if "state round refused" in r.getMessage()]
    assert len(refused) == 1, "a standing refusal logs once, not once per cycle"
    assert refused[0].startswith("state round refused: kind=plan error=StoreDataUnavailable")
    assert len(refused[0]) < 300, "the unauthenticated key in the detail is cut"
    assert "state round recovered: kind=plan after error=StoreDataUnavailable" in caplog.text


def test_a_refusal_whose_positions_move_is_still_one_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    lab = Lab(offload_above=1_000)
    sync = StateSynchroniser(
        FeedReader(lab.store, SECRET),
        {StateKind.PLAN: plan_applier(PlanStore(), clock=lambda: 1.0)},
    )

    def overclaim() -> None:
        """The manifest attests one record more than the index holds: a partial store."""
        held = len(lab.store._index[StateKind.PLAN])
        at = lab.position
        lab.store._manifests[StateKind.PLAN] = encode_manifest(
            make_manifest(SECRET, StateKind.PLAN, Version(1, at), at, held + 1, 0),
        )

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        for n in range(3):
            lab.publish(f"org-more-{n}", _body(f"org-more-{n}"))  # the attested position moves
            overclaim()
            assert not _run(sync.drain(StateKind.PLAN)).ok

    assert caplog.text.count("state round refused") == 1


# --- KEY's ledger hook ---------------------------------------------------------------------------


def test_a_refused_key_record_cuts_streams_admitted_below_it() -> None:
    ledger = RevocationLedger(clock=lambda: 0.0, slots=4, retention_s=100.0)

    ledger.refuse("k", 7)

    assert ledger.revoked_since("k", 6), "a stream admitted below the faulted record is cut"
    assert not ledger.revoked_since("k", 7), "one admitted from a verified record at it is not"
    assert not ledger.revoked_since("other", 1)


# --- B S2: the depth scan's escape pass ----------------------------------------------------------


_ESCAPE = re.compile(rb"\\.", re.DOTALL)
_STRING = re.compile(rb'"[^"]*"')
_NOT_BRACKET = re.compile(rb"[^\[\]{}]+")
_STEP = {ord("["): 1, ord("{"): 1, ord("]"): -1, ord("}"): -1}


def _regex_depth(raw: bytes) -> int:
    """The scan as it was, with one `\\\\.` regex for the escapes: the reference."""
    brackets = _NOT_BRACKET.sub(b"", _STRING.sub(b"", _ESCAPE.sub(b"", raw)))
    return max(0, max(accumulate(map(_STEP.__getitem__, brackets)), default=0))


def _escapy(rng: random.Random) -> str:
    pieces = ['"', "\\", "[", "]", "{", "}", "a", "\n", "é", '\\"', "\\\\", '\\\\"']
    return "".join(rng.choice(pieces) for _ in range(rng.randint(0, 10)))


def _escapy_json(rng: random.Random, depth: int) -> object:
    if depth == 0 or rng.random() < 0.25:
        return rng.choice([_escapy(rng), 1, None, True])
    if rng.random() < 0.5:
        return [_escapy_json(rng, depth - 1) for _ in range(rng.randint(0, 3))]
    return {_escapy(rng): _escapy_json(rng, depth - 1) for _ in range(rng.randint(0, 3))}


def test_nesting_depth_matches_the_escape_regex() -> None:
    """Escape-dense valid JSON, both `ensure_ascii` spellings: identical depths, every time.

    And with garbage appended (`json` raises at the first defect), never below the depth of the
    valid prefix, which is the most `json` can reach before it gives up.
    """
    rng = random.Random(0x136B)
    garbage = [b"\\[[[[", b"]]]]", b'"\\', b"\\\\\\{", b'{"a":[']
    for _ in range(3_000):
        value = _escapy_json(rng, rng.randint(0, 10))
        for ascii_only in (True, False):
            raw = json.dumps(value, ensure_ascii=ascii_only).encode()
            assert nesting_depth(raw) == _regex_depth(raw), raw[:120]
            reached = nesting_depth(raw)
            assert nesting_depth(raw + rng.choice(garbage)) >= reached, raw[:120]


def test_nesting_depth_of_escape_dense_bodies() -> None:
    assert nesting_depth(b'"' + b"\\\\" * 1_000 + b'"[[') == 2
    assert nesting_depth(b'"' + b'\\"' * 1_000 + b'"[[') == 2
    assert nesting_depth(b'"' + b"\\\\" * 999 + b'\\"[["') == 0, "the quote is escaped"
    assert nesting_depth(b'["' + b"\\n" * 1_000 + b'"]') == 1


def test_quarantine_is_answered_plan_quarantined() -> None:
    lab = Lab(offload_above=1_000)
    lab.queue_delta(_bad_signature)
    lab.drain()
    state = lab.snapshot.lookup(HOSTILE)
    assert isinstance(state, PlanUnavailable) and state.reason == PLAN_QUARANTINED


def test_an_index_score_pairs_only_when_it_is_a_whole_number() -> None:
    """`fetch_key`'s pairing reads ZSCORE: 3.5 must not truncate into a position that pairs."""
    from gateway_v2.runtime.store_valkey import _as_score

    assert _as_score(3.0) == 3 and _as_score(7) == 7
    assert _as_score(3.5) is None and _as_score(float("nan")) is None
    assert _as_score(None) is None and _as_score(True) is None and _as_score(b"3") is None
