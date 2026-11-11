"""Route entries for the RAG and vector surfaces (GW31).

``edge/routes.py`` is at its size limit, so every RAG entry is built here and merged by
``surface_entries``. Without dependencies the same paths answer a declared ``rag_unavailable``
(503), never a 501.
"""
from __future__ import annotations

from gateway_v2.domain import posture
from gateway_v2.edge import errors
from gateway_v2.edge.asgi_sse import ASGIReceive, ASGIScope, ASGISend
from gateway_v2.edge.rag import RagRoute
from gateway_v2.edge.rag_admin import RagAdminRoute
from gateway_v2.edge.rag_deps import RagDeps
from gateway_v2.edge.rag_ingest import INGEST_PREFIX, RagIngestRoute
from gateway_v2.edge.rag_vector import RagVectorRoute
from gateway_v2.edge.routes import ChatRoute, RouteHandler, emit_response

__all__ = ("rag_entries",)

_Entries = dict[tuple[str, str], RouteHandler]
_EXACT = (
    ("POST", "/v1/rag/query"),
    ("POST", "/v1/rag/ingest"),
    ("GET", "/v1/rag/collections"),
    ("POST", "/v1/rag/collections"),
    ("DELETE", "/v1/rag/collections"),
    ("DELETE", "/v1/rag/documents"),
    ("GET", "/v1/admin/rag/collections"),
    ("POST", "/v1/admin/rag/collections"),
    ("DELETE", "/v1/admin/rag/collections"),
    ("POST", "/v1/vector/query"),
    ("POST", "/v1/vector/upsert"),
    ("POST", "/v1/vector/delete"),
    ("GET", "/v1/vector/config"),
)


def _unavailable() -> RouteHandler:
    async def _handle(scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        await emit_response(send, errors.render_http(posture.RAG_UNAVAILABLE))

    return _handle


def rag_entries(chat: ChatRoute, rag: RagDeps | None) -> tuple[_Entries, _Entries]:
    """``(exact, prefix)`` entries for ``/v1/rag/*``, ``/v1/vector/*`` and ``/v1/admin/rag/*``."""
    if rag is None:
        handler = _unavailable()
        return {key: handler for key in _EXACT}, {("GET", INGEST_PREFIX): handler}
    query = RagRoute(chat, rag)
    ingest = RagIngestRoute(chat, rag)
    admin = RagAdminRoute(chat, rag)
    vector = RagVectorRoute(chat, rag)
    exact: _Entries = {
        ("POST", "/v1/rag/query"): query.handle_query,
        ("POST", "/v1/rag/ingest"): ingest.handle_post,
        ("GET", "/v1/rag/collections"): admin.handle_collections,
        ("POST", "/v1/rag/collections"): admin.handle_collections,
        ("DELETE", "/v1/rag/collections"): admin.handle_collections,
        ("DELETE", "/v1/rag/documents"): admin.handle_documents,
        ("GET", "/v1/admin/rag/collections"): admin.handle_admin_collections,
        ("POST", "/v1/admin/rag/collections"): admin.handle_admin_collections,
        ("DELETE", "/v1/admin/rag/collections"): admin.handle_admin_collections,
        ("POST", "/v1/vector/query"): vector.handle_query,
        ("POST", "/v1/vector/upsert"): vector.handle_upsert,
        ("POST", "/v1/vector/delete"): vector.handle_delete,
        ("GET", "/v1/vector/config"): vector.handle_config,
    }
    return exact, {("GET", INGEST_PREFIX): ingest.handle_get}
