"""The gateway's state runtime: one worker's view of the signed state (GW36 S3, G5).

Every reader already existed -- `FeedReader`, `StateSynchroniser`, the three appliers,
`StampView`, `NudgeListener` -- and nothing in product code built or scheduled them, so the
production boot could only refuse. This module builds them from the same environment the
state-control processes read (`AMF_STATE_VALKEY_URL`, `AMF_STATE_HMAC_KEY`, ...) and owns their
lifetime:

* **Construction is side-effect-free.** `serve.main` assembles the app once in the parent as a
  refuse-to-start probe and throws it away; every worker rebuilds. The redis client connects
  lazily, and no task exists until `startup()`.
* **One task owns every drain.** The nudge listener only sets an event that wakes it, so two
  drains can never race on a cursor.
* **Cold start is O(head), not O(records).** The kill switch adopts its engaged set; the
  identity cache, which holds nothing to evict, jumps its cursor to the head (fills are
  fetched on demand). Only plans are replayed, because a plan must be compiled to be served.
* **The loop never dies.** A store fault is a failed round (`StoreDataUnavailable`); anything
  else is logged with its class -- never its text, which can quote a tenant's record -- counted,
  and the next cycle runs.
* **One record never stops the cycle.** Every kind's round reports instead of raising, and PLAN
  and KEY are read with per-record isolation: a record that fails verification fails only its
  own tenant (PLAN) or key (KEY) closed, and every other tenant applies in the same round.
* **A quarantine is re-checked, bounded.** Once per freshness window, up to `RECHECK_SUBJECTS`
  quarantined keys and tenants are re-read; one whose stored record now verifies at its faulted
  position (a same-position repair by the re-hydrator) is restored without a restart.
* **A worker is not ready until it has caught up.** `startup()` waits, bounded by the freshness
  window, for its first good cycle, and uvicorn routes nothing to a worker before its lifespan
  startup completes -- so a cold worker does not flap `/readyz` while its siblings serve.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

from gateway_v2.admit.budget_state import BudgetSnapshot, budget_applier
from gateway_v2.admit.identity import IdentityCache, identity_applier, principal_of
from gateway_v2.admit.killswitch import (
    KillSwitchSnapshot,
    KillSwitchState,
    killswitch_adopter,
    killswitch_applier,
)
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.locks import FRESH_MS, PG_GRACE_MS
from gateway_v2.domain.state import SignedRecord, StateKind, StoreDataUnavailable
from gateway_v2.domain.state_knobs import (
    DEFAULT_REFRESH_PERIOD_MS,
    DEFAULT_REHYDRATE_PERIOD_MS,
    StateKnobs,
)
from gateway_v2.edge.identity_resolver import CachingIdentityResolver, hashed_bearer_key
from gateway_v2.plan.delta import PlanDeltaApplier
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import QUARANTINED, PlanStore
from gateway_v2.runtime.state_feed import FeedReader, FeedRound
from gateway_v2.runtime.state_gate import KindGates
from gateway_v2.runtime.state_metrics import StateMetricsRecorder
from gateway_v2.runtime.state_nudge import NudgeListener
from gateway_v2.runtime.state_sig import decode_manifest, decode_record, record_matches_index
from gateway_v2.runtime.state_stamp import StampView
from gateway_v2.runtime.state_task import RoundReport, StateSynchroniser
from gateway_v2.runtime.store_keys import DEFAULT_NAMESPACE, StoreKeys
from gateway_v2.runtime.store_valkey import ValkeyStateStore, require_bounded_client

LOG = logging.getLogger("amf.gateway.state")

_ASGIApp = Callable[
    [
        Mapping[str, object],
        Callable[[], Awaitable[Mapping[str, object]]],
        Callable[[Mapping[str, object]], Awaitable[None]],
    ],
    Awaitable[None],
]

#: The kinds a worker must have caught up before it is ready. BUDGET now HAS a gateway reader -- the
#: `QuotaRegistry` builds each org's lease from the served `BudgetSnapshot` the `budget_applier`
#: maintains -- so a worker serving budget decisions before it has read BUDGET to its head would
#: admit against a stale (or empty) pool, exactly the regression gating KEY/PLAN/KS prevents. BUDGET
#: drains to its (possibly empty) head on the normal cycle like PLAN, NOT via a cold-start adoption:
#: an empty-but-caught-up BUDGET head reports `ok and not truncated`, which `KindGates.observe`
#: records as fresh, so a worker with no BUDGET records still becomes ready once it has drained the
#: kind -- adding it to `READY_KINDS` does not wedge startup.
READY_KINDS = (StateKind.PLAN, StateKind.KS, StateKind.KEY, StateKind.BUDGET)

#: Kinds read with per-record isolation (`StateSynchroniser(isolate_kinds=...)`). A PLAN record that
#: fails verification quarantines its tenant (503 for that tenant only); a KEY record evicts its key
#: (the next request re-reads it and is refused while it still fails). Both fail CLOSED per subject.
ISOLATED_KINDS = frozenset({StateKind.PLAN, StateKind.KEY})

#: How many quarantined keys, and separately tenants, one re-check pass re-reads. A pass runs at
#: most once per freshness window, so a worker re-reads at most this many records per kind per
#: window however many subjects are quarantined: a store that keeps failing costs a bounded,
#: constant read rate, never one proportional to the tenant count.
RECHECK_SUBJECTS = 8


def _noop_reacquire(org: str) -> None:
    """The default BUDGET re-acquire hook before `apply_production` wires the registry's.

    A generation change seen before the `QuotaRegistry` is wired marks nothing; the registry reads
    the current served record on its first `component_for` for the org anyway, so the mark is only
    an optimisation and losing it before boot finishes is fail-safe.
    """
    del org


def _noop_revocations(records: Sequence[SignedRecord]) -> None:
    """Default revocation observer until the GW29 ledger is wired: nothing to record."""
    del records


def _noop_refuse(key_hash: str, feed_seq: int) -> None:
    """Default for a KEY record that failed verification, until the GW29 ledger is wired."""
    del key_hash, feed_seq


class RevocationSink(Protocol):
    """What the KEY applier tells the GW29 revocation ledger (`admit.revocation.RevocationLedger`).

    `observe` gets every verified KEY delta's records (a revoke among them cuts its key's
    in-flight streams); `refuse` gets a KEY record that FAILED verification, which may be a
    revocation nobody could read, so it cuts the key's streams admitted below that position.
    """

    def observe(self, records: Sequence[SignedRecord]) -> None: ...

    def refuse(self, key_hash: str, feed_seq: int) -> None: ...


def _noop_invalidate(key_hashes: Iterable[str]) -> None:
    """The default GCRA-invalidate hook before `apply_production` wires the cache's.

    A KEY delta seen before the `GcraCache` is wired invalidates nothing; a stale per-worker GCRA
    is rebuilt on the next request whose principal's `(rate_per_s, burst)` differ, so losing an
    invalidation before boot finishes is fail-safe (the allowance is at worst momentarily the old
    one, never missing).
    """
    # Drain the iterable so a generator-backed caller is not left half-consumed.
    for _ in key_hashes:
        pass


class StateConfigError(ValueError):
    """A required state setting is missing or invalid. The worker refuses to start."""


@dataclass(frozen=True, slots=True)
class StateRuntimeConfig:
    valkey_url: str
    secret: bytes
    namespace: str
    knobs: StateKnobs

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> StateRuntimeConfig:
        """The same names `state_control` reads, so one secret and one store serve both."""
        source = os.environ if env is None else env
        url = (source.get("AMF_STATE_VALKEY_URL") or "").strip()
        secret = (source.get("AMF_STATE_HMAC_KEY") or "").strip()
        if not url:
            raise StateConfigError("AMF_STATE_VALKEY_URL is required: the signed-state store")
        if not secret:
            raise StateConfigError(
                "AMF_STATE_HMAC_KEY is required and must match the re-hydrators' and the "
                "projector's, or every record and stamp fails its signature",
            )
        try:
            knobs = StateKnobs(
                fresh_ms=_int(source, "AMF_STATE_FRESH_MS", FRESH_MS),
                pg_grace_ms=_int(source, "AMF_STATE_PG_GRACE_MS", PG_GRACE_MS),
                rehydrate_period_ms=_int(
                    source, "AMF_REHYDRATE_PERIOD_MS", DEFAULT_REHYDRATE_PERIOD_MS,
                ),
                refresh_period_ms=_int(source, "AMF_STATE_REFRESH_MS", DEFAULT_REFRESH_PERIOD_MS),
            )
        except ValueError as exc:
            raise StateConfigError(str(exc)) from exc
        namespace = (source.get("AMF_STATE_NAMESPACE") or "").strip() or DEFAULT_NAMESPACE
        return cls(valkey_url=url, secret=secret.encode("utf-8"), namespace=namespace, knobs=knobs)


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise StateConfigError(f"{name} must be an integer, got {raw!r}") from exc


class StateRuntime:
    """One worker's signed-state readers, their gates, and the task that keeps them current."""

    def __init__(
        self,
        config: StateRuntimeConfig,
        *,
        client: Any = None,
        aliases: Mapping[str, str] | None = None,
    ) -> None:
        """`client` is injectable for tests; production builds a bounded `redis.asyncio` one.

        `aliases` is the GW29 model alias table, passed once to the kill-switch parser."""
        self._config = config
        knobs = config.knobs
        if client is None:
            import redis.asyncio

            timeout_s = knobs.store_timeout_ceiling_s / 2
            client = redis.asyncio.Redis.from_url(
                config.valkey_url, socket_timeout=timeout_s, socket_connect_timeout=timeout_s,
            )
            require_bounded_client(client, below_s=knobs.store_timeout_ceiling_s)
        self._client = client
        self._keys = StoreKeys(config.namespace)
        self._store = ValkeyStateStore(client, self._keys)
        self.stamp = StampView(config.secret, fresh_ms=knobs.fresh_ms)
        self.gates = KindGates(self.stamp, fresh_ms=knobs.fresh_ms)
        self.identity_cache = IdentityCache(stamp=self.gates.gate(StateKind.KEY))
        self.plans = PlanStore()
        self.snapshot = ReplicaSnapshot(
            self.plans, fresh_ms=knobs.fresh_ms, stamp=self.gates.gate(StateKind.PLAN),
        )
        self.switches = KillSwitchSnapshot(
            stale_ms=knobs.fresh_ms, stamp=self.gates.gate(StateKind.KS),
            aliases=(
                aliases if isinstance(aliases, MappingProxyType)
                else MappingProxyType(dict(aliases or {}))
            ),
        )
        self.budget_snapshot = BudgetSnapshot(gate=self.gates.gate(StateKind.BUDGET))
        # Two request-path collaborators are built LATER, in `apply_production` (edge), because they
        # need the contract, `q_safe`, the store client, and a worker id this runtime does not hold:
        # the per-org `QuotaRegistry` (whose `on_generation_change` the BUDGET applier must call)
        # and the per-principal `GcraCache` (whose entry a KEY delta must invalidate alongside the
        # identity cache). Both are reached through a mutable indirection set after construction, so
        # the appliers map below is COMPLETE at construction (Req 7.1 — all four kinds present) and
        # the registry/cache can still wire their hooks once built (`set_budget_reacquire_hook` /
        # `set_gcra_invalidate`). Until wired both default to a no-op: a round that fires them
        # before the boot finished simply does nothing, which is fail-safe (an un-invalidated GCRA
        # is rebuilt on the next principal change; an un-marked generation is re-read on first use).
        self._budget_reacquire_hook: Callable[[str], None] = _noop_reacquire
        self._gcra_invalidate: Callable[[Iterable[str]], None] = _noop_invalidate
        self._revocation_observe: Callable[[Sequence[SignedRecord]], None] = _noop_revocations
        self._revocation_refuse: Callable[[str, int], None] = _noop_refuse
        self._plan_delta = PlanDeltaApplier(self.plans, self.snapshot)
        # PLAN first: a key whose org was just onboarded resolves straight from the store, and
        # its plan must already be served by then (the projector writes plans before keys).
        self.sync = StateSynchroniser(
            FeedReader(self._store, config.secret),
            {
                StateKind.PLAN: self._plan_applier,
                StateKind.KS: killswitch_applier(self.switches),
                StateKind.KEY: self._key_applier(),
                StateKind.BUDGET: budget_applier(
                    self.budget_snapshot, lambda org: self._budget_reacquire_hook(org),
                ),
            },
            stamp=self.stamp,
            inline_kinds=frozenset({StateKind.KEY}),
            # Only kinds whose applier fails a faulted subject closed. KS and BUDGET keep their
            # whole-round refusal: an unreadable switch must never read as off, and a budget
            # round is applied all-or-nothing (Req 7.4).
            isolate_kinds=ISOLATED_KINDS,
        )
        self.metrics = StateMetricsRecorder()
        self._listener = NudgeListener(client, self._keys.updates)
        self._wake = asyncio.Event()
        self._first_cycle = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._cold: set[StateKind] = {StateKind.KS, StateKind.KEY}
        self._clock: Callable[[], float] = time.monotonic
        self._recheck_due = 0.0
        self._recheck_failing: str | None = None
        self.cycles = 0
        self.cycle_failures = 0

    # --- applier composition ------------------------------------------------------------------

    def _plan_applier(self, round_: FeedRound) -> object:
        """The PLAN applier (`plan.delta.plan_applier`'s shape), over the runtime's one applier."""
        return self._plan_delta.apply(round_.records, round_.faults)

    def _key_applier(self) -> Callable[[FeedRound], None]:
        """The KEY applier: evict the identity cache AND invalidate the GCRA cache on a delta.

        `identity_applier` already evicts exactly the key hashes a KEY delta names from the identity
        cache (and advances the applied position on an empty round via `mark_applied`). The GCRA
        cache must drop the SAME hashes so a key whose resolved `Principal` changed rebuilds its
        per-worker `LocalGCRA` from the updated `rate_per_s`/`burst` (Req 3.3). `on_delta` returns
        the evicted hashes, so this composes the shipped applier's eviction with a
        `_gcra_invalidate` call over the same hashes — one KEY delta, both caches invalidated
        together. A round with no records only advances the position and invalidates nothing
        (there is nothing to rebuild).
        """
        identity = identity_applier(self.identity_cache)

        def apply(round_: FeedRound) -> None:
            if round_.faults:
                # A KEY record that failed verification: the key is QUARANTINED at the fault's
                # index position -- nothing cached for it survives, in either cache, and every
                # fill below that position is refused (503) until a verified record at or above
                # it arrives -- and its in-flight streams are cut, because the record may be a
                # revocation this gateway could not read.
                faulted = tuple(fault.key for fault in round_.faults)
                self.identity_cache.quarantine(
                    ((fault.key, fault.feed_seq) for fault in round_.faults),
                    round_.manifest.feed_seq,
                )
                self._gcra_invalidate(faulted)
                for fault in round_.faults:
                    self._revocation_refuse(fault.key, fault.feed_seq)
                    LOG.warning(
                        "key record quarantined: key=%.128r feed_seq=%d error=%s; the key is "
                        "refused until a verified record replaces it",
                        fault.key, fault.feed_seq, fault.error,
                    )
            if round_.records:
                evicted = self.identity_cache.on_delta(round_.records, round_.manifest.feed_seq)
                if evicted:
                    self._gcra_invalidate(evicted)
                self._revocation_observe(round_.records)
            else:
                identity(round_)

        return apply

    def set_budget_reacquire_hook(self, hook: Callable[[str], None]) -> None:
        """Wire the BUDGET applier's generation-change hook (the registry's `on_generation_change`).

        Called once by `apply_production` after the `QuotaRegistry` is built. The applier was
        registered at construction with a thunk that forwards to this hook, so wiring it here lets
        a generation change mark that org's lease for re-acquisition (Req 7.3) without the appliers
        map ever being incomplete.
        """
        self._budget_reacquire_hook = hook

    def set_gcra_invalidate(self, invalidate: Callable[[Iterable[str]], None]) -> None:
        """Wire the per-principal `GcraCache.invalidate` the KEY applier calls on a delta (Req 3.3).

        Called once by `apply_production` after the `GcraCache` is built. The KEY applier calls this
        with the hashes a KEY delta evicted from the identity cache, so the two caches are
        invalidated together on the same delta.
        """
        self._gcra_invalidate = invalidate

    def set_revocation_observer(self, observe: Callable[[Sequence[SignedRecord]], None]) -> None:
        """Wire the GW29 revocation ledger: every KEY delta's records reach it (design 5.7)."""
        self._revocation_observe = observe

    def set_revocation_ledger(self, ledger: RevocationSink) -> None:
        """Wire the GW29 ledger whole: verified revocations AND KEY records that failed
        verification both cut the key's in-flight streams. What production wires."""
        self._revocation_observe = ledger.observe
        self._revocation_refuse = ledger.refuse

    # --- request path -------------------------------------------------------------------------

    def identity_resolver(self) -> CachingIdentityResolver:
        return CachingIdentityResolver(
            cache=self.identity_cache, fetch=self.fetch_key, key_namer=hashed_bearer_key,
        )

    async def fetch_key(self, key_hash: str) -> Principal | None:
        """One round trip: the KEY manifest, this key's record and its index entry, together.

        The manifest is decoded against what this worker has APPLIED. A store that regressed
        below it -- a failover to a lagging replica -- would otherwise hand back a key this
        worker already saw revoked, and the cache would hold it with nothing left to evict it.
        That is a 503 (unverified), never an answer.

        The record must also BE the one its index entry names (`record_matches_index`). An
        OLDER genuinely signed record of the key -- the pre-revocation ON record put back under
        the revocation's index entry -- verifies on its own, and was admitted, by a running
        worker and a newly started one alike. A key with an index entry and no record is refused
        too; only a key with neither is absent (401).

        A key the delta path quarantined is refused below its faulted position
        (`IdentityCache._fetch_once`); a verified, paired record at or above it lifts it here.
        """
        try:
            manifest_raw, record_raw, score = await self._store.record_with_manifest(
                StateKind.KEY, key_hash,
            )
            cursor, floor = self.sync.cursor(StateKind.KEY), self.sync.floor(StateKind.KEY)
            manifest = decode_manifest(
                self._config.secret,
                StateKind.KEY,
                manifest_raw,
                not_before=max(cursor.version, floor.version),
                feed_floor=max(cursor.feed_seq, floor.feed_seq),
            )
            if record_raw is None:
                if score is not None:
                    raise StoreDataUnavailable("key is indexed but its record is missing")
                return None
            record = decode_record(self._config.secret, StateKind.KEY, record_raw)
            if record.key != key_hash or record.feed_seq > manifest.feed_seq:
                raise StoreDataUnavailable("key record is not covered by its manifest")
            if score is None or not record_matches_index(record, score):
                raise StoreDataUnavailable("key record is not the one its index entry names")
            principal = principal_of(record)
            self.identity_cache.lift_quarantine(key_hash, record.feed_seq)
            return principal
        except StoreDataUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - a config defect must fail closed, and loudly
            LOG.error("identity fetch failed: %s", type(exc).__name__)
            raise StoreDataUnavailable(f"identity fetch failed: {type(exc).__name__}") from exc

    def ready(self) -> tuple[bool, str | None]:
        return self.gates.ready(READY_KINDS)

    def snapshot_source(self) -> ReplicaSnapshot:
        return self.snapshot

    @property
    def namespace(self) -> str:
        return self._config.namespace

    @property
    def store_client(self) -> Any:
        """The one bounded `redis.asyncio` client this worker's readers share.

        Exposed so `apply_production` can build the per-org `BudgetLease` the `QuotaRegistry`
        factory mints over the SAME bounded client (one connection budget per worker), rather than
        opening a second store connection. Read-only; the runtime owns its lifetime (`shutdown`
        closes it).
        """
        return self._client

    @property
    def store_keys(self) -> StoreKeys:
        """The `StoreKeys` layout (namespace + hash tag) the budget lease keys are built from."""
        return self._keys

    @property
    def fresh_ms(self) -> int:
        return self._config.knobs.fresh_ms

    @property
    def refresh_ms(self) -> int:
        return self._config.knobs.refresh_period_ms

    # --- lifetime -----------------------------------------------------------------------------

    async def startup(self) -> None:
        """Start the state task and wait, bounded, for this worker to catch up. Never raises."""
        self._tasks = [
            asyncio.create_task(self._run(), name="amf-state-runtime"),
            asyncio.create_task(self._listen(), name="amf-state-nudges"),
        ]
        try:
            await asyncio.wait_for(
                self._first_cycle.wait(), timeout=self._config.knobs.fresh_ms / 1000,
            )
        except TimeoutError:
            LOG.warning("state not caught up at start-up: %s", self.ready()[1])

    async def shutdown(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []
        with contextlib.suppress(Exception):
            await self._client.aclose()

    async def cycle(self) -> tuple[RoundReport, ...]:
        """One state cycle: the pending cold starts, every kind's drain, a due re-check."""
        reports = [*(await self._cold_start()), *(await self.sync.drain_all())]
        self.gates.observe(reports)
        for report in reports:
            self.metrics.observe_round(report)
        identity = self.identity_cache.stats()
        rejected, quarantined = self.plans.held_counts()
        self.metrics.observe_held_back(StateKind.PLAN, rejected=rejected, quarantined=quarantined)
        self.metrics.observe_held_back(StateKind.KEY, rejected=0, quarantined=identity.quarantined)
        self.metrics.observe_identity(identity)
        self.metrics.observe_killswitch(
            self.switches.view(), stale=self.switches.state() is KillSwitchState.STALE,
        )
        self.metrics.observe_nudge(self._listener.counters)
        self.metrics.observe_freshness(self.stamp)
        now = self._clock()
        if now >= self._recheck_due:  # last, so a re-check never delays the gates above
            self._recheck_due = now + self._config.knobs.fresh_ms / 1000
            await self._recheck_isolated()
        return tuple(reports)

    async def recheck_quarantined(self, *, limit: int = RECHECK_SUBJECTS) -> tuple[int, int]:
        """Re-read up to `limit` quarantined keys and `limit` tenants; restore the repaired ones.

        A quarantine otherwise lifts only on the subject's next verified record or a restart,
        because the cursor has passed its index entry. When the re-hydrator repairs the stored
        bytes AT the same position nothing new is published, so this pass is what notices. A
        subject is restored only when its record now verifies AND is the one the faulted index
        entry names (same key, same position); anything else leaves it quarantined, silently --
        it was logged when it was quarantined. Returns `(keys, tenants)` restored.

        Bounded: `limit` store reads per kind, the caller runs it once per freshness window, and
        the plan compile runs off the loop like an offloaded delta.
        """
        keys = 0
        for key_hash in self.identity_cache.quarantined_for_recheck(limit):
            with contextlib.suppress(StoreDataUnavailable):
                await self.fetch_key(key_hash)  # lifts the quarantine when the record pairs
            if self.identity_cache.quarantined_at(key_hash) is None:
                keys += 1
                LOG.warning("key record quarantine lifted: key=%.128r", key_hash)
        tenants = 0
        for org_id, at in self.plans.quarantined_for_recheck(limit):
            try:
                _manifest, raw, score = await self._store.record_with_manifest(
                    StateKind.PLAN, org_id,
                )
            except StoreDataUnavailable:
                continue  # the store did not answer: the next pass tries again
            if raw is None or score != at:
                continue  # gone, or re-published (the delta path owns a new position)
            if await asyncio.to_thread(self._recheck_plan, org_id, at, raw):
                tenants += 1
        return keys, tenants

    def _recheck_plan(self, org_id: str, at: int, raw: bytes) -> bool:
        """Verify one quarantined tenant's record again; if it pairs, apply it. Off the loop."""
        try:
            record = decode_record(self._config.secret, StateKind.PLAN, raw)
        except Exception:  # noqa: BLE001 - still failing: stays quarantined, already logged
            return False
        if record.key != org_id or not record_matches_index(record, at):
            return False
        self._plan_delta.apply((record,))
        held = self.plans.held_back(org_id)
        if held is not None and held.state == QUARANTINED:
            return False
        LOG.warning(
            "plan record quarantine lifted: org=%.128r feed_seq=%d now=%s",
            org_id, at, "served" if held is None else held.state,
        )
        return True

    async def _recheck_isolated(self) -> None:
        """`recheck_quarantined`, but nothing it raises stops the cycle. Logged once per change."""
        try:
            await self.recheck_quarantined()
        except Exception as exc:  # noqa: BLE001 - a re-check is best effort, the cycle is not
            error = type(exc).__name__
            if self._recheck_failing != error:
                LOG.error("quarantine re-check failed: error=%s", error)
            self._recheck_failing = error
        else:
            self._recheck_failing = None

    async def _cold_start(self) -> list[RoundReport]:
        if not self._cold:
            return []
        # The floor first: a cold start read below it is exactly SP1's window. Isolated as in
        # `drain_all`: nothing the stamp read raises stops the cycle.
        await self.sync.observe_stamp_isolated()
        reports: list[RoundReport] = []
        if StateKind.KS in self._cold:
            reports.append(
                await self.sync.bootstrap_engaged(StateKind.KS, killswitch_adopter(self.switches)),
            )
        if StateKind.KEY in self._cold:
            reports.append(
                await self.sync.fast_forward(StateKind.KEY, self.identity_cache.mark_applied),
            )
        for report in reports:
            if report.ok:
                self._cold.discard(report.kind)
        return reports

    async def _run(self) -> None:
        period_s = self._config.knobs.refresh_period_ms / 1000
        while True:
            delay_s = period_s
            try:
                reports = await self.cycle()
                self.cycles += 1
                if self.ready()[0]:
                    self._first_cycle.set()
                delay_s = self.sync.next_delay_s(reports, period_s=period_s)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the loop is the fleet's freshness
                self.cycle_failures += 1
                # The type only: the text of an exception raised while reading tenant records
                # can quote those records.
                LOG.error("state cycle failed: %s", type(exc).__name__)
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=delay_s)

    async def _listen(self) -> None:
        async def nudged(positions: Mapping[StateKind, int]) -> bool:
            del positions  # a hint only: the cycle reads the attested heads itself
            self._wake.set()
            return True

        await self._listener.run(nudged)


