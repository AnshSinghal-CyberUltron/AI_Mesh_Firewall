# Feature: gw29-killswitch-isolation, Property 11: revocation ledger has no false negative
# Validates: Requirements 5.8, 6.2 (key revoked mid-stream, record.feed_seq basis, overflow floor)
"""P11 over random revoke / re-add / overflow sequences."""

from __future__ import annotations

import inspect
import random

from gateway_v2.admit.revocation import RevocationLedger
from gateway_v2.domain.state import StateKind, StateOp, Version
from gateway_v2.runtime.state_sig import make_record
from tests.admit._lgw29_support import SECRET, Clock

SEED = 29011
ITERATIONS = 3_000
KEYS = tuple(f"k{i}" for i in range(6))


def _key(key: str, feed_seq: int, *, deleted: bool):  # type: ignore[no-untyped-def]
    return make_record(
        SECRET, StateKind.KEY, key, {"k": key}, Version(1, 9_999 - feed_seq), feed_seq,
        deleted=deleted, op=StateOp.REVOKE if deleted else StateOp.PUT,
    )


def test_property_11_no_false_negative_and_exact_without_overflow() -> None:
    rng = random.Random(SEED)
    for i in range(ITERATIONS):
        slots = rng.choice((2, 3, 64))
        ledger = RevocationLedger(clock=Clock(0.0), slots=slots, retention_s=1e9)
        revoked: dict[str, int] = {}
        overflowed = False
        seq = 0
        streams: list[tuple[str, int]] = []
        for _ in range(rng.randint(1, 30)):
            seq += rng.randint(1, 3)
            key = rng.choice(KEYS)
            op = rng.random()
            if op < 0.4:
                ledger.observe([_key(key, seq, deleted=True)])
                revoked[key] = max(revoked.get(key, -1), seq)
                overflowed |= len(ledger) >= slots and len(revoked) > slots
            elif op < 0.6:
                ledger.observe([_key(key, seq, deleted=False)])  # re-add never erases a revoke
            else:
                streams.append((key, seq))  # a stream admitted under the current generation
        for key, admitted in streams:
            truth = revoked.get(key, -1) > admitted
            got = ledger.revoked_since(key, admitted)
            tag = f"seed={SEED} i={i} key={key} admitted={admitted} slots={slots}"
            if truth:
                assert got, f"{tag} FALSE NEGATIVE"
            if not overflowed:
                assert got == truth, f"{tag} not exact below the bound"


def test_an_empty_ledger_never_reports_revoked_even_for_feed_seq_zero() -> None:
    ledger = RevocationLedger(clock=Clock(0.0), slots=4, retention_s=100.0)
    assert ledger.floor_feed_seq == -1
    assert not ledger.revoked_since("k", 0) and not ledger.revoked_since("k", 5)


def test_the_basis_is_record_feed_seq_not_the_manifest_and_not_the_version() -> None:
    ledger = RevocationLedger(clock=Clock(0.0), slots=4, retention_s=100.0)
    ledger.observe([_key("k", 10, deleted=True)])  # Version.seq is 9989: must be ignored
    assert ledger.revoked_since("k", 9) and not ledger.revoked_since("k", 10)
    assert "manifest" not in inspect.signature(ledger.revoked_since).parameters


def test_delete_readd_delete_keeps_the_highest_revoke_and_recreated_principal_is_clean() -> None:
    ledger = RevocationLedger(clock=Clock(0.0), slots=4, retention_s=100.0)
    ledger.observe([_key("k", 5, deleted=True)])
    ledger.observe([_key("k", 7, deleted=False)])
    assert ledger.revoked_since("k", 4)  # the old stream is still cut
    assert not ledger.revoked_since("k", 7)  # the re-created principal is not
    ledger.observe([_key("k", 9, deleted=True)])
    assert ledger.revoked_since("k", 7) and not ledger.revoked_since("k", 9)


def test_overflow_raises_the_floor_and_cuts_what_can_no_longer_be_proven_clean() -> None:
    cuts: list[int] = []
    ledger = RevocationLedger(clock=Clock(0.0), slots=2, retention_s=100.0,
                              on_floor_cut=lambda: cuts.append(1))
    for n, key in enumerate(("a", "b", "c"), start=1):
        ledger.observe([_key(key, n * 10, deleted=True)])
    assert ledger.floor_feed_seq == 10  # 'a' was dropped
    assert ledger.revoked_since("a", 9)  # real revoke, now proven only by the floor
    assert ledger.revoked_since("zzz", 10) and len(cuts) == 2  # unrelated key, old stream: cut
    assert not ledger.revoked_since("zzz", 11)  # newer streams are fine
    assert ledger.floor_cuts_total == 2


def test_entries_older_than_the_retention_are_dead() -> None:
    clock = Clock(0.0)
    ledger = RevocationLedger(clock=clock, slots=4, retention_s=100.0)
    ledger.observe([_key("k", 5, deleted=True)])
    assert ledger.revoked_since("k", 1)
    clock.now = 101.0
    assert not ledger.revoked_since("k", 1) and len(ledger) == 0
