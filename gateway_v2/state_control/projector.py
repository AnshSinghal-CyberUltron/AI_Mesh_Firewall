"""Turn control's rows into the signed state the gateways read (GW36 S2).

Control (Django) is the system of record. Nothing else derived signed KEY, PLAN or KILL SWITCH
records from it: control wrote v1's unsigned `auth:apikey:*` and an unsigned plan document, and
`StateWriter` had no non-test caller. This is that missing hop. It reads one consistent snapshot
of control's tables, diffs it against `amf_state_record`, and writes only what differs.

Why ONE writer, under a lease, when the re-hydrators are deliberately leaderless: a re-hydrator
copies Postgres into the store, so two of them converge on the same truth. A projector copies a
MOVING source into Postgres. Two leaderless projectors can order writes against source time --
A reads "key active", B reads "revoked" and writes the revoke, A then writes "active" -- and a
revoked key comes back. Every write is fenced on the lease inside its own transaction
(`writer.Fence`), and each fenced write renews it, so a long round never fences itself out.

The round has an order, and the order is the safety argument:

    1. kill switches ON         (stop traffic first)
    2. key revokes and changes  (no revoked key stays live)
    3. plans: set and offboard  (an org's plan exists before its keys)
    -- safety heartbeat: everything a console revocation needs is durable as of the read --
    4. kill switches OFF        (resume traffic last)
    5. new keys and un-revokes  (onboarding, bounded per round)

The re-hydrator withholds the fleet's stamp when the heartbeat is older than the declared lag,
so a stopped projector fails the fleet closed instead of serving state nobody re-derived. Steps 4
and 5 are liveness only, which is why they run after the heartbeat and why bulk onboarding is
spread over rounds: it can delay a new key, never a revocation.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from gateway_v2.detect.conditions import compile_conditions
from gateway_v2.domain.state import SignedRecord, StateKind, StateOp
from gateway_v2.domain.state_knobs import ProjectorKnobs
from gateway_v2.plan.compiler import CompileError, compile_plan
from gateway_v2.plan.document import decode_plan_body
from gateway_v2.runtime.state_sig import body_hash, canonical_body
from state_control.db import KindCounters
from state_control.source import ControlSource, SourceView
from state_control.writer import UNCHANGED, Fence, LeaseLost, StateWriter, WriteOutcome

LOG = logging.getLogger("amf.state.projector")

_SLUG = re.compile(r"[-a-zA-Z0-9_]{1,64}")
"""A v2 org id: Django's SlugField shape, enforced here because the database does not enforce it
(slugs created with `get_or_create(slug=...)` skip validation). An empty slug would become the KS
scope `org:`, which the reader refuses for the WHOLE kind, i.e. for every tenant."""

_KEY_HASH = re.compile(r"[0-9a-f]{64}")
"""`GatewayAPIKey.hash_raw_key`: sha256 hex. Anything else cannot be what a bearer hashes to."""

_ORG_WIDE = "__global__"
_DISABLE = "disable"


class ProjectorDB(Protocol):
    def claim_lease(self, holder: str, ttl_ms: int) -> bool:
        ...

    def mark_projected(self, holder: str, as_of: float) -> bool:
        ...

    def release_lease(self, holder: str) -> None:
        ...

    def snapshot(
        self,
        kind: StateKind,
    ) -> tuple[KindCounters, tuple[SignedRecord, ...], tuple[str, ...]]:
        ...


@dataclass(frozen=True, slots=True)
class ProjectionSummary:
    """What one round did. Every field is a count or a flag: nothing names a tenant."""

    leader: bool
    heartbeat: bool = False
    """The safety heartbeat was written this round."""
    complete: bool = False
    as_of: float | None = None
    writes: int = 0
    unchanged: int = 0
    revokes: int = 0
    adds: int = 0
    adds_deferred: int = 0
    invalid_plans: int = 0
    refused_conditions: int = 0
    orgs_without_plan: int = 0
    unsupported_switches: int = 0
    keys_without_org: int = 0
    invalid_names: int = 0
    lease_lost: bool = False


@dataclass
class _Tally:
    writes: int = 0
    unchanged: int = 0
    revokes: int = 0
    adds: int = 0

    def count(self, outcome: WriteOutcome) -> None:
        if outcome.status == UNCHANGED:
            self.unchanged += 1
        else:
            self.writes += outcome.records


@dataclass(frozen=True, slots=True)
class _Desired:
    """The state control's snapshot implies. Computed once per round, pure."""

    keys: Mapping[str, Mapping[str, object]]
    plans: Mapping[str, Mapping[str, object]]
    """Valid documents only. An active org with an invalid one keeps its previous record."""
    switches_on: frozenset[str]
    active_orgs: frozenset[str]
    invalid_plans: int = 0
    refused_conditions: int = 0
    orgs_without_plan: int = 0
    unsupported_switches: int = 0
    keys_without_org: int = 0
    invalid_names: int = 0
    notes: tuple[str, ...] = field(default=())


