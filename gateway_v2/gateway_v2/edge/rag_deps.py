"""GW31 RAG dependencies, built at boot and handed to ``build_app``/``build_routes``.

Every field is injected; nothing here builds a connector, an embedder or a store. Both detectors
MUST be (or be built on) a ``DeterministicFindings`` so the RAG/VECTOR surface binds
(``bind_surface`` returns any other callable unchanged).
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from gateway_v2.dispatch.rag_chunks import ChunkStore
from gateway_v2.dispatch.rag_context import Answerer, Grounder
from gateway_v2.dispatch.rag_embedding import Embedder
from gateway_v2.dispatch.rag_ingest import IngestJobStore, IngestQueue
from gateway_v2.dispatch.vector_registry import VectorRegistry
from gateway_v2.domain.finding import Finding

__all__ = ("Detector", "RagDeps")

Detector = Callable[[str], Sequence[Finding]]


@dataclass(frozen=True, slots=True)
class RagDeps:
    registry: VectorRegistry
    chunk_store: ChunkStore
    store_id_of: Callable[[str], str | None]
    detector_tier1: Detector
    ingest_queue: IngestQueue
    job_store: IngestJobStore
    token_source: Callable[[int], str]
    embedder: Embedder | None = None
    detector_tier1_output: Detector | None = None
    grounder: Grounder | None = None
    answerer: Answerer | None = None
