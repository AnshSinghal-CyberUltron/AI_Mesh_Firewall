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
from itertools import accumulate
from types import MappingProxyType
from typing import Any, Protocol

from gateway_v2.detect.conditions import MAX_GROUP_DEPTH, compile_conditions
from gateway_v2.domain.identity import DENY_ALL_TOOLS
from gateway_v2.domain.state import SignedRecord, StateKind, StateOp
from gateway_v2.domain.state_knobs import ProjectorKnobs
from gateway_v2.plan.compiler import CompileError, compile_plan
from gateway_v2.plan.document import decode_plan_body
from gateway_v2.runtime.resources_mcp import mcp_policy_bounds
from gateway_v2.runtime.state_sig import body_hash, canonical_body
from gateway_v2.runtime.store_keys import KEYS, StoreKeys
from state_control.db import KindCounters
from state_control.incident_scopes import (
    IncidentScopePolicy,
    is_isolation_scope,
    org_model_projection,
)
from state_control.source import ControlSource, SourceKey, SourceSwitch, SourceView
from state_control.writer import UNCHANGED, Fence, LeaseLost, StateWriter, WriteOutcome

LOG = logging.getLogger("amf.state.projector")

_MAX_SEED_CAS_TRIES = 16
"""Bound on the pool-seed WATCH/MULTI compare-and-set retries before this round gives up (Req 10.6).

Mirrors the lease's `_MAX_CAS_TRIES` idiom (`admit/lease.py`): not a capacity position, just the
retry budget for the optimistic transaction. On exhaustion the counter is left unchanged and the
next round re-seeds from the same record (Req 10.3, 10.6); the projector is single-writer under the
lease, so contention here is defence-in-depth against a stale re-seed racing a live decrement."""

_SLUG = re.compile(r"[-a-zA-Z0-9_]{1,64}")
"""A v2 org id: Django's SlugField shape, enforced here because the database does not enforce it
(slugs created with `get_or_create(slug=...)` skip validation). An empty slug would become the KS
scope `org:`, which the reader refuses for the WHOLE kind, i.e. for every tenant."""

_KEY_HASH = re.compile(r"[0-9a-f]{64}")
"""`GatewayAPIKey.hash_raw_key`: sha256 hex. Anything else cannot be what a bearer hashes to."""

_ORG_WIDE = "__global__"
_DISABLE = "disable"

MAX_PLAN_BODY_DEPTH = 32
"""The deepest array/object nesting a plan body may have; measured BEFORE `json` parses it.

A published body is at most five levels deep (body, rules, rule, condition, keywords). `json`
decodes recursively, and how deep it gets before `RecursionError` depends on the C stack its
caller already uses. So the projector, on its own stack, signed an unknown-key array ~9,996
deep that every gateway applier, deeper in its own stack, then failed to decode (#117 review
B1 / A-S2): one signed record aborted the PLAN round of the whole fleet. Measuring first makes
the verdict a property of the bytes alone, and everything signed decodes on any stack.

TWIN: the gateway applier's `gateway_v2.plan.document.MAX_BODY_DEPTH` / `nesting_depth`
(fix/state-applier-isolation, #136) uses the same limit and the same three-pass scan. The two
MUST agree: a body this projector signs must be one every gateway decodes.
`tests/state_control/test_projector_body_depth.py` asserts it once both are on develop."""

_JSON_ESCAPE = re.compile(r"\\.", re.DOTALL)
_JSON_STRING = re.compile(r'"[^"]*"')
_JSON_NOT_BRACKET = re.compile(r"[^\[\]{}]+")
_JSON_STEP = MappingProxyType({"[": 1, "{": 1, "]": -1, "}": -1})


def body_depth(raw: str) -> int:
    """The deepest bracket nesting in ``raw``, outside JSON strings. Linear; never recurses.

    The same three passes as the gateway's `nesting_depth`: drop every two-character escape (so
    ``\\"`` cannot end a string), drop every string (a bracket inside one does not count), drop
    everything that is not a bracket; then a running sum. For any body `json` would parse this
    is its exact depth; for a malformed one it is at least the depth `json` reaches before the
    first defect, where it raises without recursing further.
    """
    brackets = _JSON_NOT_BRACKET.sub("", _JSON_STRING.sub("", _JSON_ESCAPE.sub("", raw)))
    return max(0, max(accumulate(map(_JSON_STEP.__getitem__, brackets)), default=0))


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


