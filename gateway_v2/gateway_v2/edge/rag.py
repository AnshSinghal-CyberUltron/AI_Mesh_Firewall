"""``POST /v1/rag/query``: the four-stage RAG pipeline (GW31).

query -> retriever -> ranker -> generator. Each stage scans with its own GW26 ``pipeline_stage``.
The generator never calls a chat model by default: the response is the verified, redacted, bound
context (and an ``answer`` only when an ``Answerer`` was injected). Chunk text is read from the
ChunkStore, never from adapter metadata, and a lost ChunkStore is a declared error.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from gateway_v2.dispatch.rag_namespace import DEFAULT_PROJECT_SEGMENT
from gateway_v2.domain.plan import Surface
from gateway_v2.domain.rag import ScoredChunk, StageNote
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
from gateway_v2.edge.rag_stages import run_generator, run_query, run_ranker, run_retriever
from gateway_v2.edge.routes import ChatRoute

__all__ = ("RagRoute", "chunk_body", "trace_body")

_OK = 200


def trace_body(notes: list[StageNote]) -> list[dict[str, object]]:
    return [
        {
            "stage": n.stage,
            "action": n.action,
            "reason": n.reason,
            "dropped": n.dropped,
            "rule_ids": list(n.rule_ids),
        }
        for n in notes
    ]


def chunk_body(item: ScoredChunk, text: str) -> dict[str, object]:
    return {
        "id": item.chunk.chunk_id,
        "text": text,
        "source": item.chunk.source,
        "trust": item.chunk.trust.name.lower(),
        "relevance": round(item.relevance, 6),
    }


@dataclass(frozen=True, slots=True)
class RagRoute:
    chat: ChatRoute
    deps: RagDeps

    async def handle_query(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        await serve(
            self.chat, scope, receive, send, route="rag", surface=Surface.RAG, work=self._query
        )

    async def _query(self, ctx: RagCtx, s: Session, body: Mapping[str, object]) -> Reply:
        chat, deps = self.chat, self.deps
        bounds = chat.contract.rag_bounds()
        ns = namespace_for(chat, s, body.get("collection", DEFAULT_PROJECT_SEGMENT))
        wire = wire_of(ns)
        store_id = store_of(deps, s)
        ctx.detail["collection"] = ns.collection
        timeout_s = chat.contract.vector_operation_timeout_s()
        q = await run_query(
            chat, deps, s.plan, s.eff, body.get("query"), s.org_id, bounds.query_bytes
        )
        ctx.notes.append(q.note)
        got = await run_retriever(
            chat, deps, s.plan, s.eff, surface=Surface.RAG, org_id=s.org_id, project=ns.project,
            store_id=store_id, wire=wire, vector=q.vector, timeout_s=timeout_s,
        )
        ctx.notes.append(got.note)
        ranked = await run_ranker(
            chat, deps, s.plan, s.eff, surface=Surface.RAG, query_vector=q.vector,
            chunks=got.chunks, bounds=bounds,
        )
        ctx.notes.append(ranked.note)
        gen = await run_generator(
            chat, deps, s.plan, s.eff, org_id=s.org_id, project=ns.project, query=q.text,
            chunks=ranked.chunks, canary_bytes=bounds.canary_bytes, timeout_s=timeout_s,
        )
        ctx.notes.append(gen.note)
        payload: dict[str, object] = {
            "context": [chunk_body(item, text) for item, text in gen.texts],
            "answer": gen.answer,
            "trace": trace_body(ctx.notes),
            "settings_clamped": s.eff.settings_clamped,
        }
        return Reply(_OK, payload)