class Projector:
    """One round at a time, driven by `projector_service`. Every decision is re-derived from the
    round's own reads; the only thing kept between rounds is which warnings were already logged."""

    def __init__(
        self,
        db: ProjectorDB,
        source: ControlSource,
        writer: StateWriter,
        *,
        holder: str,
        knobs: ProjectorKnobs,
        chunk: int = 500,
    ) -> None:
        if not holder or "\n" in holder:
            raise ValueError("projector holder id must be non-empty and single-line")
        self._db = db
        self._source = source
        self._writer = writer
        self._holder = holder
        self._knobs = knobs
        self._chunk = chunk
        self._fence = Fence(holder, knobs.lease_ms)
        self._noted: frozenset[str] = frozenset()
        """Warnings already logged, so a standing condition is one line, not one per second."""

    def round_once(self) -> ProjectionSummary:
        """Claim the lease, project, heartbeat. A database or store fault propagates."""
        if not self._db.claim_lease(self._holder, self._knobs.lease_ms):
            return ProjectionSummary(leader=False)
        view = self._source.read()
        desired = desired_state(view)
        for note in desired.notes:
            if note not in self._noted:
                LOG.warning("%s", note)
        self._noted = frozenset(desired.notes)
        tally = _Tally()
        try:
            current = {kind: self._db.snapshot(kind) for kind in _KINDS}
            self._switches_on(desired, current[StateKind.KS], tally)
            self._key_safety(desired, current[StateKind.KEY], tally)
            self._plans(desired, current[StateKind.PLAN], tally)
            heartbeat = self._db.mark_projected(self._holder, view.as_of)
            if not heartbeat:
                raise LeaseLost("lease expired before the safety heartbeat")
            self._switches_off(desired, current[StateKind.KS], tally)
            deferred = self._key_adds(desired, current[StateKind.KEY], tally)
        except LeaseLost as exc:
            LOG.warning("projector %s lost the lease mid-round: %s", self._holder, exc)
            return self._summary(view, desired, tally, heartbeat=False, lease_lost=True)
        return self._summary(view, desired, tally, heartbeat=True, deferred=deferred)

    def release(self) -> None:
        """Hand the lease over on a clean stop. Never raises: shutdown must not hang on it."""
        try:
            self._db.release_lease(self._holder)
        except Exception as exc:  # noqa: BLE001 - the TTL is the fallback
            LOG.warning("projector %s could not release the lease: %s", self._holder, exc)

    # --- the phases, in order ------------------------------------------------------------------

    def _switches_on(self, desired: _Desired, ks: _Snapshot, tally: _Tally) -> None:
        engaged = set(ks[2])
        for scope in sorted(desired.switches_on - engaged):
            tally.count(self._put(StateKind.KS, scope, {"on": True}, engaged=True))

    def _key_safety(self, desired: _Desired, keys: _Snapshot, tally: _Tally) -> None:
        """Revoke what is no longer live; rewrite a live key whose body changed (org moved)."""
        for record in keys[1]:
            if record.deleted:
                continue
            body = desired.keys.get(record.key)
            if body is None:
                tally.count(
                    self._put(
                        StateKind.KEY, record.key, _body_of(record),
                        deleted=True, op=StateOp.REVOKE,
                    ),
                )
                tally.revokes += 1
            elif _differs(record, body):
                tally.count(self._put(StateKind.KEY, record.key, body))

    def _plans(self, desired: _Desired, plans: _Snapshot, tally: _Tally) -> None:
        held = {record.key: record for record in plans[1]}
        for slug, body in sorted(desired.plans.items()):
            record = held.get(slug)
            if record is None or record.deleted or _differs(record, body):
                tally.count(self._put(StateKind.PLAN, slug, body))
        for record in plans[1]:
            if record.deleted or record.key in desired.active_orgs:
                continue
            tally.count(
                self._put(
                    StateKind.PLAN, record.key, {"org_id": record.key},
                    deleted=True, op=StateOp.OFFBOARD,
                ),
            )

    def _switches_off(self, desired: _Desired, ks: _Snapshot, tally: _Tally) -> None:
        for scope in sorted(set(ks[2]) - desired.switches_on):
            tally.count(self._put(StateKind.KS, scope, {"on": False}, engaged=False))

    def _key_adds(self, desired: _Desired, keys: _Snapshot, tally: _Tally) -> int:
        """New keys in bounded chunks, then un-revokes. Returns how many adds wait a round."""
        held = {record.key: record for record in keys[1]}
        fresh = [(key, body) for key, body in sorted(desired.keys.items()) if key not in held]
        budget = self._knobs.add_chunks_per_round * self._chunk
        for start in range(0, min(len(fresh), budget), self._chunk):
            batch = fresh[start : min(start + self._chunk, budget)]
            outcome = self._writer.put_many(
                StateKind.KEY, batch, require_new=True, fence=self._fence,
            )
            tally.count(outcome)
            tally.adds += len(batch)
        for key, body in sorted(desired.keys.items()):
            record = held.get(key)
            if record is not None and record.deleted:
                tally.count(self._put(StateKind.KEY, key, body))
        return max(len(fresh) - budget, 0)

    # --- plumbing ------------------------------------------------------------------------------

    def _put(
        self,
        kind: StateKind,
        key: str,
        body: Mapping[str, object],
        *,
        deleted: bool = False,
        op: StateOp = StateOp.PUT,
        engaged: bool | None = None,
    ) -> WriteOutcome:
        return self._writer.put(
            kind, key, body,
            deleted=deleted, op=op, engaged=engaged, if_changed=True, fence=self._fence,
        )

    @staticmethod
    def _summary(
        view: SourceView,
        desired: _Desired,
        tally: _Tally,
        *,
        heartbeat: bool,
        lease_lost: bool = False,
        deferred: int = 0,
    ) -> ProjectionSummary:
        return ProjectionSummary(
            leader=not lease_lost,
            heartbeat=heartbeat,
            complete=heartbeat and not lease_lost,
            as_of=view.as_of,
            writes=tally.writes,
            unchanged=tally.unchanged,
            revokes=tally.revokes,
            adds=tally.adds,
            adds_deferred=deferred,
            invalid_plans=desired.invalid_plans,
            refused_conditions=desired.refused_conditions,
            orgs_without_plan=desired.orgs_without_plan,
            unsupported_switches=desired.unsupported_switches,
            keys_without_org=desired.keys_without_org,
            invalid_names=desired.invalid_names,
            lease_lost=lease_lost,
        )


