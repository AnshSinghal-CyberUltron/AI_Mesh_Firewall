"""GW05b phase 1: the freshness stamp as signed data.

Nothing reads or writes `{rv2}:stamp` yet, so every test here is about the one question phase 1
answers: can a gateway tell a re-hydrator's genuine assertion from anything else?

The cases that carry real weight are the two completeness ones. A stamp that omits a kind would
leave that kind's reader floored at ZERO while the process believed its state verified -- SP1
for one kind, silently -- so `make_stamp` refuses to mint it and `decode_stamp` refuses to
accept it. And the domain-separation pair proves a manifest envelope and a stamp envelope are
not substitutable under one key, which is why the signature domains are tagged at all.
"""

from __future__ import annotations

import json

import pytest

from gateway_v2.domain.state import (
    START,
    ZERO,
    Cursor,
    StateKind,
    StoreDataUnavailable,
    Version,
)
from gateway_v2.runtime.state_sig import (
    decode_manifest,
    decode_stamp,
    encode_manifest,
    encode_stamp,
    make_manifest,
    make_stamp,
    stamp_millis,
    stamp_signature,
)

SECRET = b"gw05b-test-signing-secret"
OTHER_SECRET = b"gw05b-test-signing-secret-rotated"

VERIFIED_AT = 1_793_000_000.125
BY = "rehydrator-a:4711"


def _cursors(**overrides: Cursor) -> dict[StateKind, Cursor]:
    """Every kind covered, which is the only shape a stamp is allowed to have."""
    base = {
        StateKind.PLAN: Cursor(Version(1, 40), 40),
        StateKind.KEY: Cursor(Version(1, 12), 12),
        StateKind.KS: Cursor(Version(2, 3), 57),
        StateKind.BUDGET: Cursor(Version(1, 8), 8),
    }
    for name, cursor in overrides.items():
        base[StateKind(name)] = cursor
    return base


def _stamp(**kwargs: object) -> bytes:
    fields: dict[str, object] = {
        "verified_at": VERIFIED_AT,
        "cursors": _cursors(),
        "by": BY,
        "deep": False,
        "degraded": False,
    }
    fields.update(kwargs)
    return encode_stamp(make_stamp(SECRET, **fields))  # type: ignore[arg-type]


def _tamper(raw: bytes, **fields: object) -> bytes:
    """Rewrite envelope fields WITHOUT re-signing, which is what a tampered store looks like."""
    envelope = json.loads(raw)
    for name, value in fields.items():
        if value is None:
            envelope.pop(name, None)
        else:
            envelope[name] = value
    return json.dumps(envelope, separators=(",", ":")).encode("utf-8")


# --- round trip ----------------------------------------------------------------------------------


def test_a_stamp_survives_its_own_round_trip() -> None:
    original = make_stamp(SECRET, VERIFIED_AT, _cursors(), BY, deep=True, degraded=False)
    decoded = decode_stamp(SECRET, encode_stamp(original))
    assert decoded == original
    assert decoded.verified_at == VERIFIED_AT
    assert decoded.by == BY
    assert decoded.deep is True
    assert decoded.degraded is False
    assert dict(decoded.cursors) == _cursors()


def test_the_timestamp_is_snapped_to_the_millisecond_that_was_signed() -> None:
    """Sub-millisecond precision is dropped at MINT time, not at decode time.

    If `make_stamp` kept the full float while signing only the milliseconds, a stamp would not
    equal its own round trip and every equality assertion downstream would be subtly wrong.
    """
    stamp = make_stamp(SECRET, 1_793_000_000.123_456, _cursors(), BY)
    assert stamp.verified_at == 1_793_000_000.123
    assert decode_stamp(SECRET, encode_stamp(stamp)).verified_at == stamp.verified_at


def test_cursor_order_does_not_change_the_signature() -> None:
    """Cursors sort before signing, so two writers building the same dict differently agree."""
    forward = _cursors()
    reversed_ = dict(reversed(list(forward.items())))
    assert list(forward) != list(reversed_)
    assert stamp_signature(
        SECRET, stamp_millis(VERIFIED_AT), forward, BY, deep=False, degraded=False,
    ) == stamp_signature(
        SECRET, stamp_millis(VERIFIED_AT), reversed_, BY, deep=False, degraded=False,
    )