class PoolSeeder(Protocol):
    """Seeds the LIVE budget pool counter with a generation CAS (Req 10).

    Distinct from `StateWriter`: the signed BUDGET config record is written through the writer;
    the live `{rv2}:budget:{org}:remaining` / `:generation` counters the gateway's `BudgetLease`
    spends against are a direct store family (`runtime/store_keys.py`), never a signed record. One
    `seed` call is a WATCH/MULTI compare-and-set conditioned on the stored generation (see
    `ValkeyPoolSeeder`). It returns `True` when the counter now carries this generation (seeded or
    already equal) and `False` only when the CAS lost its race `_MAX_SEED_CAS_TRIES` times.
    """

    def seed(self, org: str, *, pool_tokens: int, generation: int) -> bool:
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
    budgets: Mapping[str, int]
    """Active orgs with a stored control budget → pool tokens. An org with no row has no entry."""
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
        seeder: PoolSeeder | None = None,
        incident_scopes: IncidentScopePolicy | None = None,
    ) -> None:
        if not holder or "\n" in holder:
            raise ValueError("projector holder id must be non-empty and single-line")
        self._db = db
        self._source = source
        self._writer = writer
        self._holder = holder
        self._knobs = knobs
        self._chunk = chunk
        self._seeder = seeder
        """Seeds the live pool counter after each BUDGET write (Req 10). `None` leaves the counter
        untouched: `projector_service` always wires one; a test that only exercises the signed
        writes may leave it out."""
        self._fence = Fence(holder, knobs.lease_ms)
        self._incident_scopes = incident_scopes or IncidentScopePolicy()
        """GW29 rollout guard: org-model scopes are projected only when this is `enabled`."""
        if self._incident_scopes.refusal is not None:
            LOG.error("%s", self._incident_scopes.refusal)
        self._noted: frozenset[str] = frozenset()
        """Warnings already logged, so a standing condition is one line, not one per second."""

    def round_once(self) -> ProjectionSummary:
        """Claim the lease, project, heartbeat. A database or store fault propagates."""
        if not self._db.claim_lease(self._holder, self._knobs.lease_ms):
            return ProjectionSummary(leader=False)
        view = self._source.read()
        desired = desired_state(
            view,
            self._incident_scopes,
            mcp_key_policy=self._knobs.project_mcp_key_policy,
        )
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
            self._budgets(desired, current[StateKind.BUDGET], tally)
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
            if is_isolation_scope(scope):
                continue  # GW29: iso: is written by StateWriter.isolation, never derived here
            tally.count(self._put(StateKind.KS, scope, {"on": False}, engaged=False))

    def _budgets(self, desired: _Desired, budgets: _Snapshot, tally: _Tally) -> None:
        """Write one signed BUDGET record per org with a stored control budget, then seed its pool.

        Liveness, not revocation: the phase runs after the safety heartbeat alongside the other
        onboarding phases. `if_changed` leaves an unchanged budget untouched (Req 9.5); a changed
        budget bumps the record's `feed_seq` -- the Budget_Generation -- strictly upward (Req 9.4);
        a first-time org gets its first record and initial generation (Req 9.3). An org with no
        stored budget simply has no entry in `desired.budgets`, so nothing is written and any prior
        record is left in place (Req 9.6): budgets, unlike plans, have no offboard phase in S4.

        After writing (or finding unchanged) each org's record the live pool counter is seeded with
        a generation CAS against the record's own `feed_seq` (Req 10). A changed body returns the
        new `feed_seq` on the `WriteOutcome`; an `unchanged` outcome carries the kind-level cursor,
        not the record's, so the seed reads the held record's `feed_seq` from the round's snapshot.
        """
        held = {record.key: record for record in budgets[1]}
        for slug, pool_tokens in sorted(desired.budgets.items()):
            outcome = self._put(
                StateKind.BUDGET,
                slug,
                {"org_id": slug, "pool_tokens": pool_tokens},
            )
            tally.count(outcome)
            feed_seq = self._budget_generation(slug, outcome, held)
            if feed_seq is not None:
                self._seed_pool(slug, pool_tokens=pool_tokens, generation=feed_seq)

    @staticmethod
    def _budget_generation(
        slug: str,
        outcome: WriteOutcome,
        held: Mapping[str, SignedRecord],
    ) -> int | None:
        """The record's own `feed_seq` -- the Budget_Generation -- to seed against (Req 10.4).

        A write (`records > 0`) just minted this record, so the kind's post-write `feed_seq` on the
        outcome IS this record's `feed_seq`. An `unchanged` outcome carries the kind-level cursor,
        which may sit above this record if another org was written earlier in the phase, so the held
        record's own `feed_seq` from the round's snapshot is authoritative. A missing held record on
        an `unchanged` outcome cannot happen (unchanged means a record was found), so it returns
        `None` and seeds nothing rather than guess a generation.
        """
        if outcome.records > 0:
            return outcome.feed_seq
        record = held.get(slug)
        return record.feed_seq if record is not None else None

    def _seed_pool(self, slug: str, *, pool_tokens: int, generation: int) -> None:
        """Seed the live pool counter with a generation CAS (Req 10). Never writes a signed record.

        Delegates to the injected `PoolSeeder`, which runs the WATCH/MULTI compare-and-set over
        `{rv2}:budget:{org}:remaining` and `:generation`: it re-seeds `remaining = pool_tokens` /
        `generation = feed_seq` only on a new generation (Req 10.1, 10.2, 10.4), leaves the counter
        untouched when the stored generation already equals `feed_seq` so a live lease decrement is
        not clobbered (Req 10.5), and gives up for this round after a bounded number of CAS losses
        (Req 10.3, 10.6). A store fault here never aborts the round: the signed record is already
        durable, and the next round re-seeds from the same `feed_seq`.
        """
        if self._seeder is None:
            return
        try:
            self._seeder.seed(slug, pool_tokens=pool_tokens, generation=generation)
        except Exception as exc:  # noqa: BLE001 - the signed record is durable; the seed retries
            if "budget-seed" not in self._noted:
                LOG.warning(
                    "projector could not seed a budget pool counter (%s: %s); the signed BUDGET "
                    "record is durable and the next round retries the seed",
                    type(exc).__name__,
                    exc,
                )
                self._noted = self._noted | {"budget-seed"}

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