class LifespanHook(Protocol):
    """Something that runs inside the ASGI lifespan (the GW29 pump and ticker)."""

    async def startup(self) -> None: ...

    async def shutdown(self) -> None: ...


class StateServedApp:
    """Runs the state runtime inside the ASGI lifespan, like `DevServedApp` runs the dev pieces.

    Startup completes only after the runtime started (and waited, bounded, to catch up); a
    failure there is logged and leaves the gates unready, so the worker serves 503s rather than
    not starting. Everything that is not `lifespan` passes straight through.
    """

    def __init__(
        self, app: _ASGIApp, runtime: StateRuntime, hooks: Sequence[LifespanHook] = (),
    ) -> None:
        self._app = app
        self._runtime = runtime
        self._hooks = tuple(hooks)

    async def _run_hooks(self, *, start: bool) -> None:
        """Start or stop each hook; one that fails is logged and never blocks the others."""
        for hook in self._hooks:
            try:
                await (hook.startup() if start else hook.shutdown())
            except Exception as exc:  # noqa: BLE001 - a hook failure must not stop the worker
                LOG.error("lifespan hook failed: %s", type(exc).__name__)

    async def __call__(
        self,
        scope: Mapping[str, object],
        receive: Callable[[], Awaitable[Mapping[str, object]]],
        send: Callable[[Mapping[str, object]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "lifespan":
            await self._app(scope, receive, send)
            return

        async def _receive() -> Mapping[str, object]:
            message = await receive()
            kind = message.get("type")
            if kind == "lifespan.startup":
                try:
                    await self._runtime.startup()
                except Exception as exc:  # noqa: BLE001 - fail closed, but start
                    LOG.error("state runtime did not start: %s", type(exc).__name__)
                await self._run_hooks(start=True)
            elif kind == "lifespan.shutdown":
                await self._run_hooks(start=False)
                await self._runtime.shutdown()
            return message

        await self._app(scope, _receive, send)
