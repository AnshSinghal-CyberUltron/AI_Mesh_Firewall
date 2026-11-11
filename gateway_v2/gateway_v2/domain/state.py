"""Versioned, signed shared-state vocabulary. Pure types, no store, no I/O.

C36 (GW05) keeps Postgres as the source of truth and publishes a signed copy to the store.
Every record carries (epoch, seq), the hash of its canonical body, an explicit `deleted` flag
and an HMAC signature. None of that changes here.

GW05c changes one thing: how a reader proves the published copy is COMPLETE.

RC2 proved completeness with a per-kind digest over every record, so no reader could validate
the store without reading and hashing the whole kind — the construct that made the kill-switch
refresh O(records) and the plan reconcile O(tenants) on the serving loop (R2-02).

Instead, a record carries its own `feed_seq` and a kind's manifest carries `feed_seq`, `count`
and `on_count`:

* (epoch, seq) orders a record against ITSELF. It is not a cursor: a signed rollback writes
  (epoch + 1, 0), so seq moves backwards and a cursor built on it would silently skip changes.
* `feed_seq` is the cursor space. It is per kind, advances on every write and NEVER resets,
  epoch bumps included, so "everything that changed since position P" is answerable exactly.
* `count` (records in the kind) and `on_count` (kill-switch scopes currently engaged) are what
  a reader checks against the store's own index in O(1) to prove nothing is missing. A scope
  that is engaged but absent from the published engaged set must read as UNAVAILABLE, never as
  "switch off" — `on_count` is what makes that fail CLOSED.

A record's body travels as its CANONICAL BYTES, not as a re-serialized object. RC2 recomputed
the body hash from the parsed body, so every signature depended on the serializer producing
byte-identical output forever. Carrying the bytes removes that coupling: verification compares
what was signed.

GW05b adds `Cursor` and `Stamp`. Both are pure data and both belong here rather than in
`runtime`, because a `Stamp` carries one `Cursor` per kind and the lowest layer cannot import
upward. `Cursor` moved down from `runtime.state_feed` for exactly that reason; `runtime` still
re-exports nothing it does not own.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class StateKind(StrEnum):
    """The kinds of durable state the control plane publishes. One spelling per concept."""

    PLAN = "plan"
    KEY = "key"
    KS = "ks"
    BUDGET = "budget"


class StateOp(StrEnum):
    """Why a record was written. Carried inside the signature, so it cannot be restated."""

    PUT = "put"
    REVOKE = "revoke"
    OFFBOARD = "offboard"
    ROLLBACK = "rollback"


class StoreDataUnavailable(Exception):
    """The published copy is missing, unverifiable, incomplete or older than already applied.

    Handled exactly like a store outage: the RAM snapshot serves to its freshness bound and then
    the request fails closed. Never "no such tenant", never "invalid key", never "switch off".
    """


@dataclass(frozen=True, slots=True, order=True)
class Version:
    """A record's own version. Ordering is (epoch, seq); a rollback raises epoch and resets seq."""

    epoch: int
    seq: int

    def __str__(self) -> str:
        return f"{self.epoch}.{self.seq}"


ZERO = Version(0, 0)
"""The floor a process uses before it has applied anything. GW05b raises it from a stamp."""


@dataclass(frozen=True, slots=True)
class SignedRecord:
    """One published record. `body` is the canonical bytes that `content_hash` covers."""

    kind: StateKind
    key: str
    version: Version
    feed_seq: int
    content_hash: str
    deleted: bool
    op: StateOp
    signature: str
    body: bytes


@dataclass(frozen=True, slots=True)
class Manifest:
    """A kind's published head. Proves completeness in O(1) instead of by a whole-set digest."""

    kind: StateKind
    version: Version
    feed_seq: int
    count: int
    on_count: int
    signature: str


@dataclass(frozen=True, slots=True)
class Cursor:
    """How far a reader has applied a kind. Floors only rise; GW05b raises them from a stamp.

    Both numbers are load-bearing and `decode_manifest` checks both. The version alone is not
    enough: a partial restore can leave the store at an older POSITION under an equal version,
    and a reader floored only by version would accept it.
    """

    version: Version
    feed_seq: int

    def advanced(self, version: Version, feed_seq: int) -> Cursor:
        return Cursor(version=version, feed_seq=feed_seq)


START = Cursor(version=ZERO, feed_seq=0)
"""A reader with no stamp and nothing applied. R2-03 is that processes START here and stay here."""


@dataclass(frozen=True, slots=True)
class Stamp:
    """One re-hydrator round's signed assertion about the store it just compared with Postgres.

    GW05b exists because a gateway's own notion of "fresh" is "I read the store recently", not
    "the store has been compared with durable truth recently". The first says nothing after a
    failover to a lagging replica (SP1) or when a committed write was never published (SP2):
    the older manifest is genuinely signed, so it verifies, and nothing ages.

    A stamp makes the second thing readable by a gateway for the price of one `GET`.

    `verified_at` is the round's START, not its end. The re-hydrator reads the store BEFORE
    Postgres, so the Postgres position it compares covers every commit that happened before the
    round began. Dating the stamp at the end would over-claim by the round's duration.

    `cursors` covers EVERY `StateKind`. A stamp missing a kind would leave that kind's reader at
    `ZERO` while the process believed itself verified -- SP1 for one kind, silently. `decode_stamp`
    refuses a partial stamp for the same reason the manifest's `on_count` refuses a partial
    engaged set.

    Compares by value; not hashable, because `cursors` is a mapping. Stamps are read, replaced
    and compared, never used as keys, so this is a property rather than a limitation.
    """

    verified_at: float
    """Wall clock seconds at the round's start. Compared against the GATEWAY's wall clock."""

    cursors: Mapping[StateKind, Cursor]
    """Per kind: the (version, feed_seq) Postgres held and the store was found to match."""

    by: str
    """Which re-hydrator wrote it (REHYDRATOR_ID, default hostname:pid). Gateways log it."""

    deep: bool
    """The round also compared the whole index against Postgres, not just the O(1) heads."""

    degraded: bool
    """A Postgres ride-through stamp: the timestamp advanced, the cursors did not (PG_GRACE_MS)."""

    signature: str
