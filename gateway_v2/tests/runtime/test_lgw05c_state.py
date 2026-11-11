"""GW05c phase 1 — the versioned state model and its anti-regress rules.

The first test is the design in miniature: it fails on any implementation that derives the feed
cursor from `seq`, which is the one mistake that silently reintroduces whole-kind reloads.
"""

from __future__ import annotations

import pytest

from gateway_v2.domain.state import (
    ZERO,
    SignedRecord,
    StateKind,
    StateOp,
    StoreDataUnavailable,
    Version,
)
from gateway_v2.runtime.state_sig import (
    canonical_body,
    decode_manifest,
    decode_record,
    encode_manifest,
    encode_record,
    make_manifest,
    make_record,
    record_matches_index,
)

SECRET = b"gw05c-test-signing-secret"
OTHER_SECRET = b"gw05c-test-signing-secret-rotated"


def _record(
    key: str = "org-a",
    *,
    kind: StateKind = StateKind.PLAN,
    epoch: int = 1,
    seq: int = 1,
    feed_seq: int = 1,
    deleted: bool = False,
    op: StateOp = StateOp.PUT,
    body: dict[str, object] | None = None,
) -> SignedRecord:
    return make_record(
        SECRET,
        kind,
        key,
        {"org_id": key, "rules": []} if body is None else body,
        Version(epoch, seq),
        feed_seq,
        deleted=deleted,
        op=op,
    )


def _round_trip(record: SignedRecord) -> SignedRecord:
    return decode_record(SECRET, record.kind, encode_record(record))


def _tamper(raw: bytes, old: bytes, new: bytes) -> bytes:
    """Edit stored bytes, refusing to produce a vacuous test when the target is not there."""
    edited = raw.replace(old, new)
    assert edited != raw, f"tamper target {old!r} not present in the stored bytes"
    return edited


# --- the cursor model -------------------------------------------------------------------------


def test_feed_seq_is_monotone_across_an_epoch_bump() -> None:
    """A rollback writes (epoch + 1, 0): seq goes BACKWARDS while feed_seq must go forwards.

    A reader whose cursor sat at the pre-rollback position must still select the rollback. If the
    cursor space were `seq`, the new score (0) would be below the cursor and the rollback would
    be skipped forever — the whole kind would have to be re-read to notice, which is R2-02.
    """
    before = _record(epoch=1, seq=7, feed_seq=41)
    rollback = _record(epoch=2, seq=0, feed_seq=42, op=StateOp.ROLLBACK)

    assert rollback.version.seq < before.version.seq
    assert rollback.version > before.version
    assert rollback.feed_seq > before.feed_seq

    cursor = before.feed_seq
    assert rollback.feed_seq > cursor, "a rollback must be selectable by a cursor read"


def test_manifest_feed_seq_below_the_applied_cursor_is_unavailable() -> None:
    """The store went backwards in cursor space: the delta would be empty and stale would serve."""
    manifest = make_manifest(SECRET, StateKind.KS, Version(1, 9), feed_seq=9, count=3)
    raw = encode_manifest(manifest)

    assert decode_manifest(SECRET, StateKind.KS, raw, not_before=ZERO, feed_floor=9).feed_seq == 9
    with pytest.raises(StoreDataUnavailable, match="below the applied cursor"):
        decode_manifest(SECRET, StateKind.KS, raw, not_before=ZERO, feed_floor=10)


def test_manifest_older_than_applied_version_is_unavailable() -> None:
    manifest = make_manifest(SECRET, StateKind.PLAN, Version(1, 4), feed_seq=4, count=2)
    raw = encode_manifest(manifest)

    with pytest.raises(StoreDataUnavailable, match="older than"):
        decode_manifest(SECRET, StateKind.PLAN, raw, not_before=Version(1, 5))


def test_record_must_match_the_index_entry_that_selected_it() -> None:
    """An OLDER genuinely-signed record served under a NEWER index score must be rejected."""
    stale = _record(feed_seq=41)
    fresh = _record(seq=2, feed_seq=42)

    assert record_matches_index(fresh, 42) is True
    assert record_matches_index(stale, 42) is False


# --- signing and tamper rejection -------------------------------------------------------------


def test_record_round_trips_through_the_envelope() -> None:
    record = _record(body={"limit": 10, "nested": {"b": 2, "a": 1}})
    decoded = _round_trip(record)

    assert decoded == record
    assert decoded.body == record.body, "the signed bytes must survive encode/decode exactly"


def test_canonical_body_is_key_order_independent() -> None:
    assert canonical_body({"a": 1, "b": 2}) == canonical_body({"b": 2, "a": 1})


def test_body_bytes_are_not_reserialized_on_verify() -> None:
    """The hash covers bytes, so verification never depends on a serializer round-trip."""
    record = _record(body={"text": "caf\u00e9 \u2014 \u00fcmlaut", "n": 1})
    decoded = _round_trip(record)

    assert decoded.body == record.body
    assert decoded.content_hash == record.content_hash


