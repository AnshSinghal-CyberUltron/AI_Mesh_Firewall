"""Sign, encode and verify shared-state records and manifests. The only signing implementation.

The control-plane writer and every gateway reader import this module, so a signature can never
drift between the two planes.

Two anti-regress rules live in `decode_manifest`, and they are the whole reason a reader can
trust an incremental update:

* `not_before` — a manifest older than a version this process already applied means the store
  copy regressed (a failed-over replica, a partial restore). UNAVAILABLE, not "nothing changed".
* `feed_floor` — the same check in cursor space. A manifest whose `feed_seq` is below this
  reader's cursor means the published copy went backwards, so the delta the reader would compute
  would be empty and it would serve stale state believing it was current.

GW05b's freshness stamp is signed here too, and for one reason: `state_control` may import only
`domain.state` and this module, so there is exactly ONE signing implementation for both planes.
A stamp signed by the re-hydrator and verified by a gateway cannot drift.

The signature domains are versioned (`rec2`, `meta2`, `stamp2`). A payload signed under an older
domain, or under a sibling's domain, fails verification rather than being silently reinterpreted
-- a stamp envelope and a manifest envelope must never be substitutable under the same key.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping

from gateway_v2.domain.state import (
    Cursor,
    Manifest,
    SignedRecord,
    Stamp,
    StateKind,
    StateOp,
    StoreDataUnavailable,
    Version,
)

RECORD_DOMAIN = "rec2"
MANIFEST_DOMAIN = "meta2"
STAMP_DOMAIN = "stamp2"

STAMP_WHERE = "freshness stamp"
"""Error prose for stamp decoding. A stamp is namespace-wide, so it carries no kind."""


def canonical_body(body: Mapping[str, object]) -> bytes:
    """Deterministic bytes for a record body. Sorted keys, no incidental whitespace."""
    return json.dumps(
        body,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _mac(secret: bytes, text: str) -> str:
    return hmac.new(secret, text.encode("utf-8"), hashlib.sha256).hexdigest()


def record_signature(
    secret: bytes,
    kind: StateKind,
    key: str,
    version: Version,
    feed_seq: int,
    content_hash: str,
    deleted: bool,
    op: StateOp,
) -> str:
    """`feed_seq` is INSIDE the signature, so the index score it is checked against is signed."""
    parts = (
        RECORD_DOMAIN,
        kind.value,
        key,
        str(version.epoch),
        str(version.seq),
        str(feed_seq),
        content_hash,
        str(int(deleted)),
        op.value,
    )
    return _mac(secret, "\n".join(parts))


def manifest_signature(
    secret: bytes,
    kind: StateKind,
    version: Version,
    feed_seq: int,
    count: int,
    on_count: int,
) -> str:
    parts = (
        MANIFEST_DOMAIN,
        kind.value,
        str(version.epoch),
        str(version.seq),
        str(feed_seq),
        str(count),
        str(on_count),
    )
    return _mac(secret, "\n".join(parts))


def stamp_millis(seconds: float) -> int:
    """Millisecond granularity, which is what both the signature and the envelope carry.

    The stamp's timestamp is signed as an INTEGER. Signing the float would make every signature
    depend on float text formatting staying identical across Python versions and across the two
    planes -- the same coupling the canonical record body exists to remove.
    """
    return int(seconds * 1000)


def stamp_signature(
    secret: bytes,
    verified_at_ms: int,
    cursors: Mapping[StateKind, Cursor],
    by: str,
    *,
    deep: bool,
    degraded: bool,
) -> str:
    """Sign a freshness stamp. Cursors are sorted by kind, so the input is order-independent.

    The cursor count is signed as its own part, which makes the layout self-describing: a
    newline smuggled into `by` cannot shift the field boundaries into a second valid reading of
    some other legitimately signed stamp. `make_stamp` rejects such a `by` outright as well.
    """
    parts = [
        STAMP_DOMAIN,
        str(verified_at_ms),
        by,
        str(int(deep)),
        str(int(degraded)),
        str(len(cursors)),
    ]
    for kind in sorted(cursors, key=lambda entry: entry.value):
        cursor = cursors[kind]
        parts.append(
            f"{kind.value}={cursor.version.epoch}.{cursor.version.seq}:{cursor.feed_seq}",
        )
    return _mac(secret, "\n".join(parts))


def make_record(
    secret: bytes,
    kind: StateKind,
    key: str,
    body: Mapping[str, object],
    version: Version,
    feed_seq: int,
    *,
    deleted: bool = False,
    op: StateOp = StateOp.PUT,
) -> SignedRecord:
    canonical = canonical_body(body)
    digest = body_hash(canonical)
    return SignedRecord(
        kind=kind,
        key=key,
        version=version,
        feed_seq=feed_seq,
        content_hash=digest,
        deleted=deleted,
        op=op,
        signature=record_signature(
            secret, kind, key, version, feed_seq, digest, deleted, op,
        ),
        body=canonical,
    )


def make_manifest(
    secret: bytes,
    kind: StateKind,
    version: Version,
    feed_seq: int,
    count: int,
    on_count: int = 0,
) -> Manifest:
    return Manifest(
        kind=kind,
        version=version,
        feed_seq=feed_seq,
        count=count,
        on_count=on_count,
        signature=manifest_signature(secret, kind, version, feed_seq, count, on_count),
    )


def make_stamp(
    secret: bytes,
    verified_at: float,
    cursors: Mapping[StateKind, Cursor],
    by: str,
    *,
    deep: bool = False,
    degraded: bool = False,
) -> Stamp:
    """Build a signed stamp. Refuses to mint one that a reader would have to refuse.

    Every rejection here is a fail-closed decision made at the WRITER, so an unusable stamp is
    never published: a partial stamp would leave an uncovered kind floored at ZERO while the
    process believed itself verified, which is SP1 for that kind.
    """
    if "\n" in by:
        raise ValueError("rehydrator id must not contain a newline")
    covered = dict(cursors)
    missing = sorted(kind.value for kind in StateKind if kind not in covered)
    if missing:
        raise ValueError(f"a stamp must cover every kind; missing {', '.join(missing)}")
    verified_at_ms = stamp_millis(verified_at)
    if verified_at_ms < 0:
        raise ValueError(f"verified_at {verified_at} is before the epoch")
    return Stamp(
        # Snap to the millisecond that was actually signed, so a stamp equals its own round trip.
        verified_at=verified_at_ms / 1000,
        cursors=covered,
        by=by,
        deep=deep,
        degraded=degraded,
        signature=stamp_signature(
            secret, verified_at_ms, covered, by, deep=deep, degraded=degraded,
        ),
    )


def encode_record(record: SignedRecord) -> bytes:
    """The stored envelope. `body` is the canonical text, so the signed bytes round-trip exactly."""
    envelope = {
        "kind": record.kind.value,
        "key": record.key,
        "epoch": record.version.epoch,
        "seq": record.version.seq,
        "feed_seq": record.feed_seq,
        "hash": record.content_hash,
        "deleted": record.deleted,
        "op": record.op.value,
        "sig": record.signature,
        "body": record.body.decode("utf-8"),
    }
    return json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def encode_manifest(manifest: Manifest) -> bytes:
    envelope = {
        "kind": manifest.kind.value,
        "epoch": manifest.version.epoch,
        "seq": manifest.version.seq,
        "feed_seq": manifest.feed_seq,
        "count": manifest.count,
        "on_count": manifest.on_count,
        "sig": manifest.signature,
    }
    return json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def encode_stamp(stamp: Stamp) -> bytes:
    """The stored envelope for `{rv2}:stamp`. One key for the whole namespace."""
    envelope = {
        "verified_at_ms": stamp_millis(stamp.verified_at),
        "cursors": {
            kind.value: [cursor.version.epoch, cursor.version.seq, cursor.feed_seq]
            for kind, cursor in sorted(stamp.cursors.items(), key=lambda item: item[0].value)
        },
        "by": stamp.by,
        "deep": stamp.deep,
        "degraded": stamp.degraded,
        "sig": stamp.signature,
    }
    return json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _envelope(raw: bytes | str | None, where: str) -> Mapping[str, object]:
    """`where` already names what is being decoded, e.g. "ks manifest", "freshness stamp"."""
    if raw is None:
        raise StoreDataUnavailable(
            f"{where} missing (store empty, flushed or not yet re-hydrated)",
        )
    try:
        parsed: object = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise StoreDataUnavailable(f"{where} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise StoreDataUnavailable(f"{where} is not a JSON object")
    return parsed


def _text(env: Mapping[str, object], name: str, where: str) -> str:
    value = env.get(name)
    if not isinstance(value, str):
        raise StoreDataUnavailable(f"{where} field {name!r} is not a string")
    return value


def _whole_value(value: object, where: str) -> int:
    """`bool` is an `int` in Python, so it is excluded explicitly: `True` is not a version."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise StoreDataUnavailable(f"{where} is not an integer")
    if value < 0:
        raise StoreDataUnavailable(f"{where} is negative")
    return value


