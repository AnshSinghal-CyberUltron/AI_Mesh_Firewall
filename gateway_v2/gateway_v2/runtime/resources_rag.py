"""GW31 RAG capacity bounds, split out of ``runtime/resources.py`` (the 800-line size gate).

``ResourceContract`` mixes :class:`RagBoundsMixin` in, exactly like ``VectorBoundsMixin``.
This module is the only place with RAG numbers. ``rag_bounds()`` fails closed with
``CapacityUnavailable`` when any bound is not positive, the ``mcp_bounds()`` pattern.

Relevance mapping: ``relevance = (cosine + 1) / 2``, so orthogonal vectors score about 0.5. A
threshold at or below 0.5 filters nothing that is merely unrelated. The default is therefore
comfortably above 0.5. The runbook and v1 give no number, so 0.6 is a conservative documented
choice, not a measured one.

``wire_max_length`` is the strictest total name limit across connectors (63: Chroma, and a
Postgres identifier) minus the longest binding transport prefix (``gw30_``, 5 characters):
63 - 5 = 58. Pinecone's own namespace limit is not visible in its transport and is UNVERIFIED
here. 58 is a conservative assumption to be confirmed by the deferred cloud-store run; a lower
limit there fails closed on a store error, never a leak.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from gateway_v2.runtime.errors import CapacityUnavailable

__all__ = ("RagBounds", "RagBoundsMixin")


@dataclass(frozen=True, slots=True)
class RagBounds:
    """Read through ``ResourceContract.rag_bounds()``."""

    body_bytes: int = 4 * 1024 * 1024
    query_bytes: int = 16 * 1024
    max_documents: int = 20
    default_relevance: float = 0.6
    near_duplicate_fraction: float = 0.5
    duplicate_similarity: float = 0.97
    duplicate_jaccard: float = 0.8
    anomaly_z: float = 3.5
    anomaly_min_set: int = 5
    anomaly_floor: float = 0.25
    shingle_width: int = 5
    canary_bytes: int = 16
    org_max_length: int = 36
    project_max_length: int = 32
    collection_max_length: int = 32
    wire_max_length: int = 58
    id_max_length: int = 128
    ingest_documents_per_request: int = 100
    ingest_document_bytes: int = 256 * 1024
    chunk_text_bytes: int = 8 * 1024
    ingest_queue_depth: int = 64
    ingest_concurrency: int = 2
    embedding_dimension: int = 64
    stored_vector_bytes_per_dim: int = 8
    job_store_entries: int = 256


class RagBoundsMixin:
    """Behaviour-only mixin (no fields, no ``__slots__``) for the frozen ``ResourceContract``."""

    def max_request_bytes(self) -> int:  # pragma: no cover - supplied by ResourceContract
        raise NotImplementedError

    def vector_max_result_count(self) -> int:  # pragma: no cover - supplied by ResourceContract
        raise NotImplementedError

    def rag_bounds(self) -> RagBounds:
        """The RAG bounds. The body bound is never looser than chat's pre-check."""
        defaults = RagBounds()
        body = min(defaults.body_bytes, self.max_request_bytes())
        bounds = dataclasses.replace(
            defaults,
            body_bytes=body,
            query_bytes=min(defaults.query_bytes, body),
            ingest_document_bytes=min(defaults.ingest_document_bytes, body),
            max_documents=min(defaults.max_documents, self.vector_max_result_count()),
        )
        for name in bounds.__slots__:
            if getattr(bounds, name) <= 0:
                raise CapacityUnavailable(f"rag bound {name} below minimum to serve")
        if not 0.5 < bounds.default_relevance <= 1.0:
            raise CapacityUnavailable("rag default relevance outside (0.5, 1]")
        return bounds

    def rag_max_documents_ceiling(self) -> int:
        return self.rag_bounds().max_documents

    def rag_default_relevance_threshold(self) -> float:
        return self.rag_bounds().default_relevance

    def rag_chunk_store_entries(self) -> int:
        b = self.rag_bounds()
        return b.ingest_queue_depth * b.ingest_documents_per_request

    def rag_chunk_store_bytes(self) -> int:
        b = self.rag_bounds()
        per_entry = b.chunk_text_bytes + b.embedding_dimension * b.stored_vector_bytes_per_dim
        return self.rag_chunk_store_entries() * per_entry
