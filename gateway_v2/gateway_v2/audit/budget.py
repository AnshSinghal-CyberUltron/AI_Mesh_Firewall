"""Turn one memory budget into a per-org stream length. The arithmetic R2-05 was missing.

RC2 bounded audit with `MAXLEN 2,000,000` per org. That is a record count, and a record count is
not a memory bound: at the ~2.7 KB per record round 2 measured it permits about 5.4 GiB *per
tenant*, so on a 10.4 GiB instance the cap could never engage before the store filled. The cap
was not set wrong — it was the wrong kind of quantity.

So the budget is in BYTES, and it is divided like this:

    MAXLEN(org) = budget / (tenants x store_bytes_per_record(org))

clamped to `[min_retain, stream_maxlen]`.

Three properties that are each a decision rather than a consequence:

* **`tenants` is every org in the plan snapshot, not every org that is writing.** Idle tenants
  reserve a share they are not using. That is deliberately conservative: the alternative is a
  cap that expands while tenants are quiet and then has to contract — by trimming — exactly when
  traffic arrives and the store is under the most pressure.

* **`store_bytes_per_record` is tracked PER ORG**, as an EWMA of that org's own payloads. Divide
  a byte budget by a fleet-average record size and every org gets an equal RECORD share, so a
  tenant whose records are ten times larger takes ten times the memory. Per-org sizing gives
  each tenant an equal BYTE share of the store whatever its records look like, which is the
  property that actually bounds the total.

* **The model over-estimates on purpose** (payload x 1.10 + 256 B). A stream entry costs more
  than its payload — the id, the field name, the radix-tree node — and the direction of the
  error decides what a mistake does: over-estimate and the budget holds with a little headroom;
  under-estimate and the store fills while every gauge says the bound is being honoured. The
  tests check the model against real `MEMORY USAGE` and real `used_memory` deltas at payload
  sizes from 315 B to 6 KB rather than taking the constants on trust.

Nothing here reads the store. Sizing by measuring each entry would put a `MEMORY USAGE` per
record on the write path, which is the class of mistake C31 is about.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from gateway_v2.domain.audit_knobs import AuditMemoryKnobs
from gateway_v2.runtime.storemem import BudgetDecision

ENTRY_FACTOR = 1.35
"""Payload multiplier covering per-entry overhead that scales with the payload.

**Measured, and NOT the reference's 1.10.** `rc3-audit-mem-v1` sampled payloads of 315 B to 6 KB
and reported its model above the real cost every time. Re-measuring against Valkey 8 across a
wider range shows why that held and why it does not generalise -- the real cost per entry, as a
ratio of the payload, oscillates with the allocator's size classes rather than rising smoothly:

    payload   used_memory delta/entry   ratio    factor needed over a 256 B flat term
      315 B               379 B         1.21                 0.39
      512 B               590 B         1.15                 0.65
    1,024 B             1,204 B         1.18                 0.93
    2,048 B             2,587 B         1.26                 1.14
    2,900 B             3,092 B         1.07                 0.98
    4,096 B             5,147 B         1.26                 1.19
    6,000 B             6,171 B         1.03                 0.99
    8,192 B            10,267 B         1.25                 1.22

A payload landing just above a size class costs the next class up, so 2 KB, 4 KB and 8 KB each
need a factor above 1.10 while 2.9 KB and 6 KB do not. The reference's two sample points happen
to be two of the cheap ones. 1.35 covers the worst measured case with about 10% headroom.

The cost of being conservative is paid in the safe direction: a small-record tenant is charged
roughly 1.8x what its entries really cost, so it gets a shorter stream than it strictly could.
The budget is a ceiling, not a target, and the alternative error -- a MAXLEN too long because the
model under-charged -- fills the store while every gauge reports the bound being honoured.

Modelling the size classes directly would be more accurate and would couple this to the
allocator's behaviour, which varies by build. A flat conservative factor checked against real
measurements is the more durable of the two. The check is
`tests/runtime/test_lgw14c_live_docker.py::test_the_byte_model_is_above_what_valkey_charges`.
"""

ENTRY_BYTES = 256
"""Flat per-entry overhead: the id, the field name, and the node's share."""

SIZE_ALPHA = 0.02
"""EWMA weight of one record's payload size.

Slow on purpose. The quantity being estimated is "what this tenant's records cost", which is a
property of its traffic shape, not of its last request. A fast alpha would let one unusually
large record shrink an org's MAXLEN by an order of magnitude and trim history that did not need
to go.
"""

MAX_TRACKED_ORGS = 50_000
"""Ceiling on the per-org size table, above the 25,000-tenant scale target (R2-02).

The table is bounded by the estate in practice, because an org id comes from an authenticated
key. The ceiling is here so that "in practice" is not the only thing standing between a
long-running process and an unbounded dict; beyond it, new orgs are sized by the LARGEST
estimate seen, which yields a smaller MAXLEN and so fails towards using less of the store.
"""