def _whole(env: Mapping[str, object], name: str, where: str) -> int:
    return _whole_value(env.get(name), f"{where} field {name!r}")


def _flag(env: Mapping[str, object], name: str, where: str) -> bool:
    value = env.get(name)
    if not isinstance(value, bool):
        raise StoreDataUnavailable(f"{where} field {name!r} is not a boolean")
    return value


def decode_record(secret: bytes, kind: StateKind, raw: bytes | str | None) -> SignedRecord:
    """Verify one record. Any defect is UNAVAILABLE, never a benign empty value."""
    where = f"{kind.value} record"
    env = _envelope(raw, where)
    if _text(env, "kind", where) != kind.value:
        raise StoreDataUnavailable(f"{kind.value} record carries another kind")
    key = _text(env, "key", where)
    version = Version(
        epoch=_whole(env, "epoch", where),
        seq=_whole(env, "seq", where),
    )
    feed_seq = _whole(env, "feed_seq", where)
    content_hash = _text(env, "hash", where)
    deleted = _flag(env, "deleted", where)
    signature = _text(env, "sig", where)
    body = _text(env, "body", where).encode("utf-8")
    try:
        op = StateOp(_text(env, "op", where))
    except ValueError as exc:
        raise StoreDataUnavailable(f"{kind.value} record {key!r} has an unknown op") from exc
    if body_hash(body) != content_hash:
        raise StoreDataUnavailable(f"{kind.value} record {key!r} body does not match its hash")
    expected = record_signature(
        secret, kind, key, version, feed_seq, content_hash, deleted, op,
    )
    if not hmac.compare_digest(signature, expected):
        raise StoreDataUnavailable(f"{kind.value} record {key!r} fails its signature")
    return SignedRecord(
        kind=kind,
        key=key,
        version=version,
        feed_seq=feed_seq,
        content_hash=content_hash,
        deleted=deleted,
        op=op,
        signature=signature,
        body=body,
    )


