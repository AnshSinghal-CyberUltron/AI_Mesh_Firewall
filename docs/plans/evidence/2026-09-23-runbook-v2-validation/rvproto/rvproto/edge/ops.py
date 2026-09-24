"""Operational surface: /metrics, /healthz, /readyz, /v1/models, admin fault hooks."""

from __future__ import annotations

import json
import time
from pathlib import Path

import orjson

from rvproto.admit import posture
from rvproto.domain.request import ErrorSpec
from rvproto.edge import errors
from rvproto.edge.http import Receive, Scope, Send, bearer, header_map, request_id, send_bytes
from rvproto.edge.state import State
from rvproto.runtime import contract as rc
from rvproto.runtime.metrics import merge, prometheus, summarize
from rvproto.runtime.store import StoreError

TEXT = (b"content-type", b"text/plain; version=0.0.4")


def _gauges(st: State) -> None:
    """Live gauges, computed at scrape/dump time (GW05/GW06/GW19 hooks)."""
    g = st.metrics.set
    g("active_streams", st.active_streams)
    g("audit_completeness_ratio", st.audit.completeness())
    g("audit_produced", st.audit.produced)
    g("audit_dropped", st.audit.dropped)
    depth, age = st.audit.queue_stats()
    g("audit_queue_depth", depth)
    g("audit_queue_oldest_age_seconds", age)
    g("plan_snapshot_age_seconds", st.plans.age_s())
    g("killswitch_snapshot_age_seconds", st.ks.age_s())
    for k in [k for k in st.metrics.gauge if k.startswith(("plan_version_info", "killswitch_engaged"))]:
        del st.metrics.gauge[k]
    for org, version in st.plans.versions().items():
        g(f'plan_version_info{{org="{org}",version="{version}"}}', 1)
    for org in st.ks.orgs:
        g(f'killswitch_engaged{{scope="org",key="{org}"}}', 1)
    for model in st.ks.models:
        g(f'killswitch_engaged{{scope="model",key="{model}"}}', 1)
    for name, val in st.gate.gauges().items():
        g(name, val)
    queue_stats = getattr(st.guard, "queue_stats", None)  # owner topology: this worker's outstanding work
    if queue_stats is not None:
        items, windows, gage = queue_stats()
        g("guard_queue_items", items)
        g("guard_queue_windows", windows)
        g("guard_queue_oldest_age_seconds", gage)
    for org, held in st.admission.lease.held().items():
        g(f'lease_held_tokens{{org="{org}"}}', held)


async def readiness(st: State) -> tuple[bool, dict[str, object]]:
    g = await st.guard.readiness()
    ks = st.ks.state()
    ready = g.ready and st.plans.fresh() and ks != "stale"
    return ready, {"ready": ready, "guard": {"ready": g.ready, "backend": g.backend,
                                              "model_hash": g.model_hash, "detail": g.detail,
                                              "error": st.guard_error},
                   "plans_loaded": st.plans.loaded, "plans_fresh": st.plans.fresh(),
                   "killswitch": ks, "worker": st.s.worker_index}


async def handle(st: State, scope: Scope, receive: Receive, send: Send) -> bool:
    """Returns False when the path is not an ops route."""
    path, method = scope["path"], scope["method"]
    if path == "/healthz":
        await send_bytes(send, 200, b'{"ok":true}', [])
    elif path == "/readyz":
        ok, body = await readiness(st)
        await send_bytes(send, 200 if ok else 503, orjson.dumps(body), [])
    elif path == "/metrics":
        _gauges(st)
        await send_bytes(send, 200, prometheus(st.metrics.export()).encode(), [], TEXT)
    elif path == "/metrics.json":
        _gauges(st)
        await send_bytes(send, 200, orjson.dumps(st.metrics.export()), [])
    elif path == "/metrics/all":
        await send_bytes(send, 200, orjson.dumps(_all_workers(st)), [])
    elif path == "/_rv/contract":
        await send_bytes(send, 200, json.dumps(rc.describe(st.contract, st.logs, st.bounds),
                                               default=str).encode(), [])
    elif path == "/v1/models" and method == "GET":
        await _models(st, scope, send)
    elif path.startswith("/_rv/guard/") and method == "POST" and st.s.admin_hooks:
        await _guard_hook(st, path.rsplit("/", 1)[-1], send)
    else:
        return False
    return True


def _all_workers(st: State) -> dict[str, object]:
    _gauges(st)
    exports = [st.metrics.export()]
    if st.s.metrics_dir:
        st.metrics.dump(st.s.metrics_dir)
        exports = [json.loads(p.read_text()) for p in sorted(Path(st.s.metrics_dir).glob("worker-*.json"))]
    merged = merge(exports)
    merged["summary"] = summarize(merged["hist"])  # type: ignore[arg-type]
    if st.s.metrics_dir:  # guard owners (owner topology) are reported beside, never merged into, workers
        owners = [json.loads(p.read_text()) for p in sorted(Path(st.s.metrics_dir).glob("owner-*.json"))]
        merged["owners"] = [{"owner": o["worker"], "pid": o["pid"], "exported_at": o["exported_at"],
                             "count": o["count"], "gauge": o["gauge"], "summary": summarize(o["hist"])}
                            for o in owners]
    merged["generated_at"] = time.time()
    return merged


async def _models(st: State, scope: Scope, send: Send) -> None:
    h = header_map(scope)
    rid = request_id(h)
    key = bearer(h)
    if not key:
        return await errors.send_error(send, posture.MISSING_KEY, rid)
    key_hash, principal = st.identity.cached(key)
    if principal is None:
        try:
            principal = await st.identity.fetch(key_hash)
        except StoreError:
            return await errors.send_error(send, posture.STORE_UNAVAILABLE, rid)
    if principal is None:
        return await errors.send_error(send, posture.INVALID_KEY, rid)
    data = [{"id": m, "object": "model", "created": 0, "owned_by": "rvproto"} for m in st.s.models]
    await send_bytes(send, 200, orjson.dumps({"object": "list", "data": data}),
                     [(b"x-request-id", rid.encode())])


async def _guard_hook(st: State, action: str, send: Send) -> None:
    guard = st.guard
    if action == "kill":
        guard.kill("admin hook")
    elif action == "revive":
        guard.revive()
    else:
        spec = ErrorSpec(404, "invalid_request_error", "unknown_hook", action)
        return await errors.send_error(send, spec, "admin")
    r = await guard.readiness()
    await send_bytes(send, 200, orjson.dumps({"action": action, "ready": r.ready, "detail": r.detail,
                                              "worker": st.s.worker_index}), [])
