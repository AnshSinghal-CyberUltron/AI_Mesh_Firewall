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

`audit:<org>` is GW14c's addition, and it is the one key family here that does NOT hold security
state. It is in this layout anyway because R2-05 is precisely the discovery that audit and state
share a store and that nothing was accounting for it: putting the audit streams somewhere this
module cannot see them is how the budget stops being enforceable.
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

    def audit_stream(self, org: str) -> str:
        """One audit stream per tenant (GW14c). Keyed per org because the budget is per org.

        It carries the namespace hash tag like everything else, so one pipelined batch of
        `XADD`s and `XTRIM`s across several tenants stays a single round trip on a cluster-mode
        store. The trade is that audit then lands in the namespace's slot rather than spreading
        across shards; on the signed topology (Memorystore for Valkey 8 HA, not a sharded
        cluster) that is a non-question, and the budget is read from that instance's `maxmemory`
        either way. A sharded deployment would have to read `maxmemory` per node, which is a
        statement about that topology rather than about this key.
        """
        if not org:
            raise ValueError("an audit stream needs an org: an unattributed record cannot be "
                             "budgeted, trimmed per tenant, or exported to the right place")
        return f"{self.namespace}:audit:{org}"

    def budget_remaining(self, org: str) -> str:
        """The Org's remaining shared token budget pool (the LIVE counter, not the config).

        This is the budget-lease family (R2-09 / GW06): a worker acquires a chunk of this pool and
        then spends it locally, so the org-level budget is enforced across every worker and replica
        without a store round trip per request. It is DISTINCT from the published
        `StateKind.BUDGET` config record (hash-stored, control-plane-written): that record is the
        configured budget, this key is the live remaining count the lease decrements.

        Carries the namespace hash tag so this key, `budget_generation` and every
        `budget_lease:<worker>` land in ONE slot on a cluster-mode store, which is what lets the
        atomic acquire/return `EVAL` touch all of them in a single script.
        """
        if not org:
            raise ValueError("a budget pool needs an org: an unattributed budget cannot be "
                             "leased, counted, or enforced per tenant")
        return f"{self.namespace}:budget:{org}:remaining"

    def budget_generation(self, org: str) -> str:
        """The Org's current `Budget_Generation` (the LIVE counter, not the config).

        A monotonic tag advanced when the Org budget config changes; a lease carrying an older
        generation is treated as invalid and never spent (C29). Shares the namespace hash tag
        with `budget_remaining` and `budget_lease` so the generation check is in the same slot as
        the pool decrement in the atomic acquire.
        """
        if not org:
            raise ValueError("a budget generation needs an org: an unattributed budget cannot be "
                             "leased, counted, or enforced per tenant")
        return f"{self.namespace}:budget:{org}:generation"

    def budget_lease(self, org: str, worker_id: str) -> str:
        """One worker's outstanding (unreturned) lease chunk — the TTL-reclaim record.

        A per-worker key, carrying the granted-but-not-yet-returned chunk with a Lease_TTL on the
        store's clock, so a crashed worker's chunk is reclaimed by TTL expiry rather than lost
        (R2-19). Part of the live budget-lease counter family, distinct from the published
        `StateKind.BUDGET` config record. Shares the namespace hash tag with the pool and the
        generation so the acquire/return `EVAL` stays single-slot.
        """
        if not org:
            raise ValueError("a budget lease needs an org: an unattributed budget cannot be "
                             "leased, counted, or enforced per tenant")
        if not worker_id:
            raise ValueError("a budget lease needs a worker id: an unattributed lease cannot be "
                             "reclaimed on crash or returned on shutdown")
        return f"{self.namespace}:budget:{org}:lease:{worker_id}"

    @property
    def audit_prefix(self) -> str:
        """The prefix an operator or an exporter scans for audit streams."""
        return f"{self.namespace}:audit:"

    def audit_org(self, key: str) -> str | None:
        """The org an audit stream key belongs to, or None if it is not one of ours."""
        prefix = self.audit_prefix
        return key[len(prefix):] or None if key.startswith(prefix) else None

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
