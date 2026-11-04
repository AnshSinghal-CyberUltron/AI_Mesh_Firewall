"""``/v1/vector/query|upsert|delete`` and ``GET /v1/vector/config`` (GW31).

Query runs the retriever scan and the ranker (no query stage: there is no text) with
``Surface.VECTOR``, so a poisoned chunk is caught on this path too (L31-1). Upsert is synchronous
and shares ``write_batch`` with async ingest, so the two cannot drift. The gateway embeds the
text; a client cannot write a vector that does not match its text. ``config`` is read-only and its
response type has no credential, endpoint or ``detail_ref`` field, which is how the guarantee is
structural.
"""
from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from dataclasses import dataclass

from gateway_v2.dispatch.rag_ingest import write_batch
from gateway_v2.dispatch.vector_adapter import DeleteRequest
from gateway_v2.domain import posture
from gateway_v2.domain.decision import Disposition
from gateway_v2.domain.plan import Surface
from gateway_v2.edge.asgi_sse import ASGIReceive, ASGIScope, ASGISend
from gateway_v2.edge.rag import chunk_body, trace_body
from gateway_v2.edge.rag_common import (
    RagCtx,
    Reply,
    Session,
    adapter_call,
    adapter_for,
    namespace_for,
    serve,
    store_of,
    wire_of,
)
from gateway_v2.edge.rag_deps import RagDeps
from gateway_v2.edge.rag_ingest import parse_documents, scan_documents, valid_ids
from gateway_v2.edge.rag_stages import RagRefused, redact_text, run_ranker, run_retriever
from gateway_v2.edge.routes import ChatRoute

__all__ = ("RagVectorRoute",)

_OK = 200


def _vector(raw: object) -> tuple[float, ...]:
    if not isinstance(raw, list) or not raw:
        raise RagRefused(posture.RAG_INVALID_REQUEST)
    out: list[float] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            raise RagRefused(posture.RAG_INVALID_REQUEST)
        out.append(float(item))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class RagVectorRoute:
    chat: ChatRoute
    deps: RagDeps

    async def _serve(
        self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend, work: object, **kw: bool
    ) -> None:
        await serve(
            self.chat, scope, receive, send, route="vector", surface=Surface.VECTOR,
            work=work, **kw,  # type: ignore[arg-type]
        )

    async def handle_query(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        await self._serve(scope, receive, send, self._query)

    async def handle_upsert(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        await self._serve(scope, receive, send, self._upsert)

    async def handle_delete(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        await self._serve(scope, receive, send, self._delete)

    async def handle_config(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        await self._serve(scope, receive, send, self._config, needs_body=False)

    async def _query(self, ctx: RagCtx, s: Session, body: Mapping[str, object]) -> Reply:
        chat, deps = self.chat, self.deps
        bounds = chat.contract.rag_bounds()
        ns = namespace_for(chat, s, body.get("collection"))
        vector = _vector(body.get("vector"))
        eff = s.eff
        if "top_k" in body:
            top_k = body["top_k"]
            if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
                raise RagRefused(posture.RAG_INVALID_REQUEST)
            eff = dataclasses.replace(eff, top_k=min(top_k, eff.top_k))
        timeout_s = chat.contract.vector_operation_timeout_s()
        got = await run_retriever(
            chat, deps, s.plan, eff, surface=Surface.VECTOR, org_id=s.org_id, project=ns.project,
            store_id=store_of(deps, s), wire=wire_of(ns), vector=vector, timeout_s=timeout_s,
        )
        ctx.notes.append(got.note)
        ranked = await run_ranker(
            chat, deps, s.plan, eff, surface=Surface.VECTOR, query_vector=vector,
            chunks=got.chunks, bounds=bounds,
        )
        ctx.notes.append(ranked.note)
        matches = []
        for item in ranked.chunks:
            text = item.chunk.text
            decision = item.decision
            if decision is not None and decision.disposition is Disposition.REDACT:
                if eff.rag_redaction_enabled:
                    masked = redact_text(text, decision)
                    if masked is None:
                        raise RagRefused(posture.RAG_OUTPUT_WITHHELD)
                    text = masked
            matches.append(chunk_body(item, text))
        return Reply(_OK, {"matches": matches, "trace": trace_body(ctx.notes)})

    async def _upsert(self, ctx: RagCtx, s: Session, body: Mapping[str, object]) -> Reply:
        chat, deps = self.chat, self.deps
        bounds = chat.contract.rag_bounds()
        ns = namespace_for(chat, s, body.get("collection"))
        store_id = store_of(deps, s)
        if deps.embedder is None:
            raise RagRefused(posture.RAG_EMBEDDER_UNAVAILABLE)
        docs = parse_documents(
            body, "records", bounds, lambda: "doc-" + deps.token_source(bounds.canary_bytes)
        )
        scanned = await scan_documents(chat, deps, s, docs, Surface.VECTOR)
        code, stored = await write_batch(
            deps.registry, deps.chunk_store, deps.embedder, store_id=store_id, org_id=s.org_id,
            wire=wire_of(ns), docs=scanned,
            timeout_s=chat.contract.vector_operation_timeout_s(),
        )
        if code != posture.VECTOR_OK or stored != len(scanned):
            raise RagRefused(code if code != posture.VECTOR_OK else posture.PROVIDER_UNREACHABLE)
        return Reply(_OK, {"stored": stored})

    async def _delete(self, ctx: RagCtx, s: Session, body: Mapping[str, object]) -> Reply:
        chat, deps = self.chat, self.deps
        ns = namespace_for(chat, s, body.get("collection"))
        ids = valid_ids(body.get("ids"), chat.contract.rag_bounds())
        store_id, wire = store_of(deps, s), wire_of(ns)
        adapter = adapter_for(deps, store_id)
        result = await adapter_call(
            adapter.delete(DeleteRequest(s.org_id, store_id, wire, ids)),
            chat.contract.vector_operation_timeout_s(),
        )
        if not result.ok:
            raise RagRefused(result.code)
        await deps.chunk_store.delete_many(wire, ids)
        return Reply(_OK, {"deleted": result.count if result.count is not None else len(ids)})

    async def _config(self, ctx: RagCtx, s: Session, body: Mapping[str, object]) -> Reply:
        del body
        if ctx.scope.get("method") != "GET":
            raise RagRefused(posture.RAG_INVALID_REQUEST)
        store_id = store_of(self.deps, s)
        caps = adapter_for(self.deps, store_id).capabilities()
        return Reply(
            _OK,
            {
                "connector": store_id,
                "capabilities": {
                    "filters": caps.filters,
                    "namespaces": caps.namespaces,
                    "hybrid_search": caps.hybrid_search,
                },
            },
        )
