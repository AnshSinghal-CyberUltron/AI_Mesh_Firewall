"""GW31 shared route plumbing: body, identity, plan, settings gate, admission, rendering, audit.

Every RAG handler runs :func:`serve`. Order inside: bounded body, JSON, identity, plan, effective
settings, ``rag_enabled`` gate, project re-check, admission, budget, then the handler's own work.
Auth, admission and budget all precede the first adapter call. Any exception that is not a
:class:`RagRefused` becomes ``rag_internal_error`` and only its CLASS NAME is logged, because an
exception message can carry chunk text.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from gateway_v2.admit.grant import Admitted, ShedVerdict
from gateway_v2.admit.identity import valid_project
from gateway_v2.dispatch.rag_namespace import NamespaceRefused, build_namespace, wire_collection
from gateway_v2.domain import posture
from gateway_v2.domain.plan import ExecutionPlan, PlanUnavailable, PlanUnknownTenant, Surface
from gateway_v2.domain.rag import Namespace, StageNote
from gateway_v2.edge import errors
from gateway_v2.edge.asgi_sse import ASGIReceive, ASGIScope, ASGISend, read_body_bounded
from gateway_v2.edge.audit_emit import emit_chat_audit
from gateway_v2.edge.identity_resolver import TenantIdentity, hashed_bearer_key, identity_of
from gateway_v2.edge.rag_stages import RagRefused
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.request_ids import RequestIdUnavailable
from gateway_v2.edge.routes import ChatRoute, emit_response
from gateway_v2.plan import lookup_plan
from gateway_v2.plan.rag_settings import EffectiveRag, effective_settings

__all__ = (
    "RagCtx",
    "Reply",
    "Session",
    "adapter_call",
    "adapter_for",
    "json_object",
    "namespace_for",
    "serve",
    "store_of",
    "wire_of",
)

import asyncio
from collections.abc import Coroutine

from gateway_v2.dispatch.vector_adapter import AdapterResult, Vector_Adapter
from gateway_v2.edge.rag_deps import RagDeps

LOG = logging.getLogger("amf.rag")
_OK = 200


class RagCtx:
    """What one request learned, for the audit record and the trace. Built per request."""

    __slots__ = ("detail", "holds", "notes", "scope", "session")

    def __init__(self, scope: ASGIScope) -> None:
        self.scope = scope
        self.holds = RequestHolds()
        self.notes: list[StageNote] = []
        self.detail: dict[str, str] = {}
        self.session: Session | None = None


@dataclass(frozen=True, slots=True)
class Session:
    identity: TenantIdentity
    plan: ExecutionPlan
    eff: EffectiveRag
    project: str

    @property
    def org_id(self) -> str:
        return self.identity.org_id


@dataclass(frozen=True, slots=True)
class Reply:
    status: int
    body: Mapping[str, object]
    headers: tuple[tuple[str, str], ...] = ()


Work = Callable[[RagCtx, Session, Mapping[str, object]], Awaitable[Reply]]


def json_object(raw: bytes) -> Mapping[str, object]:
    try:
        parsed: object = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RagRefused(posture.RAG_INVALID_REQUEST) from exc
    if not isinstance(parsed, dict):
        raise RagRefused(posture.RAG_INVALID_REQUEST)
    return parsed


def namespace_for(chat: ChatRoute, session: Session, collection: object) -> Namespace:
    """The only way a handler gets a namespace. Refuses before any adapter call."""
    bounds = chat.contract.rag_bounds()
    try:
        return build_namespace(
            session.org_id,
            session.project,
            collection,  # type: ignore[arg-type]
            session.eff.vector_db_isolation,
            org_max=bounds.org_max_length,
            project_max=bounds.project_max_length,
            collection_max=bounds.collection_max_length,
            wire_max=bounds.wire_max_length,
        )
    except NamespaceRefused as exc:
        raise RagRefused(posture.RAG_NAMESPACE_REFUSED) from exc


def wire_of(ns: Namespace) -> str:
    return wire_collection(ns)


async def _session(chat: ChatRoute, ctx: RagCtx, surface: Surface) -> Session:
    outcome = await identity_of(chat.identity, ctx.scope)
    identity = outcome.identity
    if identity is None:
        raise RagRefused(outcome.refusal)
    ctx.holds.note_tenant(identity.org_id)
    state = lookup_plan(chat.snapshot_source(), identity.org_id, surface)
    if isinstance(state, (PlanUnavailable, PlanUnknownTenant)):
        raise RagRefused(posture.PLAN_UNAVAILABLE)
    eff = effective_settings(state, chat.contract)
    if not eff.rag_enabled:
        raise RagRefused(posture.RAG_DISABLED)
    project = identity.principal.project
    if not valid_project(project, chat.contract.rag_bounds().project_max_length):
        raise RagRefused(posture.PROJECT_INVALID)
    return Session(identity, state, eff, project)


async def _admit_and_charge(chat: ChatRoute, ctx: RagCtx, session: Session, chars: int) -> None:
    request_id = ctx.holds.request_id
    verdict = await chat.admission.admit(
        owner_id=session.org_id, request_id=request_id, enqueued_at=chat.clock()
    )
    if isinstance(verdict, ShedVerdict):
        raise RagRefused(verdict.code)
    ctx.holds.admitted(request_id)
    cost = chat.contract.estimate_request_tokens(chars, 0, 1)
    budget = await chat.quota.evaluate(
        cost,
        request_id=request_id,
        owner=session.org_id,
        principal=session.identity.principal,
        key_hash=hashed_bearer_key(ctx.scope) or "",
    )
    if not isinstance(budget, Admitted):
        raise RagRefused(budget.code)
    ctx.holds.spent(cost, request_id)


async def serve(
    chat: ChatRoute,
    scope: ASGIScope,
    receive: ASGIReceive,
    send: ASGISend,
    *,
    route: str,
    surface: Surface,
    work: Work,
    needs_body: bool = True,
) -> None:
    ctx = RagCtx(scope)
    refused: str | None = None
    reply: Reply | None = None
    try:
        raw = b""
        if needs_body:
            body = await read_body_bounded(receive, chat.contract.rag_bounds().body_bytes)
            if body is None:
                raise RagRefused(posture.RAG_REQUEST_TOO_LARGE)
            raw = body
        parsed = json_object(raw) if needs_body else {}
        ctx.session = session = await _session(chat, ctx, surface)
        try:
            ctx.holds.begin_request(session.org_id)
        except RequestIdUnavailable as exc:
            # Fail closed (req 2.8): no id exists, so none is rendered; nothing is admitted,
            # spent or dispatched.
            ctx.holds.note_failure(posture.REQUEST_ID_UNAVAILABLE)
            raise RagRefused(posture.REQUEST_ID_UNAVAILABLE) from exc
        await _admit_and_charge(chat, ctx, session, len(raw))
        reply = await work(ctx, session, parsed)
    except RagRefused as stop:
        refused = stop.code
    except Exception as exc:  # noqa: BLE001 - class name only: a message can carry chunk text
        LOG.error("rag handler failed: %s", type(exc).__name__)
        refused = posture.RAG_INTERNAL_ERROR
    try:
        await _respond(send, ctx, reply, refused)
    finally:
        emit_chat_audit(
            chat.audit,
            ctx.holds,
            refused,
            route=route,
            detail={**ctx.detail, "stages": ",".join(n.stage for n in ctx.notes)},
            observer=chat.observer,
        )
        ctx.holds.release(chat.admission, chat.quota)


async def _respond(send: ASGISend, ctx: RagCtx, reply: Reply | None, refused: str | None) -> None:
    request_id = ctx.holds.request_id or None
    if refused is not None or reply is None:
        response = errors.render_http(refused or posture.RAG_INTERNAL_ERROR, request_id=request_id)
        if refused == posture.RAG_INGEST_OVERLOAD:
            retry = str(max(1, round(posture.MIN_RETRY_AFTER_S)))
            response = errors.HTTPResponse(
                status=response.status,
                body=response.body,
                headers=(*response.headers, ("retry-after", retry)),
            )
    else:
        payload = json.dumps(
            {**reply.body, **({"request_id": request_id} if request_id else {})},
            separators=(",", ":"),
        ).encode()
        headers = (("content-type", "application/json"), *reply.headers)
        if request_id:
            headers = (*headers, ("x-request-id", request_id))
        response = errors.HTTPResponse(status=reply.status, body=payload, headers=headers)
    await emit_response(send, response)


def store_of(deps: RagDeps, session: Session) -> str:
    store_id = deps.store_id_of(session.org_id)
    if store_id is None:
        raise RagRefused(posture.STORE_NOT_CONFIGURED)
    return store_id


def adapter_for(deps: RagDeps, store_id: str) -> Vector_Adapter:
    found = deps.registry.select(store_id)
    if isinstance(found, AdapterResult):
        raise RagRefused(found.code)
    return found


async def adapter_call(
    call: Coroutine[object, object, AdapterResult], timeout_s: float
) -> AdapterResult:
    """One adapter operation, bounded. A raise or timeout is ``provider_unreachable``."""
    try:
        return await asyncio.wait_for(call, timeout_s)
    except Exception:  # noqa: BLE001 - an adapter must not raise; fail closed if it does
        return AdapterResult(posture.PROVIDER_UNREACHABLE)
