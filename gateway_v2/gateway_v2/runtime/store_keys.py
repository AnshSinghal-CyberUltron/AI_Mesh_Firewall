"""Published-state key layout. Shared by the gateway readers and the control-plane writer.

Every key carries the namespace as a hash tag (`{rv2}` by default) so a multi-key read, a MULTI
or a Lua script stays in ONE slot on a cluster-mode store.

`idx` and `on` are the GW05c additions:

* `idx:<kind>` — a sorted set, member = record key, score = that record's `feed_seq`. It is the
  change feed, and deliberately an INDEX rather than a log: one entry per record, always at its
  current version. There is no retention to tune and no trim gap to detect, so a reader that was
  away for an hour still selects exactly the records that changed.
* `on:ks` — the set of currently engaged kill-switch scopes. Kill-switch records are O(tenants),
  because C36 requires an explicit OFF record for every scope, but the ENGAGED subset is normally
  empty or tiny. Publishing it separately is what makes a cold start O(engaged) instead of
  O(tenants), and `on_count` in the signed manifest is what keeps that fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass

from gateway_v2.domain.state import StateKind

DEFAULT_NAMESPACE = "{rv2}"

HASH_KINDS = frozenset({StateKind.KEY, StateKind.KS, StateKind.BUDGET})
"""Kinds whose records live as fields of one hash. Plans get their own key: the bodies are large."""

ENGAGED_KINDS = frozenset({StateKind.KS})
"""Kinds that publish a separate engaged set."""


@dataclass(frozen=True, slots=True)
class StoreKeys:
    """Key names for one namespace. A tenant-independent, allocation-free layout."""

    namespace: str = DEFAULT_NAMESPACE

    def manifest(self, kind: StateKind) -> str:
        return f"{self.namespace}:meta:{kind.value}"

    def index(self, kind: StateKind) -> str:
        return f"{self.namespace}:idx:{kind.value}"

    def engaged(self, kind: StateKind) -> str:
        return f"{self.namespace}:on:{kind.value}"

    def record_hash(self, kind: StateKind) -> str:
        """The hash holding every record of a hash-stored kind."""
        if kind not in HASH_KINDS:
            raise ValueError(f"{kind.value} records are not hash-stored")
        return f"{self.namespace}:{kind.value}"

    def record_key(self, kind: StateKind, key: str) -> str:
        """The standalone key of one record of a key-stored kind."""
        if kind in HASH_KINDS:
            raise ValueError(f"{kind.value} records are hash-stored")
        return f"{self.namespace}:{kind.value}:{key}"

    @property
    def updates(self) -> str:
        """Pub/sub nudge channel. Carries `<kind>:<feed_seq>`; it is a latency hint, not a feed."""
        return f"{self.namespace}:updates"

    @property
    def stamp(self) -> str:
        """GW05b's signed freshness stamp. Raises a reader's cursor floor above zero."""
        return f"{self.namespace}:stamp"


KEYS = StoreKeys()
"""The default-namespace layout."""
