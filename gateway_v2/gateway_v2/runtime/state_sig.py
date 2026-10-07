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

The signature domains are versioned (`rec2`, `meta2`). A payload signed under an older domain
fails verification rather than being silently reinterpreted.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping

from gateway_v2.domain.state import (
    Manifest,
    SignedRecord,
    StateKind,
    StateOp,
    StoreDataUnavailable,
    Version,
)

RECORD_DOMAIN = "rec2"
MANIFEST_DOMAIN = "meta2"


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


def _envelope(kind: StateKind, raw: bytes | str | None, what: str) -> Mapping[str, object]:
    if raw is None:
        raise StoreDataUnavailable(
            f"{kind.value} {what} missing (store empty, flushed or not yet re-hydrated)",
        )
    try:
        parsed: object = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise StoreDataUnavailable(f"{kind.value} {what} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise StoreDataUnavailable(f"{kind.value} {what} is not a JSON object")
    return parsed


def _text(env: Mapping[str, object], name: str, kind: StateKind, what: str) -> str:
    value = env.get(name)
    if not isinstance(value, str):
        raise StoreDataUnavailable(f"{kind.value} {what} field {name!r} is not a string")
    return value


def _whole(env: Mapping[str, object], name: str, kind: StateKind, what: str) -> int:
    value = env.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise StoreDataUnavailable(f"{kind.value} {what} field {name!r} is not an integer")
    if value < 0:
        raise StoreDataUnavailable(f"{kind.value} {what} field {name!r} is negative")
    return value


def _flag(env: Mapping[str, object], name: str, kind: StateKind, what: str) -> bool:
    value = env.get(name)
    if not isinstance(value, bool):
        raise StoreDataUnavailable(f"{kind.value} {what} field {name!r} is not a boolean")
    return value


def decode_record(secret: bytes, kind: StateKind, raw: bytes | str | None) -> SignedRecord:
    """Verify one record. Any defect is UNAVAILABLE, never a benign empty value."""
    env = _envelope(kind, raw, "record")
    if _text(env, "kind", kind, "record") != kind.value:
        raise StoreDataUnavailable(f"{kind.value} record carries another kind")
    key = _text(env, "key", kind, "record")
    version = Version(
        epoch=_whole(env, "epoch", kind, "record"),
        seq=_whole(env, "seq", kind, "record"),
    )
    feed_seq = _whole(env, "feed_seq", kind, "record")
    content_hash = _text(env, "hash", kind, "record")
    deleted = _flag(env, "deleted", kind, "record")
    signature = _text(env, "sig", kind, "record")
    body = _text(env, "body", kind, "record").encode("utf-8")
    try:
        op = StateOp(_text(env, "op", kind, "record"))
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
    env = _envelope(kind, raw, "manifest")
    if _text(env, "kind", kind, "manifest") != kind.value:
        raise StoreDataUnavailable(f"{kind.value} manifest carries another kind")
    version = Version(
        epoch=_whole(env, "epoch", kind, "manifest"),
        seq=_whole(env, "seq", kind, "manifest"),
    )
    feed_seq = _whole(env, "feed_seq", kind, "manifest")
    count = _whole(env, "count", kind, "manifest")
    on_count = _whole(env, "on_count", kind, "manifest")
    signature = _text(env, "sig", kind, "manifest")
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


def record_matches_index(record: SignedRecord, score: int) -> bool:
    """A record fetched by an index entry must be the generation that entry points at.

    Without this, a store could serve an OLDER signed version of a record under a NEWER index
    score and the reader would accept it: every individual check passes, because the old record
    is genuinely signed. The score is in the record's signature, so the pair must agree.
    """
    return record.feed_seq == score