_KINDS = (StateKind.KEY, StateKind.PLAN, StateKind.KS)
_Snapshot = tuple[KindCounters, tuple[SignedRecord, ...], tuple[str, ...]]


def desired_state(view: SourceView) -> _Desired:
    """The records control's snapshot implies. Pure: the property tests drive it directly."""
    notes: list[str] = []
    invalid_names = 0
    active: set[str] = set()
    for org in view.orgs:
        if not _SLUG.fullmatch(org.slug):
            invalid_names += 1
            notes.append(f"org slug {org.slug!r} is not a valid v2 org id; not projected")
        elif org.active:
            active.add(org.slug)
    keys: dict[str, Mapping[str, object]] = {}
    keys_without_org = 0
    for key in view.keys:
        if not key.active:
            continue
        if key.org_slug is None:
            keys_without_org += 1
            continue
        if not _KEY_HASH.fullmatch(key.key_hash):
            invalid_names += 1
            continue
        if key.org_slug in active:
            keys[key.key_hash] = _key_body(key.key_id, key.org_slug)
    plans: dict[str, Mapping[str, object]] = {}
    invalid_plans = refused = 0
    documented = {plan.org_slug for plan in view.plans}
    for plan in view.plans:
        if plan.org_slug not in active:
            continue
        body, refusals, why = validate_plan(plan.org_slug, plan.body)
        refused += refusals
        if body is None:
            invalid_plans += 1
            notes.append(
                f"plan document for {plan.org_slug!r} revision {plan.revision} is not "
                f"projectable ({why}); the previous record, if any, stays",
            )
        else:
            plans[plan.org_slug] = body
    without_plan = len(active - documented)
    switches_on: set[str] = set()
    unsupported = 0
    for switch in view.switches:
        if switch.org_slug is not None and switch.org_slug not in active:
            continue  # the org's keys are revoked; its switch has nothing left to stop
        if (
            switch.org_slug is not None
            and switch.model_name == _ORG_WIDE
            and not switch.api_key_prefix
            and switch.action == _DISABLE
        ):
            switches_on.add(f"org:{switch.org_slug}")
            continue
        # v2 scopes are `global`, `org:<id>` and `model:<name>`, and `model:` is GLOBAL: mapping an
        # org's model switch onto it would stop that model for every tenant. Request-path
        # enforcement and an org-scoped model scope are GW29; until then this is said out loud.
        unsupported += 1
        notes.append(
            f"engaged kill switch org={switch.org_slug!r} model={switch.model_name!r} "
            f"credential_prefix={bool(switch.api_key_prefix)} action={switch.action!r} has no v2 "
            "scope and is NOT enforced by gateway v2 (GW29)",
        )
    return _Desired(
        keys=keys,
        plans=plans,
        switches_on=frozenset(switches_on),
        active_orgs=frozenset(active),
        invalid_plans=invalid_plans,
        refused_conditions=refused,
        orgs_without_plan=without_plan,
        unsupported_switches=unsupported,
        keys_without_org=keys_without_org,
        invalid_names=invalid_names,
        notes=tuple(notes),
    )


