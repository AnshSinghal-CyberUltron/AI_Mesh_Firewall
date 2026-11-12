"""The served per-org BUDGET value type (GW36 S4, Req 9.2).

``BudgetRecord`` is the pure, decoded shape of a signed ``StateKind.BUDGET`` record as the gateway
serves it: one org's configured token pool plus the record's generation. The generation **is** the
record's ``feed_seq`` — it is never a separate body field that could disagree with the cursor, so a
lease carrying an older generation is spotted by a plain integer compare (see
``runtime/store_keys.py`` for the live-counter family the generation guards).

This is a value type only. It holds no store client, no signing authority, and imports nothing from
the Django control plane — ``admit`` sits below ``edge``/``resolve`` and above
``runtime``/``domain`` under the import-linter ``layers`` contract, and a decoded record is a plain
immutable value.

Alongside the value type this module carries the served ``BudgetSnapshot`` and the
``budget_applier`` that maintains it (GW36 S4 task 8.1). The applier is the synchroniser
``Applier`` for ``StateKind.BUDGET``: the feed reader has already verified each record's signature,
body hash, ``feed_seq`` against the index score, and the kind's anti-regress floor by the time a
drained round reaches the applier, so the applier's job is the one check the feed reader cannot
make -- it decodes
the BUDGET body, confirms the body's ``org_id`` is the record it was keyed under (completeness), and
turns the verified record into a served ``BudgetRecord``. Any body defect, kind mismatch, or the
``StoreDataUnavailable`` the feed reader itself raises on drift leaves the cursor and the served set
untouched, because ``StateSynchroniser.round_once`` treats an applier ``StoreDataUnavailable`` as
"refuse this delta, the cursor stays put".

Freshness follows the KEY/PLAN/KS snapshots: ``BudgetSnapshot.fresh()`` consults a per-kind
``KindGate`` (a ``FreshnessGate``) for BUDGET so ``/readyz`` can gate on it (task 10.4). Like the
plan and kill-switch snapshots the gate is injected and optional; a snapshot with no gate reports
fresh, which is the wiring-not-yet-present case the dev profile exercises.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass

from gateway_v2.domain.state import SignedRecord, StateKind, StoreDataUnavailable
from gateway_v2.runtime.state_feed import FeedRound
from gateway_v2.runtime.state_stamp import FreshnessGate

__all__ = ("BudgetRecord", "BudgetSnapshot", "budget_applier", "record_of")


@dataclass(frozen=True, slots=True)
class BudgetRecord:
    """One org's served BUDGET state: a configured token pool and its generation.

    ``pool_tokens`` is the org's configured pool token count, a non-negative integer decoded from
    the signed BUDGET record body. ``generation`` equals the record's published ``feed_seq`` (the
    Budget_Generation); the gateway reads it straight off the decoded ``SignedRecord`` so it can
    never drift from the cursor the record was verified at.

    Frozen and slotted: a served record is an immutable value with no identity beyond its fields.
    Construction rejects a negative ``pool_tokens`` or a negative ``generation`` so an invalid
    record can never enter the served set (Req 9.2).
    """

    org: str
    pool_tokens: int
    generation: int

    def __post_init__(self) -> None:
        if self.pool_tokens < 0:
            raise ValueError(f"pool_tokens must be non-negative, got {self.pool_tokens}")
        if self.generation < 0:
            raise ValueError(f"generation must be non-negative, got {self.generation}")


def record_of(record: SignedRecord) -> BudgetRecord:
    """Decode one verified BUDGET record into a served ``BudgetRecord``.

    The feed reader has already proven this record's signature, that its body hashes to the signed
    ``content_hash``, that its ``feed_seq`` matches the index score it was fetched under, and that
    the manifest it came from is not older than the kind's cursor or floor. What it cannot know is
    the BUDGET body's shape, so that is the whole of what this does: parse the two-field body, read
    the pool, and confirm the body names the org the record was keyed under.

    The generation is the record's ``feed_seq`` read straight off the ``SignedRecord`` -- never a
    body field, so it cannot disagree with the cursor the record was verified at. ``org`` is the
    record key, not the body ``org_id``; the two are checked equal so a record keyed for one org
    carrying another org's body is refused rather than served under the wrong lease.

    Every defect raises ``StoreDataUnavailable``, which the synchroniser treats as "refuse this
    delta, cursor stays put" -- the published copy is treated as a store outage, never as a benign
    empty value (Req 7.4).
    """
    if record.kind is not StateKind.BUDGET:
        raise StoreDataUnavailable(f"{record.kind.value} record is not a budget")
    if record.deleted:
        # An offboarded org's record stays as an explicit record carrying a pool, never a
        # tombstone the gateway must interpret; a deleted BUDGET record is a shape this module
        # does not define.
        raise StoreDataUnavailable(f"budget record {record.key!r} is deleted")
    try:
        body: object = json.loads(record.body)
    except RecursionError as exc:
        raise StoreDataUnavailable(
            f"budget record {record.key!r} body is nested too deeply",
        ) from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise StoreDataUnavailable(
            f"budget record {record.key!r} body is not valid JSON: {exc}",
        ) from exc
    if not isinstance(body, dict):
        raise StoreDataUnavailable(f"budget record {record.key!r} body is not an object")
    org_id = body.get("org_id")
    if not isinstance(org_id, str) or not org_id.strip():
        raise StoreDataUnavailable(
            f"budget record {record.key!r} field 'org_id' must be a non-empty string",
        )
    if org_id != record.key:
        raise StoreDataUnavailable(
            f"budget record keyed {record.key!r} carries org_id {org_id!r}",
        )
    pool = body.get("pool_tokens")
    if isinstance(pool, bool) or not isinstance(pool, int):
        raise StoreDataUnavailable(
            f"budget record {record.key!r} field 'pool_tokens' must be an integer",
        )
    if pool < 0:
        raise StoreDataUnavailable(
            f"budget record {record.key!r} field 'pool_tokens' must not be negative",
        )
    return BudgetRecord(org=org_id, pool_tokens=pool, generation=record.feed_seq)


class BudgetSnapshot:
    """Per-worker served BUDGET state: one ``BudgetRecord`` per org, read from RAM only.

    The ``budget_applier`` writes this from drained deltas; the ``QuotaRegistry`` reads it once per
    org to build that org's lease and never re-reads it on the request path. Holds instance state
    only -- a plain dict under a lock, like the kill-switch engaged set -- so there is no
    module-level mutable and nothing to invalidate across workers.
    """

    def __init__(self, *, gate: FreshnessGate | None = None) -> None:
        self._gate = gate
        self._lock = threading.Lock()
        self._records: dict[str, BudgetRecord] = {}

    # --- request path: RAM only ----------------------------------------------------------------

    def record_for(self, org: str) -> BudgetRecord | None:
        """The org's served record, or None when none is served. One dict read under the lock."""
        with self._lock:
            return self._records.get(org)

    def fresh(self, now: float | None = None) -> bool:
        """Whether BUDGET has been verified recently enough to serve from.

        Gated by the BUDGET ``KindGate`` the same way the plan/KS snapshots gate freshness: fresh
        only when the stamp is fresh AND this process has read BUDGET to its head within the
        window. With no gate injected it reports fresh -- the not-yet-wired case, matching the
        plan and kill-switch snapshots' optional-stamp contract. ``/readyz`` wiring is task 10.4.
        """
        if self._gate is None:
            return True
        return self._gate.fresh(now)

    def unverified(self) -> str | None:
        """Why BUDGET must not be served from RAM, or None when it may be. For the 503 detail."""
        if self._gate is None or self._gate.fresh():
            return None
        return self._gate.unverified()

    # --- refresh path: O(changes) --------------------------------------------------------------

    def replace(self, record: BudgetRecord) -> int | None:
        """Install a served record for one org, replacing any prior one for that org.

        Returns the generation it replaced when the org was already served under a different
        generation, else None (a first record, or an unchanged generation). The applier uses that
        to decide whether to fire the re-acquire hook, so the decision stays with the component
        that knows both generations.
        """
        with self._lock:
            previous = self._records.get(record.org)
            self._records[record.org] = record
            if previous is not None and previous.generation != record.generation:
                return previous.generation
            return None