class StreamBudget:
    """Holds the budget and each org's size estimate, and answers "how long may this stream be".

    One instance per writer. Every worker derives the same MAXLEN for an org from the same
    inputs; where they briefly differ (a worker that has seen fewer of an org's records) the
    store simply trims to the smallest of them, which is the safe direction.
    """

    def __init__(
        self,
        knobs: AuditMemoryKnobs,
        tenants: Callable[[], int] = lambda: 1,
        *,
        max_tracked_orgs: int = MAX_TRACKED_ORGS,
    ) -> None:
        self._knobs = knobs
        self._tenants = tenants
        self._max_tracked = max(max_tracked_orgs, 1)
        self._budget_bytes: int | None = knobs.explicit_budget_bytes
        self._budget_source = (
            "AMF_AUDIT_STORE_BUDGET_MB" if self._budget_bytes is not None else "not resolved yet"
        )
        self._payload_ewma: dict[str, float] = {}
        self._largest = 0.0

    # --- the budget ----------------------------------------------------------------------------

    @property
    def budget_bytes(self) -> int | None:
        """Bytes all audit streams may use, or None when it could not be resolved."""
        return self._budget_bytes

    @property
    def budget_source(self) -> str:
        return self._budget_source

    def adopt(self, decision: BudgetDecision) -> None:
        """Take a freshly resolved budget. Called at start-up and on every memory sample.

        Re-adopting is what makes a store resize followable without a restart, and it is why the
        budget is not a constructor-only value.
        """
        self._budget_bytes = decision.budget_bytes
        self._budget_source = decision.source

    # --- the size model ------------------------------------------------------------------------

    def observe(self, org: str, payload_bytes: int) -> None:
        """Fold one record's serialized size into that org's estimate. O(1), no allocation."""
        if payload_bytes < 0:
            raise ValueError("a payload cannot have a negative size")
        current = self._payload_ewma.get(org)
        if current is None:
            if len(self._payload_ewma) >= self._max_tracked:
                # Beyond the ceiling, do not grow the table. The org is then sized by the
                # largest estimate, which under-states its MAXLEN rather than over-stating it.
                self._largest = max(self._largest, float(payload_bytes))
                return
            updated = float(payload_bytes)
        else:
            updated = current + (payload_bytes - current) * SIZE_ALPHA
        self._payload_ewma[org] = updated
        self._largest = max(self._largest, updated)

    def bytes_per_record(self, org: str) -> float:
        """What one of this org's records is assumed to cost in the store."""
        payload = self._payload_ewma.get(org)
        if payload is None:
            payload = self._largest
        return payload * ENTRY_FACTOR + ENTRY_BYTES

    @property
    def tracked_orgs(self) -> int:
        return len(self._payload_ewma)

    # --- the answer ----------------------------------------------------------------------------

    def maxlen(self, org: str) -> int:
        """How long this org's stream may be. Always a cap, never "unbounded".

        With no resolved budget this is the per-org upper bound — RC2's behaviour, and a bound
        in name only, which is why `audit_budget` returns a warning alongside it rather than
        letting it pass as a configuration.
        """
        if self._budget_bytes is None:
            return self._knobs.stream_maxlen
        per_org = self._budget_bytes / (max(1, self._tenants()) * self.bytes_per_record(org))
        return max(self._knobs.min_retain, min(self._knobs.stream_maxlen, int(per_org)))

    def maxlens(self, orgs: Iterable[str]) -> dict[str, int]:
        """The caps for the orgs one batch touched. What the store adapter is handed."""
        return {org: self.maxlen(org) for org in orgs}

    def reading(self, orgs: Iterable[str] = ()) -> BudgetReading:
        """A snapshot for the metrics surface: scalars only, never one series per tenant."""
        caps = [self.maxlen(org) for org in orgs]
        costs = [self.bytes_per_record(org) for org in orgs]
        return BudgetReading(
            budget_bytes=self._budget_bytes or 0,
            tenants=max(1, self._tenants()),
            tracked_orgs=len(self._payload_ewma),
            min_stream_maxlen=min(caps) if caps else 0,
            max_bytes_per_record=max(costs) if costs else 0.0,
        )


@dataclass(frozen=True, slots=True)
class BudgetReading:
    """The scalars a scrape needs. Deliberately not a per-org map.

    R2-10 measured what a tenant-derived label set costs: a full metric directory made 40% of
    new tenants' first requests return HTTP 500. So the batch's MOST CONSTRAINED stream and its
    LARGEST per-record estimate are published, which is what an operator actually asks of this
    mechanism, and the per-org detail stays answerable from the store itself.
    """

    budget_bytes: int = 0
    tenants: int = 1
    tracked_orgs: int = 0
    min_stream_maxlen: int = 0
    max_bytes_per_record: float = 0.0

    def as_mapping(self) -> Mapping[str, float]:
        return {
            "budget_bytes": float(self.budget_bytes),
            "tenants": float(self.tenants),
            "tracked_orgs": float(self.tracked_orgs),
            "min_stream_maxlen": float(self.min_stream_maxlen),
            "max_bytes_per_record": self.max_bytes_per_record,
        }