def validate_plan(slug: str, raw: str) -> tuple[Mapping[str, object] | None, int, str]:
    """`(body to sign, refused customer conditions, why not)`.

    The checks a gateway's `plan_from_record` runs, plus the ones that would otherwise abort the
    whole round on one tenant's data: the body must canonicalise -- no NaN, and no lone surrogate,
    which cannot be encoded as UTF-8. (A NUL cannot reach Postgres `text`: JSON escapes every
    control character as `\\u0000` even with `ensure_ascii=False`.) A customer regex the matcher
    refuses is NOT a rejection: the gateway refuses that rule per request
    (`POLICY_REGEX_TIMEOUT`), and the count is reported so the console can show it.
    """
    try:
        parsed: Any = json.loads(raw)
        if not isinstance(parsed, dict):
            return None, 0, "body is not a JSON object"
        canonical = canonical_body(parsed)
    except (ValueError, UnicodeEncodeError) as exc:
        return None, 0, f"{type(exc).__name__}: {exc}"[:200]
    try:
        document = decode_plan_body(canonical)
        if document.org_id != slug:
            return None, 0, f"body claims org {document.org_id!r}"
        plan = compile_plan(
            document.org_id,
            document.drafts,
            streaming_mode=document.streaming_mode,
            compiled_at=0.0,
        )
    except CompileError as exc:
        return None, 0, f"CompileError: {exc}"[:200]
    refused = sum(1 for item in compile_conditions(plan.rules).prepared if item.refused)
    return parsed, refused, ""


def _key_body(key_id: str, org_slug: str) -> Mapping[str, object]:
    """Rate 0 = no per-key request limit (GW36 §9.3: control configures TPM only). Declared."""
    return {"key_id": key_id, "org_id": org_slug, "rate_per_s": 0.0, "burst": 0.0}


def _body_of(record: SignedRecord) -> Mapping[str, object]:
    parsed: Any = json.loads(record.body)
    if not isinstance(parsed, dict):
        raise ValueError(f"{record.kind.value} record {record.key!r} body is not an object")
    return parsed


def _differs(record: SignedRecord, body: Mapping[str, object]) -> bool:
    """A local pre-filter so an unchanged estate costs no transaction. The writer's
    `if_changed` re-checks under the counter lock, which is the authoritative comparison."""
    return record.content_hash != body_hash(canonical_body(body))