_KINDS = (StateKind.KEY, StateKind.PLAN, StateKind.KS, StateKind.BUDGET)
_Snapshot = tuple[KindCounters, tuple[SignedRecord, ...], tuple[str, ...]]


def desired_state(
    view: SourceView,
    incident_scopes: IncidentScopePolicy | None = None,
    *,
    mcp_key_policy: bool = False,
) -> _Desired:
    """The records control's snapshot implies. Pure: the property tests drive it directly.
    `incident_scopes` is the GW29 rollout guard; None (the default) keeps org-model switches
    unsupported, so a fleet that has not rolled never receives a scope it cannot read.
    `mcp_key_policy` (GW17c) projects per-key MCP allow-list/cap only when on."""
    policy = incident_scopes or IncidentScopePolicy()
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
            allowed, cap = _mcp_policy(key, mcp_key_policy, notes)
            keys[key.key_hash] = _key_body(key.key_id, key.org_slug, allowed, cap)
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
            if refusals:
                # Signed by design (the gateway refuses the rule per request), so without this
                # line the only trace is the fleet-wide `refused_conditions` gauge, naming no org.
                # Org, revision and a count only: never a rule id or pattern (tenant data).
                notes.append(
                    f"plan document for {plan.org_slug!r} revision {plan.revision} is signed "
                    f"with {refusals} customer condition(s) the matcher refuses (ReDoS shape, "
                    f"groups nested deeper than {MAX_GROUP_DEPTH}, or does not compile); "
                    "gateways refuse those rules per request (policy_regex_timeout)",
                )
    without_plan = len(active - documented)
    budgets: dict[str, int] = {}
    for budget in view.budgets:
        if budget.org_slug in active:
            budgets[budget.org_slug] = budget.pool_tokens
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
        if (
            policy.enabled
            and switch.org_slug is not None
            and switch.model_name != _ORG_WIDE
            and not switch.api_key_prefix
            and switch.action == _DISABLE
        ):
            scope = org_model_projection(switch.org_slug, switch.model_name)
            if scope is not None:
                switches_on.add(scope)
                continue
            invalid_names += 1
            notes.append(
                f"kill switch org={switch.org_slug!r} model name is not a valid model name; "
                "not projected",
            )
            continue
        # `model:<name>` is GLOBAL: mapping an org's model switch onto it would stop that model
        # for every tenant, so a switch with no safe scope is counted and said out loud.
        unsupported += 1
        notes.append(_unsupported_note(switch, policy))
    return _Desired(
        keys=keys,
        plans=plans,
        budgets=budgets,
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


def _unsupported_note(switch: SourceSwitch, policy: IncidentScopePolicy) -> str:
    detail = (
        f"engaged kill switch org={switch.org_slug!r} model={switch.model_name!r} "
        f"credential_prefix={bool(switch.api_key_prefix)} action={switch.action!r}"
    )
    if switch.api_key_prefix:
        return f"{detail} has no v2 scope and is NOT enforced by gateway v2"
    if switch.org_slug is not None and switch.action == _DISABLE and not policy.enabled:
        return (
            f"{detail} maps to an org-model scope, which is not projected until "
            "AMF_STATE_PROJECT_INCIDENT_SCOPES is on and AMF_GATEWAY_MIN_SCOPE_GENERATION is "
            "declared; it is NOT enforced by gateway v2 until then"
        )
    return f"{detail} has no v2 scope and is NOT enforced by gateway v2"


def validate_plan(slug: str, raw: str) -> tuple[Mapping[str, object] | None, int, str]:
    """`(body to sign, refused customer conditions, why not)`.

    The checks a gateway's `plan_from_record` runs, plus the ones that would otherwise abort the
    whole round on one tenant's data: the body must canonicalise -- no NaN, and no lone surrogate,
    which cannot be encoded as UTF-8. (A NUL cannot reach Postgres `text`: JSON escapes every
    control character as `\\u0000` even with `ensure_ascii=False`.) A customer regex the matcher
    refuses is NOT a rejection: the gateway refuses that rule per request
    (`POLICY_REGEX_TIMEOUT`), and the count is reported so the console can show it.

    A body nested deeper than `MAX_PLAN_BODY_DEPTH` is not projectable, decided before `json`
    parses it, so the verdict does not depend on this caller's stack and nothing signed is too
    deep for a gateway's.

    No exception escapes. Anything the checks did not anticipate makes THIS document not
    projectable, so the caller keeps the org's previous record and the round still reaches the
    heartbeat for every other tenant. Letting it escape would fail every round while the row
    exists, age the heartbeat, and fail the whole fleet closed on one tenant's data.

    The reason is a fixed phrase, prefixed by the exception's TYPE where there is one: never an
    exception's text and never a value from the body. It goes into a note the round logs, and an
    exception raised on one tenant's row can quote that row (#118 S8). An operator with access to
    the row gets the full message by running `compile_plan` on it.
    """
    try:
        return _validate_plan(slug, raw)
    except Exception as exc:  # noqa: BLE001 - one tenant's row must never stop the round
        return None, 0, f"{type(exc).__name__}: unanticipated fault while validating"


def _validate_plan(slug: str, raw: str) -> tuple[Mapping[str, object] | None, int, str]:
    if body_depth(raw) > MAX_PLAN_BODY_DEPTH:
        return None, 0, f"body nests deeper than {MAX_PLAN_BODY_DEPTH} levels"
    try:
        parsed: Any = json.loads(raw)
    except ValueError as exc:
        return None, 0, f"{type(exc).__name__}: body is not valid JSON"
    if not isinstance(parsed, dict):
        return None, 0, "body is not a JSON object"
    try:
        canonical = canonical_body(parsed)
    except (ValueError, UnicodeEncodeError) as exc:
        return None, 0, f"{type(exc).__name__}: no canonical form (NaN, Infinity, lone surrogate)"
    try:
        document = decode_plan_body(canonical)
        if document.org_id != slug:
            return None, 0, "body claims another org"
        plan = compile_plan(
            document.org_id,
            document.drafts,
            streaming_mode=document.streaming_mode,
            compiled_at=0.0,
            rag_settings=document.rag_settings,
        )
    except CompileError:
        return None, 0, "CompileError: the plan does not compile"
    refused = sum(1 for item in compile_conditions(plan.rules).prepared if item.refused)
    return parsed, refused, ""


def _key_body(
    key_id: str, org_slug: str, allowed: tuple[str, ...] = (), cap: int = 0
) -> Mapping[str, object]:
    """Rate 0 = no per-key request limit (GW36 §9.3: control configures TPM only). Declared.

    GW17c: the MCP fields are written ONLY when they differ from the defaults, so a key without
    a policy produces the same bytes (and content hash) as before this card.
    """
    body: dict[str, object] = {
        "key_id": key_id, "org_id": org_slug, "rate_per_s": 0.0, "burst": 0.0,
    }
    if allowed:
        body["mcp_allowed_tools"] = sorted(set(allowed))
    if cap:
        body["mcp_max_tool_calls"] = cap
    return body


def _mcp_policy(
    key: SourceKey, enabled: bool, notes: list[str]
) -> tuple[tuple[str, ...], int]:
    """The (allow-list, cap) to project for one key (DD-11). Never projects looser than asked:
    an over-bound or malformed allow-list is deny-all, an over-bound cap is the bound itself."""
    if not enabled:
        return (), 0
    bounds = mcp_policy_bounds()
    allowed = key.mcp_allowed_tools
    if allowed is None or len(allowed) > bounds.tools_max or any(
        not name or len(name) > bounds.tool_name_max for name in allowed
    ):
        notes.append(f"key {key.key_id!r} MCP allow-list is over-bound or malformed; deny-all")
        allowed = (DENY_ALL_TOOLS,)
    cap = key.mcp_max_tool_calls
    if cap < 0 or cap > bounds.cap_max:
        notes.append(f"key {key.key_id!r} MCP call cap is over-bound; projected as the bound")
        cap = bounds.cap_max
    return allowed, cap


def _body_of(record: SignedRecord) -> Mapping[str, object]:
    parsed: Any = json.loads(record.body)
    if not isinstance(parsed, dict):
        raise ValueError(f"{record.kind.value} record {record.key!r} body is not an object")
    return parsed


def _differs(record: SignedRecord, body: Mapping[str, object]) -> bool:
    """A local pre-filter so an unchanged estate costs no transaction. The writer's
    `if_changed` re-checks under the counter lock, which is the authoritative comparison."""
    return record.content_hash != body_hash(canonical_body(body))


def _as_int(value: object) -> int | None:
    """Coerce a store answer (bytes / str / int / None) to int; a missing key reads as `None`."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            return int(value)
        except ValueError:
            return None
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


class ValkeyPoolSeeder:
    """Seeds the live budget pool counter with a WATCH/MULTI generation CAS (Req 10).

    This is the seed half of the control->store budget path. The signed `StateKind.BUDGET` config
    record is written by `StateWriter`; this writes the LIVE counter family that the gateway's
    `BudgetLease` spends against (`{rv2}:budget:{org}:remaining`, `:generation` -- the names come
    from `runtime/store_keys.py`, never a literal here). Only the projector seeds these keys.

    Atomicity -- WATCH/MULTI, not Lua EVAL -- follows the house idiom (`admit/lease.py`): the
    `fakeredis` the suite runs against lacks `EVAL`, and a real Valkey behaves identically under
    the optimistic transaction. The generation is read INSIDE the `WATCH`, so the `EXEC` is guarded
    on the exact value the decision was made against; a concurrent writer that touched either key
    between the read and the `EXEC` raises `WatchError` and the whole cycle retries, bounded by
    `_MAX_SEED_CAS_TRIES`.

    A SYNCHRONOUS `redis` client (the same `redis.Redis` the projector's publisher uses), because a
    projector round is synchronous. `keys` carries the namespace so the seed lands in the store's
    one slot alongside every other `{rv2}:budget:{org}:*` key.
    """

    __slots__ = ("_client", "_keys")

    def __init__(self, client: Any, *, keys: StoreKeys = KEYS) -> None:
        self._client = client
        self._keys = keys

    def seed(self, org: str, *, pool_tokens: int, generation: int) -> bool:
        """Compare-and-set the pool counter against `generation` (the record's `feed_seq`).

        Returns `True` when the counter carries this generation at the end of the call -- whether it
        was re-seeded on a new generation (Req 10.1, 10.2, 10.4) or already equal and left untouched
        (Req 10.5) -- and `False` only when the CAS lost its race `_MAX_SEED_CAS_TRIES` times, in
        which case the counter is left exactly as it was (Req 10.3) and the caller retries next
        round (Req 10.6).
        """
        from redis.exceptions import WatchError

        remaining_key = self._keys.budget_remaining(org)
        generation_key = self._keys.budget_generation(org)
        for _ in range(_MAX_SEED_CAS_TRIES):
            with self._client.pipeline() as pipe:
                try:
                    pipe.watch(remaining_key, generation_key)
                    stored = _as_int(pipe.get(generation_key))
                    if stored == generation:
                        # Already this generation: do NOT re-seed -- a live lease decrement against
                        # `remaining` must not be clobbered (Req 10.5).
                        pipe.unwatch()
                        return True
                    # A new (or absent) generation: re-seed the pool and advance the tag atomically,
                    # guarded by the WATCH on both keys (Req 10.1, 10.2, 10.4).
                    pipe.multi()
                    pipe.set(remaining_key, pool_tokens)
                    pipe.set(generation_key, generation)
                    pipe.execute()
                    return True
                except WatchError:
                    # A concurrent writer touched the pool or generation between the WATCH and the
                    # EXEC: the counter is unchanged (Req 10.3); retry the optimistic cycle.
                    continue
        return False
