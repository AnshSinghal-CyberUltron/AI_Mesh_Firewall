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
"""

from __future__ import annotations

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