# --- forgery and tampering -----------------------------------------------------------------------


def test_a_stamp_signed_under_another_secret_is_refused() -> None:
    raw = encode_stamp(make_stamp(OTHER_SECRET, VERIFIED_AT, _cursors(), BY))
    with pytest.raises(StoreDataUnavailable, match="fails its signature"):
        decode_stamp(SECRET, raw)


@pytest.mark.parametrize(
    "field",
    ["verified_at_ms", "by", "deep", "degraded", "cursors", "sig"],
)
def test_editing_any_signed_field_breaks_the_signature(field: str) -> None:
    replacements: dict[str, object] = {
        "verified_at_ms": stamp_millis(VERIFIED_AT) + 1,
        "by": "rehydrator-b:9",
        "deep": True,
        "degraded": True,
        "cursors": {"plan": [1, 41, 41], "key": [1, 12, 12], "ks": [2, 3, 57], "budget": [1, 8, 8]},
        "sig": "0" * 64,
    }
    with pytest.raises(StoreDataUnavailable, match="fails its signature"):
        decode_stamp(SECRET, _tamper(_stamp(), **{field: replacements[field]}))


def test_rolling_one_cursor_backwards_breaks_the_signature() -> None:
    """The attack this blocks: keep a genuine stamp's timestamp, lower the floor it attests."""
    rolled = {"plan": [1, 4, 4], "key": [1, 12, 12], "ks": [2, 3, 57], "budget": [1, 8, 8]}
    with pytest.raises(StoreDataUnavailable, match="fails its signature"):
        decode_stamp(SECRET, _tamper(_stamp(), cursors=rolled))


# --- completeness --------------------------------------------------------------------------------


@pytest.mark.parametrize("dropped", list(StateKind))
def test_minting_a_stamp_that_omits_a_kind_is_refused(dropped: StateKind) -> None:
    partial = {kind: cursor for kind, cursor in _cursors().items() if kind is not dropped}
    with pytest.raises(ValueError, match=f"missing {dropped.value}"):
        make_stamp(SECRET, VERIFIED_AT, partial, BY)


@pytest.mark.parametrize("dropped", list(StateKind))
def test_decoding_a_stamp_that_omits_a_kind_is_refused(dropped: StateKind) -> None:
    """Signed, authentic, and still refused: a partial stamp is SP1 for the uncovered kind.

    Built by signing the partial set directly, bypassing `make_stamp`, so this proves the READER
    fails closed rather than relying on the writer's guard.
    """
    partial = {kind: cursor for kind, cursor in _cursors().items() if kind is not dropped}
    envelope = {
        "verified_at_ms": stamp_millis(VERIFIED_AT),
        "cursors": {
            kind.value: [cursor.version.epoch, cursor.version.seq, cursor.feed_seq]
            for kind, cursor in sorted(partial.items(), key=lambda item: item[0].value)
        },
        "by": BY,
        "deep": False,
        "degraded": False,
        "sig": stamp_signature(
            SECRET, stamp_millis(VERIFIED_AT), partial, BY, deep=False, degraded=False,
        ),
    }
    raw = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    with pytest.raises(StoreDataUnavailable, match=f"does not cover {dropped.value}"):
        decode_stamp(SECRET, raw)


def test_an_unknown_kind_in_a_stamp_is_refused() -> None:
    cursors = {"plan": [1, 40, 40], "key": [1, 12, 12], "ks": [2, 3, 57], "quota": [1, 1, 1]}
    with pytest.raises(StoreDataUnavailable, match="unknown kind 'quota'"):
        decode_stamp(SECRET, _tamper(_stamp(), cursors=cursors))


# --- malformed -----------------------------------------------------------------------------------


def test_an_absent_stamp_is_refused_as_missing() -> None:
    with pytest.raises(StoreDataUnavailable, match="freshness stamp missing"):
        decode_stamp(SECRET, None)


def test_a_non_json_stamp_is_refused() -> None:
    with pytest.raises(StoreDataUnavailable, match="not valid JSON"):
        decode_stamp(SECRET, b"{not json")


