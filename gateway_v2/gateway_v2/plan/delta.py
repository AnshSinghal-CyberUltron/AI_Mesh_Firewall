"""Apply changed plan records. O(changes), never O(tenants).

This replaces RC2's `reconcile_once`, which read the whole plan index, re-digested every entry
and then looped over every org once a second on the serving loop — 72 ms per second at 25,000
tenants (R2-02).

Three behaviours are load-bearing and each has a test:

* **Idempotent.** A record already applied is skipped, not an error. Duplicate and replayed
  deltas are normal: a nudge and a periodic round can both deliver the same change.
* **Monotone.** An older record for a tenant already on a newer plan is skipped. `PlanStore.put`
  would raise on a regress; the applier must not turn a benign duplicate into a failed round.
* **Per-tenant isolation of failure.** A body the compiler rejects leaves that tenant on its
  previous valid plan and the round continues for everyone else. A tenant with no plan yet is
  registered as KNOWN so it reads PLAN_UNAVAILABLE, never PLAN_UNKNOWN_TENANT — the v2.1 defect
  where a flush plus re-seed wedged tenants at 403 forever.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from gateway_v2.domain.plan import ExecutionPlan, is_newer
from gateway_v2.domain.state import SignedRecord, StateKind
from gateway_v2.plan.compiler import CompileError, compile_plan
from gateway_v2.plan.document import decode_plan_body
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore


@dataclass(frozen=True, slots=True)
class ApplyOutcome:
    """What one delta did. Every tuple is O(changes), so it is safe to log and to meter."""

    applied: tuple[str, ...]
    offboarded: tuple[str, ...]
    skipped: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...]

    @property
    def touched(self) -> int:
        return len(self.applied) + len(self.offboarded) + len(self.skipped) + len(self.rejected)


def plan_from_record(record: SignedRecord, *, compiled_at: float) -> ExecutionPlan:
    """Compile a signed body and stamp it with the version Postgres issued.

    The compiler derives its own (epoch, sequence) from a previous plan; that counter belongs to
    the control plane, so the record's version replaces it. Two workers compiling the same body
    therefore produce byte-identical plans, which is what lets LGW05-4 compare served versions.
    """
    if record.kind is not StateKind.PLAN:
        raise CompileError(f"{record.kind.value} record cannot become a plan")
    document = decode_plan_body(record.body)
    if document.org_id != record.key:
        raise CompileError(
            f"plan body claims {document.org_id!r} but the record is keyed {record.key!r}",
        )
    compiled = compile_plan(
        document.org_id,
        document.drafts,
        streaming_mode=document.streaming_mode,
        compiled_at=compiled_at,
    )
    return replace(
        compiled,
        epoch=record.version.epoch,
        sequence=record.version.seq,
        feed_seq=record.feed_seq,
    )


class PlanDeltaApplier:
    """Turns signed plan records into served plans. Holds no per-tenant state of its own."""

    def __init__(
        self,
        store: PlanStore,
        snapshot: ReplicaSnapshot | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._snapshot = snapshot
        self._clock = clock

    def apply(self, records: Sequence[SignedRecord]) -> ApplyOutcome:
        applied: list[str] = []
        offboarded: list[str] = []
        skipped: list[str] = []
        rejected: list[tuple[str, str]] = []
        for record in records:
            org_id = record.key
            if record.deleted:
                self._store.offboard(org_id)
                offboarded.append(org_id)
                continue
            try:
                plan = plan_from_record(record, compiled_at=self._clock())
            except CompileError as exc:
                # The tenant stays KNOWN, so it reads PLAN_UNAVAILABLE and keeps any plan it has.
                self._store.register_tenant(org_id)
                rejected.append((org_id, str(exc)))
                continue
            if self._already_served(plan):
                skipped.append(org_id)
                continue
            self._store.put(plan)
            if self._snapshot is not None:
                # Last-known-good for exactly this tenant. RC2 pre-warmed every tenant instead.
                self._snapshot.absorb(org_id)
            applied.append(org_id)
        return ApplyOutcome(
            applied=tuple(applied),
            offboarded=tuple(offboarded),
            skipped=tuple(skipped),
            rejected=tuple(rejected),
        )

    def _already_served(self, plan: ExecutionPlan) -> bool:
        current = self._store.read(plan.org_id)
        if not isinstance(current, ExecutionPlan):
            return False
        return not is_newer(plan, current)
