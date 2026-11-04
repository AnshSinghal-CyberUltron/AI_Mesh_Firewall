"""``POST /v1/rag/ingest`` and ``GET /v1/rag/ingest/{job_id}`` (GW31).

Accept path: validate, scan (stage ``None``: ingest is not one of the four query stages), embed
check, record the job in the injected job store, enqueue, then 202. No 202 without a successful
``put``. "Durably queued" means recorded in that store; an in-memory store loses accepted jobs on
process exit and ``GET`` then answers ``rag_job_not_found``. A lost job never reports ``stored``.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from gateway_v2.dispatch.rag_ingest import STATE_ACCEPTED, IngestDoc, IngestJob
from gateway_v2.domain import posture
from gateway_v2.domain.decision import Disposition
from gateway_v2.domain.plan import Phase, Surface
from gateway_v2.domain.rag import TrustLevel
from gateway_v2.edge.asgi_sse import ASGIReceive, ASGIScope, ASGISend
from gateway_v2.edge.rag_common import (
    RagCtx,
    Reply,
    Session,
    namespace_for,
    serve,
    store_of,
    wire_of,
)
from gateway_v2.edge.rag_deps import RagDeps
from gateway_v2.edge.rag_stages import RagRefused, detector_for, redact_text, resolver_at, scan_text
from gateway_v2.edge.routes import ChatRoute
from gateway_v2.runtime.resources_rag import RagBounds

__all__ = ("INGEST_PREFIX", "RagIngestRoute", "parse_documents", "scan_documents")

INGEST_PREFIX = "/v1/rag/ingest/"
_ACCEPTED = 202
_OK = 200
_TRUST_BY_NAME: Mapping[str, TrustLevel] = MappingProxyType(
    {
        "untrusted": TrustLevel.UNTRUSTED,
        "low": TrustLevel.LOW,
        "medium": TrustLevel.MEDIUM,
    }
)
_RESERVED = "_rag_"
_PRINTABLE_LO, _PRINTABLE_HI = 0x21, 0x7E


def _valid_id(value: object, max_len: int) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= max_len
        and all(_PRINTABLE_LO <= ord(ch) <= _PRINTABLE_HI for ch in value)
    )


def valid_ids(raw: object, bounds: RagBounds) -> tuple[str, ...]:
    """Delete ids: a non-empty list of valid ids within the per-request bound."""
    if (
        not isinstance(raw, list)
        or not raw
        or len(raw) > bounds.ingest_documents_per_request
        or not all(_valid_id(i, bounds.id_max_length) for i in raw)
    ):
        raise RagRefused(posture.RAG_INVALID_REQUEST)
    return tuple(str(i) for i in raw)


def parse_documents(
    body: Mapping[str, object],
    key: str,
    bounds: RagBounds,
    mint_id: Callable[[], str],
) -> tuple[IngestDoc, ...]:
    """Validate ``body[key]``. Trust can be lowered, never raised above ``MEDIUM``."""
    raw = body.get(key)
    if not isinstance(raw, list) or not raw:
        raise RagRefused(posture.RAG_INVALID_REQUEST)
    if len(raw) > bounds.ingest_documents_per_request:
        raise RagRefused(posture.RAG_INGEST_TOO_LARGE)
    trust_raw = body.get("trust", "medium")
    if not isinstance(trust_raw, str) or trust_raw not in _TRUST_BY_NAME:
        raise RagRefused(posture.RAG_INVALID_REQUEST)
    base = _TRUST_BY_NAME[trust_raw]
    docs: list[IngestDoc] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise RagRefused(posture.RAG_INVALID_REQUEST)
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip():
            raise RagRefused(posture.RAG_INVALID_REQUEST)
        if len(text.encode()) > bounds.chunk_text_bytes:
            raise RagRefused(posture.RAG_INGEST_TOO_LARGE)
        meta = entry.get("metadata")
        if meta is not None and (
            not isinstance(meta, dict) or any(str(k).startswith(_RESERVED) for k in meta)
        ):
            raise RagRefused(posture.RAG_INVALID_REQUEST)
        source = entry.get("source", "")
        if not isinstance(source, str) or len(source) > bounds.id_max_length:
            raise RagRefused(posture.RAG_INVALID_REQUEST)
        doc_id = entry.get("id")
        if doc_id is None:
            doc_id = mint_id()
        if not _valid_id(doc_id, bounds.id_max_length) or doc_id in seen:
            raise RagRefused(posture.RAG_INVALID_REQUEST)
        seen.add(str(doc_id))
        trust = base if source else min(base, TrustLevel.LOW)
        docs.append(IngestDoc(str(doc_id), text, source, trust))
    return tuple(docs)


async def scan_documents(
    chat: ChatRoute,
    deps: RagDeps,
    s: Session,
    docs: Sequence[IngestDoc],
    surface: Surface,
) -> tuple[IngestDoc, ...]:
    """Content scan with stage ``None``. BLOCK refuses; REDACT stores the verified masked text."""
    detector = detector_for(chat, deps, s.eff, surface)
    resolver = resolver_at(chat, s.plan, Phase.INPUT, surface, None)
    out: list[IngestDoc] = []
    for doc in docs:
        scan = await scan_text(chat, detector, resolver, "rag:ingest", doc.text)
        if scan.refuse_code is not None or scan.decision is None:
            raise RagRefused(scan.refuse_code or "scan_failure")
        decision = scan.decision
        if decision.disposition is Disposition.BLOCK:
            raise RagRefused(posture.RAG_INGEST_BLOCKED)
        text = doc.text
        if decision.disposition is Disposition.REDACT:
            masked = redact_text(text, decision)
            if masked is None:
                raise RagRefused("input_unmaskable")
            text = masked
        out.append(IngestDoc(doc.doc_id, text, doc.source, doc.trust))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class RagIngestRoute:
    chat: ChatRoute
    deps: RagDeps

    async def handle_post(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        await serve(
            self.chat, scope, receive, send, route="rag_ingest", surface=Surface.RAG,
            work=self._accept,
        )

    async def handle_get(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        await serve(
            self.chat, scope, receive, send, route="rag_ingest", surface=Surface.RAG,
            work=self._status, needs_body=False,
        )

    async def _accept(self, ctx: RagCtx, s: Session, body: Mapping[str, object]) -> Reply:
        chat, deps = self.chat, self.deps
        bounds = chat.contract.rag_bounds()
        ns = namespace_for(chat, s, body.get("collection"))
        store_id = store_of(deps, s)
        if deps.embedder is None:
            raise RagRefused(posture.RAG_EMBEDDER_UNAVAILABLE)
        docs = parse_documents(
            body, "documents", bounds, lambda: "doc-" + deps.token_source(bounds.canary_bytes)
        )
        scanned = await scan_documents(chat, deps, s, docs, Surface.RAG)
        job = IngestJob(
            job_id="job-" + deps.token_source(bounds.canary_bytes),
            org_id=s.org_id,
            project=ns.project,
            store_id=store_id,
            wire=wire_of(ns),
            state=STATE_ACCEPTED,
            total=len(scanned),
        )
        try:
            put = await deps.job_store.put(job)
        except Exception:  # noqa: BLE001
            put = False
        if not put:
            raise RagRefused(posture.RAG_UNAVAILABLE)
        if not deps.ingest_queue.submit(job, scanned):
            await deps.job_store.remove(job.job_id)
            raise RagRefused(posture.RAG_INGEST_OVERLOAD)
        ctx.detail["collection"] = ns.collection
        return Reply(_ACCEPTED, {"job_id": job.job_id, "state": STATE_ACCEPTED})

    async def _status(self, ctx: RagCtx, s: Session, body: Mapping[str, object]) -> Reply:
        del body
        path = ctx.scope.get("path")
        job_id = path[len(INGEST_PREFIX) :] if isinstance(path, str) else ""
        found = None
        if job_id and "/" not in job_id:
            found = await self.deps.job_store.get(job_id)
        project = s.project if s.eff.vector_db_isolation == "project" else "default"
        if found is None or found.org_id != s.org_id or found.project != project:
            raise RagRefused(posture.RAG_JOB_NOT_FOUND)
        return Reply(
            _OK,
            {
                "job_id": found.job_id,
                "state": found.state,
                "code": found.code,
                "stored": found.stored,
                "total": found.total,
            },
        )
