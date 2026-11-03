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
  else is logged with its class, counted, and the next cycle runs.
* **A worker is not ready until it has caught up.** `startup()` waits, bounded by the freshness
  window, for its first good cycle, and uvicorn routes nothing to a worker before its lifespan
  startup completes -- so a cold worker does not flap `/readyz` while its siblings serve.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from gateway_v2.admit.identity import IdentityCache, identity_applier, principal_of
from gateway_v2.admit.killswitch import (
    KillSwitchSnapshot,
    KillSwitchState,
    killswitch_adopter,
    killswitch_applier,
)
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.locks import FRESH_MS, PG_GRACE_MS
from gateway_v2.domain.state import StateKind, StoreDataUnavailable
from gateway_v2.domain.state_knobs import (
    DEFAULT_REFRESH_PERIOD_MS,
    DEFAULT_REHYDRATE_PERIOD_MS,
    StateKnobs,
)
from gateway_v2.edge.identity_resolver import CachingIdentityResolver, hashed_bearer_key
from gateway_v2.plan.delta import plan_applier
from gateway_v2.plan.snapshot import ReplicaSnapshot
from gateway_v2.plan.store import PlanStore
from gateway_v2.runtime.state_feed import FeedReader
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

#: The kinds a worker must have caught up before it is ready. BUDGET has no gateway reader yet
#: (S4), so it is not one of them.
READY_KINDS = (StateKind.PLAN, StateKind.KS, StateKind.KEY)


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

    def __init__(self, config: StateRuntimeConfig, *, client: Any = None) -> None:
        """`client` is injectable for tests; production builds a bounded `redis.asyncio` one."""
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
        )
        # PLAN first: a key whose org was just onboarded resolves straight from the store, and
        # its plan must already be served by then (the projector writes plans before keys).
        self.sync = StateSynchroniser(
            FeedReader(self._store, config.secret),
            {
                StateKind.PLAN: plan_applier(self.plans, self.snapshot),
                StateKind.KS: killswitch_applier(self.switches),
                StateKind.KEY: identity_applier(self.identity_cache),
            },
            stamp=self.stamp,
            inline_kinds=frozenset({StateKind.KEY}),
        )
        self.metrics = StateMetricsRecorder()
        self._listener = NudgeListener(client, self._keys.updates)
        self._wake = asyncio.Event()
        self._first_cycle = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._cold: set[StateKind] = {StateKind.KS, StateKind.KEY}
        self.cycles = 0
        self.cycle_failures = 0

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
        with contextlib.suppress(StoreDataUnavailable):
            # The floor first: a cold start read below it is exactly SP1's window.
            await self.sync.observe_stamp()
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
                LOG.error("state cycle failed: %s: %s", type(exc).__name__, exc)
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=delay_s)

    async def _listen(self) -> None:
        async def nudged(positions: Mapping[StateKind, int]) -> bool:
            del positions  # a hint only: the cycle reads the attested heads itself
            self._wake.set()
            return True

        await self._listener.run(nudged)


class StateServedApp:
    """Runs the state runtime inside the ASGI lifespan, like `DevServedApp` runs the dev pieces.

    Startup completes only after the runtime started (and waited, bounded, to catch up); a
    failure there is logged and leaves the gates unready, so the worker serves 503s rather than
    not starting. Everything that is not `lifespan` passes straight through.
    """

    def __init__(self, app: _ASGIApp, runtime: StateRuntime) -> None:
        self._app = app
        self._runtime = runtime

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
                    LOG.error("state runtime did not start: %s: %s", type(exc).__name__, exc)
            elif kind == "lifespan.shutdown":
                await self._runtime.shutdown()
            return message

        await self._app(scope, _receive, send)