def decode_manifest(
    secret: bytes,
    kind: StateKind,
    raw: bytes | str | None,
    *,
    not_before: Version,
    feed_floor: int = 0,
) -> Manifest:
    """Verify a kind's head and refuse anything older than this process has already applied."""
    where = f"{kind.value} manifest"
    env = _envelope(raw, where)
    if _text(env, "kind", where) != kind.value:
        raise StoreDataUnavailable(f"{kind.value} manifest carries another kind")
    version = Version(
        epoch=_whole(env, "epoch", where),
        seq=_whole(env, "seq", where),
    )
    feed_seq = _whole(env, "feed_seq", where)
    count = _whole(env, "count", where)
    on_count = _whole(env, "on_count", where)
    signature = _text(env, "sig", where)
    expected = manifest_signature(secret, kind, version, feed_seq, count, on_count)
    if not hmac.compare_digest(signature, expected):
        raise StoreDataUnavailable(f"{kind.value} manifest fails its signature")
    if on_count > count:
        raise StoreDataUnavailable(
            f"{kind.value} manifest claims {on_count} engaged of {count} records",
        )
    if version < not_before:
        raise StoreDataUnavailable(
            f"{kind.value} manifest {version} is older than {not_before} already applied",
        )
    if feed_seq < feed_floor:
        raise StoreDataUnavailable(
            f"{kind.value} manifest feed_seq {feed_seq} is below the applied cursor {feed_floor}",
        )
    return Manifest(
        kind=kind,
        version=version,
        feed_seq=feed_seq,
        count=count,
        on_count=on_count,
        signature=signature,
    )


