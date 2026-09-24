"""Per-worker component graph, built in the ASGI lifespan (never module-level state)."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
import time
from dataclasses import dataclass, field

import redis.asyncio as aioredis

from rvproto.admit.admission import Admission
from rvproto.admit.identity import Identity
from rvproto.admit.killswitch import KillSwitch
from rvproto.admit.overload import LoadGate
from rvproto.admit.quota import Gcra, TokenLease
from rvproto.audit.sink import AuditSink
from rvproto.detect.deterministic import DeterministicDetectors
from rvproto.detect.guard.factory import GuardImpl, build
from rvproto.detect.matcher import Matcher
from rvproto.detect.semantic import SemanticDetector
from rvproto.dispatch.provider import ProviderClient
from rvproto.edge.inspect import PlanInspector, Verifier
from rvproto.plan.snapshot import PlanSnapshot
from rvproto.runtime import contract as rc
from rvproto.runtime.config import Settings, describe, load_settings
from rvproto.runtime.metrics import Registry
from rvproto.runtime.store import StoreError, connect, connect_background


@dataclass
class State:
    s: Settings
    contract: rc.ResourceContract
    logs: tuple[str, ...]
    bounds: rc.Bounds
    metrics: Registry
    redis: aioredis.Redis
    redis_bg: aioredis.Redis
    identity: Identity
    ks: KillSwitch
    admission: Admission
    plans: PlanSnapshot
    matcher: Matcher
    det: DeterministicDetectors
    verifier: Verifier
    guard: GuardImpl
    sem: SemanticDetector
    audit: AuditSink
    provider: ProviderClient
    guard_deadline_ms: float
    gate: LoadGate
    guard_error: str | None = None
    active_streams: int = 0
    tasks: list[asyncio.Task[None]] = field(default_factory=list)
    inspectors: dict[tuple[str, str], PlanInspector] = field(default_factory=dict)

    def inspector(self, plan: object) -> PlanInspector:
        key = (plan.org_id, plan.version)  # type: ignore[attr-defined]
        ins = self.inspectors.get(key)
        if ins is None:
            ins = self.inspectors[key] = PlanInspector(self.matcher, self.det, plan)  # type: ignore[arg-type]
        return ins

    def stream_ceiling(self) -> int:
        return rc.stream_ceiling(self.contract, self.active_streams)


def _log(event: str, **kw: object) -> None:
    sys.stderr.write(json.dumps({"event": event, "t": time.time(), **kw}, default=str) + "\n")
    sys.stderr.flush()


async def build_state() -> State:
    s = load_settings()
    contract, logs = rc.load()
    bounds = rc.derive(contract, s)
    metrics = Registry(s.worker_index)
    redis = connect(s.redis_url, contract, bounds)
    redis_bg = connect_background(s.redis_url, contract, bounds)
    identity = Identity(redis)
    ks = KillSwitch(redis_bg, metrics, refresh_ms=s.ks_refresh_ms, stale_ms=s.ks_stale_ms,
                    on_epoch=identity.on_epoch)
    try:
        await ks.refresh_once()
    except StoreError as exc:
        _log("killswitch_initial_refresh_failed", error=repr(exc))
    lease = TokenLease(redis, metrics, chunk_tokens=bounds.lease_chunk_tokens)
    plans = PlanSnapshot(redis_bg, metrics, reconcile_ms=s.plan_reconcile_ms, stale_ms=s.ks_stale_ms)
    await plans.load_all()
    matcher = Matcher()
    det = DeterministicDetectors(matcher)
    guard = build(s, metrics)
    sem = SemanticDetector(s.guard_tokenizer, guard, window=s.window_tokens,
                           overlap=s.window_overlap, max_windows=s.max_windows,
                           model_hash=guard.model_hash)
    audit = AuditSink(redis_bg, metrics, stream_maxlen=s.audit_stream_maxlen)
    audit_rate = await audit.calibrate()
    bounds = rc.derive(contract, s, audit_rate=audit_rate)
    assert bounds.audit_queue is not None
    audit.configure(bounds.audit_queue)
    provider = ProviderClient(s, bounds, metrics)
    await provider.start()
    deadline = s.guard_deadline_ms if s.guard_deadline_ms is not None else contract.target_p99_ms
    gate = LoadGate(metrics, inflight_cap=contract.connection_budget())
    st = State(s, contract, logs, bounds, metrics, redis, redis_bg, identity, ks,
               Admission(identity, ks, Gcra(), lease), plans, matcher, det,
               Verifier(matcher, s.decode_budget), guard, sem, audit, provider, deadline, gate)
    _log("startup", worker=s.worker_index, settings=describe(s), matcher=matcher.engine,
         contract=rc.describe(contract, logs, bounds), audit_drain_rate=audit_rate,
         guard_deadline_ms=deadline)
    return st


async def start_guard(st: State) -> None:
    """Warm the guard (TRT engine build can take minutes), then derive guard bounds."""
    try:
        await st.guard.start()
    except Exception as exc:  # stays not-ready; requests get UNAVAILABLE
        st.guard_error = repr(exc)
        _log("guard_start_failed", worker=st.s.worker_index, error=repr(exc))
        return
    hint = st.guard.capacity_hint()
    guard_q = None
    if hint is not None:
        st.contract = rc.with_guard(st.contract, hint.tokens_per_second)
        guard_q = rc.derive(st.contract, st.s).guard_queue_tokens
        st.bounds = dataclasses.replace(st.bounds, guard_queue_tokens=guard_q)
        windows_per_s = hint.tokens_per_second / st.s.window_tokens
        input_cap = st.contract.queue_depth(windows_per_s)
        st.gate.configure_guard(input_cap=input_cap, guard_cap_tokens=guard_q or 0,
                                tokens_per_s=hint.tokens_per_second)
        st.metrics.set("guard_tokens_per_s", hint.tokens_per_second)
    _log("guard_ready", worker=st.s.worker_index, backend=st.guard.name, detail=st.guard.detail,
         guard_queue_tokens=guard_q, input_queue_cap=st.gate.input_cap)
