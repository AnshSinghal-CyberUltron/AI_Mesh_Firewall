"""Egress wiring: JSON and SSE responses + the OUTPUT-phase audit record."""

from __future__ import annotations

import asyncio
import time

import aiohttp
import orjson

from rvproto.audit import record
from rvproto.domain.decision import Disposition
from rvproto.edge import errors
from rvproto.edge.ctx import Ctx, InputResult, headers_for
from rvproto.edge.http import Send, send_bytes
from rvproto.edge.state import State
from rvproto.egress.jsonout import inspect_json
from rvproto.egress.stream import StreamPipeline

now = time.perf_counter_ns
SSE_HEADERS = ((b"content-type", b"text/event-stream; charset=utf-8"),
               (b"cache-control", b"no-cache"), (b"x-accel-buffering", b"no"))


def _audit_output(st: State, ctx: Ctx, decision: object, verification: str, stages: str) -> bool:
    rec = record.build(decision, request_id=ctx.rid, org_id=ctx.plan.org_id,  # type: ignore[arg-type]
                       key_id=ctx.grant.principal.key_id, verification=verification,
                       stages=stages, wall_ns=now() - ctx.t0)
    return st.audit.emit(rec)


async def respond_json(st: State, ctx: Ctx, res: InputResult, resp: aiohttp.ClientResponse,
                       send: Send, t_dispatch: int) -> None:
    m = st.metrics
    raw = await resp.read()
    t_prov = now()
    m.observe("provider_total_ns", t_prov - t_dispatch)
    try:
        jo = inspect_json(raw, st.inspector(ctx.plan))
    except orjson.JSONDecodeError:
        m.inc("upstream_malformed_json")
        return await errors.send_error(send, errors.upstream("provider returned malformed JSON",
                                                             "provider_protocol_error"), ctx.rid)
    m.observe("t_output_scan_ns", now() - t_prov)
    stages = f"{res.stages},dispatch:E,out:{'E' if st.s.exp_output_scan else 'S'}"
    audited = _audit_output(st, ctx, jo.decision, "json", stages) and res.audited
    stages = f"{stages},audit:{'E' if audited else 'U'}"
    hdr = headers_for(ctx, res.decision.disposition.value, stages)
    hdr.append((b"x-rv-output", jo.decision.disposition.value.encode()))
    if jo.decision.disposition is Disposition.BLOCK:
        return await errors.send_error(send, errors.blocked(jo.decision.deciding_rules, "output_blocked"),
                                       ctx.rid, hdr)
    hdr.append((b"x-request-id", ctx.rid.encode()))
    await send_bytes(send, 200, jo.body, hdr)
    t_end = now()
    m.observe("t_finalize_ns", t_end - t_prov)
    m.observe("t_total_ns", t_end - ctx.t0)


async def respond_stream(st: State, ctx: Ctx, res: InputResult, resp: aiohttp.ClientResponse,
                         send: Send, t_dispatch: int) -> None:
    m = st.metrics
    stages = f"{res.stages},dispatch:E,out:{'E' if st.s.exp_output_scan else 'S'},audit:{'E' if res.audited else 'U'}"
    hdr = [*SSE_HEADERS, (b"x-request-id", ctx.rid.encode()),
           *headers_for(ctx, res.decision.disposition.value, stages)]
    await send({"type": "http.response.start", "status": 200, "headers": hdr})

    async def body(chunk: bytes) -> None:
        await send({"type": "http.response.body", "body": chunk, "more_body": True})

    pipe = StreamPipeline(st.inspector(ctx.plan), m, ceiling=st.stream_ceiling(),
                          inject_hold=st.s.inject_hold, holdback=st.s.holdback)
    st.active_streams += 1
    sst = pipe.stats
    try:
        await pipe.run(resp, body)
        await send({"type": "http.response.body", "body": b"", "more_body": False})
    except asyncio.CancelledError:
        _audit_output(st, ctx, pipe.final_decision(sst), "client_disconnected", stages)
        raise
    finally:
        st.active_streams -= 1
    _audit_output(st, ctx, pipe.final_decision(sst), sst.error or "stream_complete", stages)
    t_fin = now()
    m.observe("t_finalize_ns", t_fin - sst.last_ready_ns)
    if sst.first_ready_ns:
        m.observe("provider_first_bytes_ns", sst.first_ready_ns - t_dispatch)
    m.observe("provider_total_ns", sst.last_ready_ns - t_dispatch)
    m.observe("release_lag_max_ns", sst.release_lag_max_ns)
    m.observe("output_scans_per_request", sst.scans)
    m.observe("t_total_ns", t_fin - ctx.t0)
    if sst.error:
        m.inc("stream_errors")