def test_a_json_array_is_not_a_stamp() -> None:
    with pytest.raises(StoreDataUnavailable, match="not a JSON object"):
        decode_stamp(SECRET, b"[]")


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("verified_at_ms", "'verified_at_ms' is not an integer"),
        ("by", "'by' is not a string"),
        ("deep", "'deep' is not a boolean"),
        ("degraded", "'degraded' is not a boolean"),
        ("sig", "'sig' is not a string"),
        ("cursors", "'cursors' is not an object"),
    ],
)
def test_a_missing_field_is_refused_by_type(field: str, message: str) -> None:
    with pytest.raises(StoreDataUnavailable, match=message):
        decode_stamp(SECRET, _tamper(_stamp(), **{field: None}))


def test_a_negative_timestamp_is_refused() -> None:
    with pytest.raises(StoreDataUnavailable, match="'verified_at_ms' is negative"):
        decode_stamp(SECRET, _tamper(_stamp(), verified_at_ms=-1))


def test_a_boolean_is_not_a_timestamp() -> None:
    """`bool` is an `int` in Python, so `True` would otherwise read as 1 ms past the epoch."""
    with pytest.raises(StoreDataUnavailable, match="'verified_at_ms' is not an integer"):
        decode_stamp(SECRET, _tamper(_stamp(), verified_at_ms=True))


@pytest.mark.parametrize("triple", [[1, 40], [1, 40, 40, 40], "1.40:40", 40, None])
def test_a_cursor_that_is_not_a_triple_is_refused(triple: object) -> None:
    cursors = {"plan": triple, "key": [1, 12, 12], "ks": [2, 3, 57], "budget": [1, 8, 8]}
    with pytest.raises(StoreDataUnavailable, match=r"cursor for plan is not \[epoch"):
        decode_stamp(SECRET, _tamper(_stamp(), cursors=cursors))


def test_a_negative_position_in_a_cursor_is_refused() -> None:
    cursors = {"plan": [1, 40, -40], "key": [1, 12, 12], "ks": [2, 3, 57], "budget": [1, 8, 8]}
    with pytest.raises(StoreDataUnavailable, match="cursor for plan is negative"):
        decode_stamp(SECRET, _tamper(_stamp(), cursors=cursors))


def test_a_newline_in_the_rehydrator_id_is_refused_at_mint_time() -> None:
    """Removes the whole field-confusion class rather than relying on the layout being rigid."""
    with pytest.raises(ValueError, match="must not contain a newline"):
        make_stamp(SECRET, VERIFIED_AT, _cursors(), "rehydrator-a\n0\n0\n6")


def test_a_timestamp_before_the_epoch_is_refused_at_mint_time() -> None:
    with pytest.raises(ValueError, match="before the epoch"):
        make_stamp(SECRET, -1.0, _cursors(), BY)


# --- domain separation ---------------------------------------------------------------------------


def test_a_manifest_envelope_is_not_a_stamp() -> None:
    manifest = encode_manifest(make_manifest(SECRET, StateKind.KS, Version(2, 3), 57, 57, 4))
    with pytest.raises(StoreDataUnavailable, match="freshness stamp field"):
        decode_stamp(SECRET, manifest)


def test_a_stamp_envelope_is_not_a_manifest() -> None:
    with pytest.raises(StoreDataUnavailable, match="ks manifest field"):
        decode_manifest(SECRET, StateKind.KS, _stamp(), not_before=ZERO)


def test_the_stamp_domain_separates_it_from_a_manifest_mac() -> None:
    """Same secret, same numbers, different domain tag: the two MACs must not coincide."""
    cursor = Cursor(Version(2, 3), 57)
    stamp_mac = stamp_signature(
        SECRET, 57, {kind: cursor for kind in StateKind}, "ks", deep=False, degraded=False,
    )
    manifest = make_manifest(SECRET, StateKind.KS, Version(2, 3), 57, 57, 4)
    assert stamp_mac != manifest.signature


# --- the cursor type, now that it lives in the domain layer --------------------------------------


def test_start_is_the_floorless_cursor_r2_03_is_about() -> None:
    assert START == Cursor(version=ZERO, feed_seq=0)
    assert START.version == ZERO
    assert START.feed_seq == 0


def test_a_cursor_advances_without_mutating() -> None:
    moved = START.advanced(Version(1, 9), 9)
    assert moved == Cursor(Version(1, 9), 9)
    assert START == Cursor(version=ZERO, feed_seq=0), "cursors are frozen"