def test_tampered_body_is_unavailable() -> None:
    record = _record()
    # The envelope carries the body as an escaped JSON string, so tamper the escaped form.
    envelope = _tamper(
        encode_record(record), b'\\"org_id\\":\\"org-a\\"', b'\\"org_id\\":\\"org-b\\"',
    )

    with pytest.raises(StoreDataUnavailable, match="does not match its hash"):
        decode_record(SECRET, record.kind, envelope)


def test_tampered_feed_seq_is_unavailable() -> None:
    """feed_seq is inside the signature, so an index score cannot be restated."""
    record = _record(feed_seq=7)
    envelope = _tamper(encode_record(record), b'"feed_seq":7', b'"feed_seq":9')

    with pytest.raises(StoreDataUnavailable, match="fails its signature"):
        decode_record(SECRET, record.kind, envelope)


def test_tampered_deleted_flag_is_unavailable() -> None:
    """Flipping `deleted` would un-revoke a key or disengage a switch."""
    record = _record(kind=StateKind.KEY, key="hash-1", deleted=True, op=StateOp.REVOKE)
    envelope = _tamper(encode_record(record), b'"deleted":true', b'"deleted":false')

    with pytest.raises(StoreDataUnavailable, match="fails its signature"):
        decode_record(SECRET, record.kind, envelope)


def test_record_signed_for_another_kind_is_unavailable() -> None:
    record = _record(kind=StateKind.KEY, key="hash-1")

    with pytest.raises(StoreDataUnavailable, match="carries another kind"):
        decode_record(SECRET, StateKind.PLAN, encode_record(record))


def test_wrong_secret_is_unavailable() -> None:
    record = _record()

    with pytest.raises(StoreDataUnavailable, match="fails its signature"):
        decode_record(OTHER_SECRET, record.kind, encode_record(record))


def test_manifest_tampered_count_is_unavailable() -> None:
    """count is the completeness proof: it must not be restatable by the store."""
    manifest = make_manifest(SECRET, StateKind.KS, Version(1, 1), feed_seq=1, count=5, on_count=1)
    envelope = _tamper(encode_manifest(manifest), b'"count":5', b'"count":4')

    with pytest.raises(StoreDataUnavailable, match="fails its signature"):
        decode_manifest(SECRET, StateKind.KS, envelope, not_before=ZERO)


def test_manifest_on_count_above_count_is_unavailable() -> None:
    manifest = make_manifest(SECRET, StateKind.KS, Version(1, 1), feed_seq=1, count=2, on_count=3)

    with pytest.raises(StoreDataUnavailable, match="engaged of"):
        decode_manifest(SECRET, StateKind.KS, encode_manifest(manifest), not_before=ZERO)


# --- malformed and missing input ---------------------------------------------------------------


def test_missing_manifest_is_unavailable_not_empty() -> None:
    with pytest.raises(StoreDataUnavailable, match="manifest missing"):
        decode_manifest(SECRET, StateKind.PLAN, None, not_before=ZERO)


def test_missing_record_is_unavailable_not_empty() -> None:
    with pytest.raises(StoreDataUnavailable, match="record missing"):
        decode_record(SECRET, StateKind.PLAN, None)


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"[1,2,3]",
        b'"a string"',
        b"123",
        b"{}",
    ],
)
def test_malformed_record_is_unavailable(raw: bytes) -> None:
    with pytest.raises(StoreDataUnavailable):
        decode_record(SECRET, StateKind.PLAN, raw)


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"[]",
        b"{}",
        b'{"kind":"plan","epoch":1,"seq":1,"feed_seq":1,"count":1,"on_count":0}',
    ],
)
def test_malformed_manifest_is_unavailable(raw: bytes) -> None:
    with pytest.raises(StoreDataUnavailable):
        decode_manifest(SECRET, StateKind.PLAN, raw, not_before=ZERO)


def test_negative_feed_seq_in_an_envelope_is_unavailable() -> None:
    record = _record(feed_seq=1)
    envelope = _tamper(encode_record(record), b'"feed_seq":1', b'"feed_seq":-1')

    with pytest.raises(StoreDataUnavailable, match="is negative"):
        decode_record(SECRET, record.kind, envelope)


def test_unknown_op_is_unavailable() -> None:
    record = _record()
    envelope = _tamper(encode_record(record), b'"op":"put"', b'"op":"wipe"')

    with pytest.raises(StoreDataUnavailable, match="unknown op"):
        decode_record(SECRET, record.kind, envelope)


def test_boolean_is_not_accepted_where_an_integer_is_required() -> None:
    record = _record()
    envelope = _tamper(encode_record(record), b'"seq":1', b'"seq":true')

    with pytest.raises(StoreDataUnavailable, match="not an integer"):
        decode_record(SECRET, record.kind, envelope)


def test_version_ordering_is_epoch_then_seq() -> None:
    assert Version(1, 2) < Version(1, 3)
    assert Version(1, 99) < Version(2, 0)
    assert ZERO < Version(1, 0)
