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
* **A worker is not ready until it has caught up.** `startup()` waits, bounded by the freshness
  window, for its first good cycle, and uvicorn routes nothing to a worker before its lifespan
  startup completes -- so a cold worker does not flap `/readyz` while its siblings serve.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
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
from gateway_v2.plan.delta import plan_applier
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore
from gateway_v2.runtime.state_feed import FeedReader, FeedRound
from gateway_v2.runtime.state_gate import KindGates
from gateway_v2.runtime.state_metrics import StateMetricsRecorder
from gateway_v2.runtime.state_nudge import NudgeListener
from gateway_v2.runtime.state_sig import decode_manifest, decode_record
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
        # PLAN first: a key whose org was just onboarded resolves straight from the store, and
        # its plan must already be served by then (the projector writes plans before keys).
        self.sync = StateSynchroniser(
            FeedReader(self._store, config.secret),
            {
                StateKind.PLAN: plan_applier(self.plans, self.snapshot),
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
        self.cycles = 0
        self.cycle_failures = 0

    # --- applier composition ------------------------------------------------------------------

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
                # A KEY record that failed verification: nothing cached for that key survives,
                # in either cache, so its next request re-reads it and is refused while it fails.
                faulted = tuple(fault.key for fault in round_.faults)
                self.identity_cache.evict(faulted, round_.manifest.feed_seq)
                self._gcra_invalidate(faulted)
                for fault in round_.faults:
                    LOG.warning(
                        "key record quarantined: key=%r feed_seq=%d error=%s; the key is "
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

    # --- request path -------------------------------------------------------------------------

    def identity_resolver(self) -> CachingIdentityResolver:
        return CachingIdentityResolver(
            cache=self.identity_cache, fetch=self.fetch_key, key_namer=hashed_bearer_key,
        )

    async def fetch_key(self, key_hash: str) -> Principal | None:
        """One round trip: the KEY manifest and this key's record, read together.

        The manifest is decoded against what this worker has APPLIED. A store that regressed
        below it -- a failover to a lagging replica -- would otherwise hand back a key this
        worker already saw revoked, and the cache would hold it with nothing left to evict it.
        That is a 503 (unverified), never an answer.
        """
        try:
            manifest_raw, record_raw = await self._store.record_with_manifest(
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
                return None
            record = decode_record(self._config.secret, StateKind.KEY, record_raw)
            if record.key != key_hash or record.feed_seq > manifest.feed_seq:
                raise StoreDataUnavailable("key record is not covered by its manifest")
            return principal_of(record)
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
        """One state cycle: the pending cold starts, then every kind's drain."""
        reports = [*(await self._cold_start()), *(await self.sync.drain_all())]
        self.gates.observe(reports)
        for report in reports:
            self.metrics.observe_round(report)
        self.metrics.observe_identity(self.identity_cache.stats())
        self.metrics.observe_killswitch(
            self.switches.view(), stale=self.switches.state() is KillSwitchState.STALE,
        )
        self.metrics.observe_nudge(self._listener.counters)
        self.metrics.observe_freshness(self.stamp)
        return tuple(reports)

    async def _cold_start(self) -> list[RoundReport]:
        if not self._cold:
            return []
        try:
            # The floor first: a cold start read below it is exactly SP1's window.
            await self.sync.observe_stamp()
        except StoreDataUnavailable:
            pass
        except Exception as exc:  # noqa: BLE001 - as in `drain_all`: never stops the cycle
            LOG.error("freshness stamp read failed: error=%s", type(exc).__name__)
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
