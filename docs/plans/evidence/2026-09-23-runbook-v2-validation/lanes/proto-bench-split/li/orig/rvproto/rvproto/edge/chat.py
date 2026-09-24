"""/v1/chat/completions: the per-request lifecycle (runbook §10.4), composition root.

edge(t0) -> admit -> plan pin -> detect(input: canonicalize once; submit PG2 windows
first; deterministic pass meanwhile; await guard) -> resolve -> [BLOCK: 403, zero
provider calls] -> dispatch(apply + verify transformations; ONE authorized call)
-> egress(JSON | SSE) -> audit (one record per phase, enqueued, never awaited).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import aiohttp

from rvproto.admit import posture
from rvproto.admit.overload import Shed
from rvproto.audit import record
from rvproto.detect.canon import DecodeBudgetExceeded, canonicalize
from rvproto.detect.guard.batcher import padded_cost
from rvproto.detect.semantic import InputTooLong
from rvproto.dispatch.transform import apply_and_verify
from rvproto.domain.findings import SEMANTIC_DETECTOR, Finding, FindingStatus, skipped
from rvproto.domain.plan import Phase, PlanUnavailable, PlanUnknownTenant
from rvproto.domain.request import ChatRequest, ErrorSpec
from rvproto.edge import errors
from rvproto.edge.http import (
    BodyTooLarge,
    Receive,
    Scope,
    Send,
    bearer,
    header_map,
    read_body,
    request_id,
    wait_disconnect,
)
from rvproto.edge.respond import respond_json, respond_stream
from rvproto.edge.ctx import Ctx, InputResult, blocked_stages, headers_for
from rvproto.edge.state import State
from rvproto.edge.wire import parse_chat
from rvproto.resolve.resolver import authorize, resolve

now = time.perf_counter_ns


def _sem_code(f: Finding) -> str:
    return {"executed": "E", "skipped": "S", "unavailable": "U"}[f.status.value]


class ChatHandler:
    def __init__(self, st: State) -> None:
        self.st = st

    async def handle(self, scope: Scope, receive: Receive, send: Send, t0: int) -> None:
        st, m = self.st, self.st.metrics
        h = header_map(scope)
        rid = request_id(h)
        try:
            body = await read_body(receive, st.s.max_body_bytes)
        except BodyTooLarge:
            return await errors.send_error(send, errors.too_large("Request body too large.", "body_too_large"), rid)
        chat = parse_chat(body)
        if isinstance(chat, ErrorSpec):
            return await errors.send_error(send, chat, rid)
        ticket = st.gate.enter()
        if isinstance(ticket, Shed):
            return await errors.send_error(send, posture.overloaded(ticket.reason, ticket.retry_after_s), rid)
        try:
            await self._admitted(scope, receive, send, t0, h, rid, body, chat, ticket)
        finally:
            st.gate.leave(ticket)

    async def _admitted(self, scope: Scope, receive: Receive, send: Send, t0: int, h: dict[bytes, bytes],
                        rid: str, body: bytes, chat: ChatRequest, ticket: int) -> None:
        st, m = self.st, self.st.metrics
        out_tokens = chat.max_tokens if chat.max_tokens is not None else st.s.default_output_tokens
        grant = await st.admission.admit(bearer(h), len(body) // 4 + out_tokens, chat.model)
        t_admit = now()
        m.observe("t_admit_ns", t_admit - t0)
        if isinstance(grant, ErrorSpec):
            m.inc(f"rejected_{grant.status}")
            return await errors.send_error(send, grant, rid)
        m.observe("round_trips_per_request", grant.round_trips)
        m.inc("shared_state_round_trips", grant.round_trips)
        m.inc(f'requests_by_round_trips{{n="{min(grant.round_trips, 2)}"}}')
        plan = st.plans.get(grant.principal.org_id)
        if isinstance(plan, PlanUnavailable | PlanUnknownTenant):
            spec = posture.PLAN_UNAVAILABLE if isinstance(plan, PlanUnavailable) else posture.PLAN_UNKNOWN
            return await errors.send_error(send, spec, rid)
        m.observe("t_plan_ns", now() - t_admit)
        prefix = st.s.passthrough_prefix.encode()
        passthrough = tuple((k.decode(), v.decode()) for k, v in scope["headers"]
                            if k.lower().startswith(prefix))
        ctx = Ctx(rid, t0, chat, grant, plan, passthrough, ticket)
        work = asyncio.ensure_future(self._run(ctx, send))
        watch = asyncio.ensure_future(wait_disconnect(receive))
        done, _ = await asyncio.wait((work, watch), return_when=asyncio.FIRST_COMPLETED)
        if work in done:
            watch.cancel()
            return work.result()
        m.inc("client_disconnects")
        t_cancel = now()
        work.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await work
        m.observe("cancel_propagation_ns", now() - t_cancel)

    async def _run(self, ctx: Ctx, send: Send) -> None:
        st, m = self.st, self.st.metrics
        res = await self._input(ctx)
        if isinstance(res, ErrorSpec):
            return await errors.send_error(send, res, ctx.rid)
        if res.auth is None:
            m.inc("disposition_BLOCK")
            m.observe("t_input_ns", now() - ctx.t0)
            spec = errors.blocked(res.decision.deciding_rules,
                                  "redaction_unverified" if res.verification.startswith("FAILED") else "blocked_by_policy")
            hdr = headers_for(ctx, "BLOCK", blocked_stages(res.stages, res.audited))
            return await errors.send_error(send, spec, ctx.rid, hdr)
        m.inc(f"disposition_{res.decision.disposition.value}")
        if st.s.inject_predispatch_ms > 0:
            await asyncio.sleep(st.s.inject_predispatch_ms / 1000.0)
        t_dispatch = now()
        m.observe("t_input_ns", t_dispatch - ctx.t0)
        try:
            resp = await st.provider.send(res.auth, res.body, ctx.rid, ctx.passthrough)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            m.inc("upstream_connect_errors")
            return await errors.send_error(send, errors.upstream(f"provider unreachable: {type(exc).__name__}"), ctx.rid)
        m.observe("dispatch_headers_ns", now() - t_dispatch)
        try:
            if resp.status != 200:
                m.inc("upstream_status_errors")
                await resp.read()
                return await errors.send_error(send, errors.upstream(f"provider status {resp.status}"), ctx.rid)
            if ctx.chat.stream:
                await respond_stream(st, ctx, res, resp, send, t_dispatch)
            else:
                await respond_json(st, ctx, res, resp, send, t_dispatch)
        finally:
            resp.release()

    async def _input(self, ctx: Ctx) -> InputResult | ErrorSpec:
        shed = self.st.gate.enter_input(ctx.ticket)
        if shed is not None:
            return posture.overloaded(shed.reason, shed.retry_after_s)
        try:
            return await self._detect_and_resolve(ctx)
        finally:
            self.st.gate.leave_input(ctx.ticket)

    async def _detect_and_resolve(self, ctx: Ctx) -> InputResult | ErrorSpec:
        st, m, plan = self.st, self.st.metrics, ctx.plan
        t = now()
        try:
            canon = [canonicalize(seg.text, st.s.decode_budget) for seg in ctx.chat.segments]
        except DecodeBudgetExceeded as exc:
            return errors.bad_request(str(exc), "messages", "decode_budget_exceeded")
        t_canon = now()
        m.observe("t_canon_ns", t_canon - t)
        if SEMANTIC_DETECTOR in plan.required_input:
            try:
                windows = st.sem.windows([c.text for c in canon])
            except InputTooLong as exc:
                m.inc("rejected_input_too_long")
                return errors.too_large(str(exc), "context_length_exceeded")
            m.observe("t_tokenize_ns", now() - t_canon)
            m.observe("guard_windows_per_request", len(windows.rows))
            cost = padded_cost(windows.rows, st.s.guard_seq_buckets)
            shed = st.gate.take_guard(cost)
            if shed is not None:  # overload: shed before any guard work, never skip the detector
                return posture.overloaded(shed.reason, shed.retry_after_s)
            try:
                fut = st.sem.submit(windows, st.guard_deadline_ms)
                t_det0 = now()
                det = st.det.scan(canon, plan.required_input)
                m.observe("t_det_scan_ns", now() - t_det0)
                sem = await self._await_guard(fut, windows)
            finally:
                st.gate.give_guard(cost)
            if isinstance(sem, Shed):  # the owner's queue is full: shed, never skip the detector
                return posture.overloaded(sem.reason, sem.retry_after_s)
        else:
            t_det0 = now()
            det = st.det.scan(canon, plan.required_input)
            m.observe("t_det_scan_ns", now() - t_det0)
            sem = await self._await_guard(None, None)
        assert not isinstance(sem, Shed)
        findings = (*det, sem)
        t_res = now()
        decision = resolve(findings, plan, Phase.INPUT)
        auth = authorize(decision, ctx.rid)
        m.observe("t_resolve_ns", now() - t_res)
        body, verification = ctx.chat.body, "none"
        if auth is not None and decision.transformations:
            t_tr = now()
            tr = apply_and_verify(ctx.chat, decision.transformations, st.verifier)
            m.observe("t_transform_ns", now() - t_tr)
            if not tr.verified:  # unmaskable path: never dispatched unverified
                m.inc("redaction_verification_failed")
                auth, verification = None, f"FAILED {tr.detail}"
            else:
                body, verification = tr.body, f"verified {tr.detail}"
        stages = f"canon:E,det:E,sem:{_sem_code(sem)},resolve:E"
        rec = record.build(decision, request_id=ctx.rid, org_id=plan.org_id,
                           key_id=ctx.grant.principal.key_id, verification=verification,
                           stages=stages, wall_ns=now() - ctx.t0)
        audited = st.audit.emit(rec)
        return InputResult(decision, auth, body, verification, stages, audited)

    async def _await_guard(self, fut: asyncio.Future | None, windows: object) -> Finding | Shed:  # type: ignore[type-arg]
        st, m = self.st, self.st.metrics
        if fut is None:
            return skipped(SEMANTIC_DETECTOR, st.sem.version)
        t = now()
        try:
            result = await fut
        except asyncio.CancelledError:
            fut.cancel()
            raise
        m.observe("t_guard_wait_ns", now() - t)
        if result.retry_after_s is not None:
            return st.gate.downstream_shed("guard_owner_queue", result.retry_after_s)
        f = st.sem.finding(windows, result)  # type: ignore[arg-type]
        if f.status is FindingStatus.UNAVAILABLE:
            m.inc("guard_unavailable_findings")
        return f