def _stamp_cursors(env: Mapping[str, object]) -> dict[StateKind, Cursor]:
    """Parse the per-kind cursors and refuse a stamp that does not cover every kind."""
    raw = env.get("cursors")
    if not isinstance(raw, dict):
        raise StoreDataUnavailable(f"{STAMP_WHERE} field 'cursors' is not an object")
    out: dict[StateKind, Cursor] = {}
    for name, triple in raw.items():
        try:
            kind = StateKind(name)
        except ValueError as exc:
            raise StoreDataUnavailable(
                f"{STAMP_WHERE} names an unknown kind {name!r}",
            ) from exc
        if not isinstance(triple, list) or len(triple) != 3:
            raise StoreDataUnavailable(
                f"{STAMP_WHERE} cursor for {kind.value} is not [epoch, seq, feed_seq]",
            )
        where = f"{STAMP_WHERE} cursor for {kind.value}"
        numbers = [_whole_value(value, where) for value in triple]
        out[kind] = Cursor(version=Version(numbers[0], numbers[1]), feed_seq=numbers[2])
    missing = sorted(kind.value for kind in StateKind if kind not in out)
    if missing:
        raise StoreDataUnavailable(
            f"{STAMP_WHERE} does not cover {', '.join(missing)}: an uncovered kind would sit at "
            "zero while the process believed its state verified",
        )
    return out


def decode_stamp(secret: bytes, raw: bytes | str | None) -> Stamp:
    """Verify a freshness stamp. Missing, partial, malformed or forged are all UNAVAILABLE.

    The caller separates ABSENT from INVALID for metrics -- a missing stamp usually means no
    re-hydrator is running, while an invalid one is a security signal -- by testing `raw is None`
    before it gets here, rather than by catching a second exception type.
    """
    env = _envelope(raw, STAMP_WHERE)
    verified_at_ms = _whole(env, "verified_at_ms", STAMP_WHERE)
    cursors = _stamp_cursors(env)
    by = _text(env, "by", STAMP_WHERE)
    deep = _flag(env, "deep", STAMP_WHERE)
    degraded = _flag(env, "degraded", STAMP_WHERE)
    signature = _text(env, "sig", STAMP_WHERE)
    expected = stamp_signature(
        secret, verified_at_ms, cursors, by, deep=deep, degraded=degraded,
    )
    if not hmac.compare_digest(signature, expected):
        raise StoreDataUnavailable(f"{STAMP_WHERE} fails its signature")
    return Stamp(
        verified_at=verified_at_ms / 1000,
        cursors=cursors,
        by=by,
        deep=deep,
        degraded=degraded,
        signature=signature,
    )


def record_matches_index(record: SignedRecord, score: int) -> bool:
    """A record fetched by an index entry must be the generation that entry points at.

    Without this, a store could serve an OLDER signed version of a record under a NEWER index
    score and the reader would accept it: every individual check passes, because the old record
    is genuinely signed. The score is in the record's signature, so the pair must agree.
    """
    return record.feed_seq == score
