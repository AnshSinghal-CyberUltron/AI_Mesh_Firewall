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

"Rejects" means ANY exception compiling one verified record, not only `CompileError`. The
compiler's own refusals are `CompileError`, but a tenant's data reached `re` and `json` through
it and they raise `RecursionError` and `OverflowError`; before, those escaped this loop, failed
the round, and -- because the cursor did not move -- failed every round after it, for every
tenant on every gateway reading the record.

Two different failures, two different answers, both per tenant:

* a VERIFIED record that does not compile is GW05's rejection: the previous valid plan keeps
  serving (or PLAN_UNAVAILABLE when there is none) and the tenant is told (`PlanStore.reject`,
  the applied-version report, `amf_state_records_rejected_total`).
* a record that FAILED VERIFICATION arrives as a `RecordFault` from an isolating poll. It is not
  what the control plane wrote, so the tenant fails CLOSED until a verified record replaces it
  (`PlanStore.quarantine`, `amf_state_records_quarantined_total`). Last-good would let a store
  writer pin a tenant to a superseded policy.

Logs name the org, the feed position and the exception TYPE. Never the exception's text: a
`CompileError` quotes rule ids and a decoder can quote the body, and both are the tenant's data.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from gateway_v2.domain.plan import ExecutionPlan, is_newer
from gateway_v2.domain.state import SignedRecord, StateKind
from gateway_v2.plan.compiler import CompileError, compile_plan
from gateway_v2.plan.document import decode_plan_body
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore
from gateway_v2.runtime.state_feed import FeedRound, RecordFault

LOG = logging.getLogger("amf.gateway.state")


@dataclass(frozen=True, slots=True)
class ApplyOutcome:
    """What one delta did. Every tuple is O(changes), so it is safe to log and to meter."""

    applied: tuple[str, ...]
    offboarded: tuple[str, ...]
    skipped: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...]
    """`(org, exception type)`. The type only: the message can quote the tenant's body."""

    quarantined: tuple[str, ...] = ()

    @property
    def touched(self) -> int:
        return (
            len(self.applied) + len(self.offboarded) + len(self.skipped) + len(self.rejected)
            + len(self.quarantined)
        )

    @property
    def rejected_count(self) -> int:
        """Read by the synchroniser for `amf_state_records_rejected_total` (`RejectionCount`)."""
        return len(self.rejected)


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
        controls=document.controls,
        rag_settings=document.rag_settings,
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

    def apply(
        self,
        records: Sequence[SignedRecord],
        faults: Sequence[RecordFault] = (),
    ) -> ApplyOutcome:
        applied: list[str] = []
        offboarded: list[str] = []
        skipped: list[str] = []
        rejected: list[tuple[str, str]] = []
        for record in records:
            org_id = record.key
            if record.deleted:
                self._store.offboard(org_id)
                if self._snapshot is not None:
                    self._snapshot.forget(org_id)
                offboarded.append(org_id)
                continue
            try:
                plan = plan_from_record(record, compiled_at=self._clock())
            except Exception as exc:  # noqa: BLE001 - one tenant's body never stops the round
                # The tenant stays KNOWN, so it reads PLAN_UNAVAILABLE and keeps any plan it has.
                error = type(exc).__name__
                self._store.reject(org_id, feed_seq=record.feed_seq, error=error)
                LOG.warning(
                    "plan record rejected: org=%r feed_seq=%d error=%s; the previous valid plan, "
                    "if any, keeps serving",
                    org_id, record.feed_seq, error,
                )
                rejected.append((org_id, error))
                continue
            if self._already_served(plan):
                self._store.clear_held(org_id)
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
            quarantined=self._quarantine(faults),
        )

    def _quarantine(self, faults: Sequence[RecordFault]) -> tuple[str, ...]:
        """Fail each faulted tenant closed and drop its last-known-good. O(faults)."""
        for fault in faults:
            self._store.quarantine(fault.key, feed_seq=fault.feed_seq, error=fault.error)
            if self._snapshot is not None:
                self._snapshot.forget(fault.key)
            LOG.warning(
                "plan record quarantined: org=%r feed_seq=%d error=%s; the tenant is refused "
                "until a verified record replaces it",
                fault.key, fault.feed_seq, fault.error,
            )
        return tuple(fault.key for fault in faults)

    def _already_served(self, plan: ExecutionPlan) -> bool:
        # The held plan, not `read`: a quarantined tenant reads PLAN_UNAVAILABLE but still holds
        # the version a new record must beat, and `put` would refuse a regress against it.
        current = self._store.current(plan.org_id)
        if current is None:
            return False
        return not is_newer(plan, current)


def plan_applier(
    store: PlanStore,
    snapshot: ReplicaSnapshot | None = None,
    *,
    clock: Callable[[], float] = time.time,
) -> Callable[[FeedRound], ApplyOutcome]:
    """Bind a store and snapshot to the round shape the synchroniser drives."""
    applier = PlanDeltaApplier(store, snapshot, clock=clock)

    def apply(round_: FeedRound) -> ApplyOutcome:
        return applier.apply(round_.records, round_.faults)

    return apply
