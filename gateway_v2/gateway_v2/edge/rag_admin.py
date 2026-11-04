"""Collections, documents and admin collection routes (GW31).

Every handler is Principal-scoped: there is no cross-org parameter. The namespace is built once
through ``build_namespace`` and the GW30 adapter is called with the encoded wire name only.

``DELETE`` on a collection deletes the listed ``ids`` (Clarification C-1). The GW30 protocol has
no drop-collection operation, so a delete with no ``ids`` is ``unsupported_operation`` with zero
adapter calls, never an empty success.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from gateway_v2.dispatch.rag_namespace import parse_wire
from gateway_v2.dispatch.rag_retrieval import listed_name
from gateway_v2.dispatch.vector_adapter import CreateRequest, DeleteRequest, ListRequest
from gateway_v2.domain import posture
from gateway_v2.domain.plan import Surface
from gateway_v2.edge.asgi_sse import ASGIReceive, ASGIScope, ASGISend
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
from gateway_v2.edge.rag_ingest import valid_ids
from gateway_v2.edge.rag_stages import RagRefused
from gateway_v2.edge.routes import ChatRoute

__all__ = ("RagAdminRoute",)

LOG = logging.getLogger("amf.rag")
_OK = 200
_METHOD_WORK: Mapping[str, str] = MappingProxyType(
    {"GET": "_list", "POST": "_create", "DELETE": "_delete_ids"}
)


@dataclass(frozen=True, slots=True)
class RagAdminRoute:
    chat: ChatRoute
    deps: RagDeps

    async def handle_collections(
        self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend
    ) -> None:
        await self._dispatch(scope, receive, send, "rag")

    async def handle_admin_collections(
        self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend
    ) -> None:
        await self._dispatch(scope, receive, send, "rag_admin")

    async def handle_documents(
        self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend
    ) -> None:
        await serve(
            self.chat, scope, receive, send, route="rag", surface=Surface.RAG,
            work=self._delete_ids,
        )

    async def _dispatch(
        self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend, route: str
    ) -> None:
        method = scope.get("method")
        name = _METHOD_WORK.get(method) if isinstance(method, str) else None
        work = getattr(self, name) if name else self._not_allowed
        await serve(
            self.chat, scope, receive, send, route=route, surface=Surface.RAG,
            work=work, needs_body=method != "GET",
        )

    async def _not_allowed(self, ctx: RagCtx, s: Session, body: Mapping[str, object]) -> Reply:
        raise RagRefused(posture.RAG_INVALID_REQUEST)

    async def _list(self, ctx: RagCtx, s: Session, body: Mapping[str, object]) -> Reply:
        deps = self.deps
        store_id = store_of(deps, s)
        result = await adapter_call(
            adapter_for(deps, store_id).list_collections(ListRequest(s.org_id, store_id)),
            self.chat.contract.vector_operation_timeout_s(),
        )
        if not result.ok or result.collections is None:
            raise RagRefused(result.code if not result.ok else posture.RAG_RETRIEVAL_INVALID)
        project = s.project if s.eff.vector_db_isolation == "project" else "default"
        names: list[str] = []
        for raw in result.collections:
            wire = listed_name(raw)
            ns = None if wire is None else parse_wire(wire)
            if ns is not None and ns.org_id == s.org_id and ns.project == project:
                names.append(ns.collection)
        return Reply(_OK, {"collections": sorted(names)})

    async def _create(self, ctx: RagCtx, s: Session, body: Mapping[str, object]) -> Reply:
        deps = self.deps
        ns = namespace_for(self.chat, s, body.get("collection"))
        if deps.embedder is None:
            raise RagRefused(posture.RAG_EMBEDDER_UNAVAILABLE)
        store_id = store_of(deps, s)
        result = await adapter_call(
            adapter_for(deps, store_id).create_collection(
                CreateRequest(s.org_id, store_id, wire_of(ns), deps.embedder.dimension, "cosine")
            ),
            self.chat.contract.vector_operation_timeout_s(),
        )
        if not result.ok:
            raise RagRefused(result.code)
        return Reply(_OK, {"collection": ns.collection})

    async def _delete_ids(self, ctx: RagCtx, s: Session, body: Mapping[str, object]) -> Reply:
        deps = self.deps
        ns = namespace_for(self.chat, s, body.get("collection"))
        if body.get("ids") is None:
            raise RagRefused(posture.UNSUPPORTED_OPERATION)
        ids = valid_ids(body.get("ids"), self.chat.contract.rag_bounds())
        store_id, wire = store_of(deps, s), wire_of(ns)
        result = await adapter_call(
            adapter_for(deps, store_id).delete(DeleteRequest(s.org_id, store_id, wire, ids)),
            self.chat.contract.vector_operation_timeout_s(),
        )
        if not result.ok:
            raise RagRefused(result.code)
        try:
            await deps.chunk_store.delete_many(wire, ids)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("chunk store cleanup failed: %s", type(exc).__name__)
        return Reply(_OK, {"deleted": result.count if result.count is not None else len(ids)})