def budget_applier(
    snapshot: BudgetSnapshot,
    registry_hook: Callable[[str], None],
) -> Callable[[FeedRound], None]:
    """Bind a snapshot and the registry re-acquire hook to the round shape the synchroniser drives.

    On a drained round it replaces the served ``BudgetRecord`` for every org named in the round's
    records and leaves every org NOT named in the round untouched (Req 7.2) -- the snapshot is a
    per-org map, so an unnamed org simply keeps its entry. A round with no BUDGET changes applies
    nothing, which is the steady-state short circuit.

    Decode is ``record_of``; any body defect, kind mismatch, or the drift ``StoreDataUnavailable``
    the feed reader raised before the round was built propagates out of the applier. The decode
    runs over the whole round before any served record is touched, so a bad record late in a round
    does not leave the served set half-updated: either every named org advances or none does, and
    ``round_once`` leaves the cursor put on the raise (Req 7.4).

    When a record moves an org's generation to a value other than the one currently served, the
    hook (``QuotaRegistry.on_generation_change``) is called so the org's lease re-acquires under
    the new generation (Req 7.3). The hook fires only after every record decoded, so a refused
    round never marks an org for re-acquire.
    """

    def apply(round_: FeedRound) -> None:
        decoded = [record_of(record) for record in round_.records]
        changed: list[str] = []
        for budget in decoded:
            if snapshot.replace(budget) is not None:
                changed.append(budget.org)
        for org in changed:
            registry_hook(org)

    return apply
