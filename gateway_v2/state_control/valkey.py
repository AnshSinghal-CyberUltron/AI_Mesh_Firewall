"""The writer's Valkey/Redis adapter. Five commands per write, whatever the estate size.

    SET or HSET   the one record
    ZADD          its index entry at feed_seq
    SADD / SREM   the engaged set              (kill switch only)
    SET           the manifest                 LAST, always
    PUBLISH       "<kind>:<feed_seq>"           a latency nudge, not a feed

RC2 issued `DEL` plus `HSET` of a kind's every record here (R2-02). `publish_kind` still does,
and is still correct for the re-hydrator restoring a flushed store, where the whole kind IS the
change. It stays a single MULTI on purpose: chunking it would let a reader observe a half-rebuilt
index under the old manifest, and the restore window is the thing being minimised, not the
atomicity.

Two ordering rules, both relied on by the reader:

* **The manifest is written last.** A reader therefore sees either the old manifest (and skips,
  because `feed_seq` has not moved) or the new one, whose index entries are already present.
* **A publish never moves the manifest backwards.** `WATCH` on the manifest key plus a decode of
  what is already there means a slow publisher of an older generation loses to the newer one,
  rather than overwriting it. The decode uses the signing secret, so the comparison is made
  against a manifest that actually verifies.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from gateway_v2.domain.state import (
    ZERO,
    Manifest,
    SignedRecord,
    Stamp,
    StateKind,
    StoreDataUnavailable,
)
from gateway_v2.runtime.state_sig import (
    decode_manifest,
    decode_stamp,
    encode_manifest,
    encode_record,
    encode_stamp,
)
from gateway_v2.runtime.store_keys import HASH_KINDS, KEYS, StoreKeys
from state_control.publisher import StoredHead

LOG = logging.getLogger("amf.state.valkey")

WATCH_ATTEMPTS = 20


class ValkeyPublisher:
    """Satisfies `state_control.StatePublisher` over a synchronous redis client.

    The control plane is not a serving loop, so a synchronous client is the simpler and more
    honest choice here; the data plane's reader is async and separate.
    """

    def __init__(
        self,
        client: Any,
        secret: bytes,
        keys: StoreKeys = KEYS,
        *,
        chunk: int = 500,
    ) -> None:
        if not secret:
            raise ValueError("a state signing secret is required")
        self._client = client
        self._secret = secret
        self._keys = keys
        self._chunk = max(chunk, 1)

    # --- reads ---------------------------------------------------------------------------------

    def stored_feed_seq(self, kind: StateKind) -> int | None:
        raw = self._client.get(self._keys.manifest(kind))
        return self._feed_seq_of(kind, raw)

    def stored_head(self, kind: StateKind) -> StoredHead:
        """O(1). The re-hydrator's per-round read."""
        index = self._keys.index(kind)
        with self._client.pipeline(transaction=True) as pipe:
            pipe.get(self._keys.manifest(kind))
            pipe.zcard(index)
            pipe.zrevrange(index, 0, 0, withscores=True)
            pipe.scard(self._keys.engaged(kind))
            manifest_raw, count, top, engaged = pipe.execute()
        return StoredHead(
            manifest_raw=manifest_raw if isinstance(manifest_raw, bytes) else None,
            index_count=int(count or 0),
            index_top=0 if not top else int(top[0][1]),
            engaged_count=int(engaged or 0),
        )

    def stored_index(self, kind: StateKind) -> Mapping[str, int]:
        """O(records). The DEEP verification path only."""
        entries = self._client.zrange(self._keys.index(kind), 0, -1, withscores=True)
        return {_text(member): int(score) for member, score in entries}

    def read_stamp(self) -> bytes | None:
        raw = self._client.get(self._keys.stamp)
        return raw if isinstance(raw, bytes) else None

    # --- the freshness stamp -------------------------------------------------------------------

    def put_stamp(self, stamp: Stamp) -> bool:
        """WATCH-guarded, so a slower re-hydrator never replaces a newer round's stamp.

        Two or more re-hydrators run at once by design (R2-04: one is a single point of global
        non-enforcement), and their rounds start at slightly different moments. Without the
        guard, the slower one's `SET` would land last and move `verified_at` BACKWARDS, which is
        the one thing a freshness clock must never do.

        A held stamp that does not verify is OVERWRITTEN rather than respected. Trusting its
        timestamp would let anyone who can write the key once plant an un-decodable far-future
        value and block every genuine stamp from then on.
        """
        name = self._keys.stamp
        payload = encode_stamp(stamp)
        with self._client.pipeline() as pipe:
            for _ in range(WATCH_ATTEMPTS):
                try:
                    pipe.watch(name)
                    if self._stamp_is_newer(pipe.get(name), stamp):
                        pipe.reset()
                        return False
                    pipe.multi()
                    pipe.set(name, payload)
                    pipe.execute()
                    return True
                except _WATCH_ERRORS:
                    continue
        raise RuntimeError("freshness stamp kept racing other re-hydrators")

    def _stamp_is_newer(self, raw: object, stamp: Stamp) -> bool:
        if not isinstance(raw, (bytes, str)):
            return False
        try:
            held = decode_stamp(self._secret, raw)
        except StoreDataUnavailable:
            LOG.warning("replacing an unverifiable freshness stamp")
            return False
        if held.verified_at < stamp.verified_at:
            return False
        LOG.info(
            "stamp skipped: %s verified at %.3f, this round started at %.3f",
            held.by,
            held.verified_at,
            stamp.verified_at,
        )
        return True

    # --- writes --------------------------------------------------------------------------------

    def publish_record(
        self,
        record: SignedRecord,
        manifest: Manifest,
        *,
        engaged: bool | None = None,
    ) -> bool:
        def stage(pipe: Any) -> None:
            self._stage_record(pipe, record, engaged)

        return self._guarded(record.kind, manifest, stage)

    def publish_batch(
        self,
        records: Sequence[SignedRecord],
        manifest: Manifest,
        *,
        engaged: Mapping[str, bool] | None = None,
        chunk: int = 500,
    ) -> bool:
        """Records in bounded chunks, then the manifest under WATCH.

        The record and index writes are not guarded: each is keyed and carries its own
        `feed_seq`, so a concurrent newer publish writes a HIGHER score and the reader, which
        bounds itself by the manifest, never sees the older one. Only the manifest -- the thing
        that must not move backwards -- needs the guard.
        """
        if not records:
            return True
        kind = records[0].kind
        size = max(chunk, 1)
        for start in range(0, len(records), size):
            with self._client.pipeline(transaction=False) as pipe:
                for record in records[start:start + size]:
                    flag = None if engaged is None else engaged.get(record.key)
                    self._stage_record(pipe, record, flag)
                pipe.execute()
        return self._guarded(kind, manifest, lambda pipe: None)

    def publish_kind(
        self,
        kind: StateKind,
        records: Sequence[SignedRecord],
        manifest: Manifest,
        engaged: Sequence[str] = (),
        *,
        allow_regress: bool = False,
    ) -> bool:
        """RE-HYDRATOR ONLY. O(records) by definition: the whole kind is the change.

        Guarded unless `allow_regress`, which only the `store_ahead` repair may pass. This used
        to be unconditionally unguarded, so with two re-hydrators a slower instance's stale
        snapshot could move the manifest backwards (R2-04).
        """

        def stage(pipe: Any) -> None:
            pipe.delete(self._keys.index(kind))
            pipe.delete(self._keys.engaged(kind))
            if kind in HASH_KINDS:
                pipe.delete(self._keys.record_hash(kind))
            else:
                for record in records:
                    pipe.delete(self._keys.record_key(kind, record.key))
            for record in records:
                self._stage_record(pipe, record, None)
            if engaged:
                pipe.sadd(self._keys.engaged(kind), *engaged)

        LOG.warning(
            "republishing kind=%s records=%d allow_regress=%s",
            kind.value,
            len(records),
            allow_regress,
        )
        return self._guarded(kind, manifest, stage, guard=not allow_regress)

    # --- internals -----------------------------------------------------------------------------

    def _stage_record(self, pipe: Any, record: SignedRecord, engaged: bool | None) -> None:
        payload = encode_record(record)
        if record.kind in HASH_KINDS:
            pipe.hset(self._keys.record_hash(record.kind), record.key, payload)
        else:
            pipe.set(self._keys.record_key(record.kind, record.key), payload)
        pipe.zadd(self._keys.index(record.kind), {record.key: record.feed_seq})
        if engaged is None:
            return
        name = self._keys.engaged(record.kind)
        if engaged:
            pipe.sadd(name, record.key)
        else:
            pipe.srem(name, record.key)

    def _guarded(
        self,
        kind: StateKind,
        manifest: Manifest,
        stage: Any,
        *,
        guard: bool = True,
    ) -> bool:
        """Stage, then write the manifest LAST, refusing to move it backwards."""
        name = self._keys.manifest(kind)
        with self._client.pipeline() as pipe:
            for _ in range(WATCH_ATTEMPTS):
                try:
                    pipe.watch(name)
                    if guard and self._is_ahead(kind, pipe.get(name), manifest):
                        pipe.reset()
                        return False
                    pipe.multi()
                    stage(pipe)
                    pipe.set(name, encode_manifest(manifest))
                    pipe.publish(self._keys.updates, f"{kind.value}:{manifest.feed_seq}")
                    pipe.execute()
                    return True
                except _WATCH_ERRORS:
                    continue
        raise RuntimeError(f"{kind.value}: publish kept racing other publishers")

    def _is_ahead(self, kind: StateKind, raw: object, manifest: Manifest) -> bool:
        held = self._feed_seq_of(kind, raw)
        if held is None or held <= manifest.feed_seq:
            return False
        LOG.warning(
            "publish skipped kind=%s version=%s: the store already holds %s",
            kind.value,
            manifest.version,
            held,
        )
        return True

    def _feed_seq_of(self, kind: StateKind, raw: object) -> int | None:
        if not isinstance(raw, (bytes, str)):
            return None
        try:
            return decode_manifest(self._secret, kind, raw, not_before=ZERO).feed_seq
        except StoreDataUnavailable:
            return None  # a missing or forged manifest never blocks a publish


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _watch_errors() -> tuple[type[BaseException], ...]:
    try:
        from redis.exceptions import WatchError
    except ImportError:  # pragma: no cover - redis is a declared dependency
        return ()
    return (WatchError,)


_WATCH_ERRORS = _watch_errors()
