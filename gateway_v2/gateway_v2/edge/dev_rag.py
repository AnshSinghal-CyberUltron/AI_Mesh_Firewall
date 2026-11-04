"""DEV PROFILE wiring for the GW31 RAG and vector surfaces.

Mirrors ``dev_mcp.py``. It builds ``RagDeps`` over an in-process vector adapter, the model-free
``DeterministicHashEmbedder``, an in-memory ChunkStore and an in-memory job store. All of it
lives in ONE process: run a single worker, because data ingested in another process is invisible
and queries then fail closed with ``rag_chunk_store_unavailable``. A restart clears everything.

The dev profile has no Tier-2 backend. Grounding in GW31 is an availability gate only, so the dev
grounder reports ``ok``: the deterministic detectors are the only semantic layer here and they
are available. Production wiring (GW36) supplies the real grounder.
"""
from __future__ import annotations

import asyncio
import secrets
from collections.abc import Coroutine, Mapping
from typing import cast

from gateway_v2.dispatch.rag_chunks import InMemoryChunkStore
from gateway_v2.dispatch.rag_context import GROUND_OK, GroundingVerdict
from gateway_v2.dispatch.rag_dev_store import InMemoryVectorAdapter
from gateway_v2.dispatch.rag_embedding import DeterministicHashEmbedder
from gateway_v2.dispatch.rag_ingest import IngestQueue, InMemoryJobStore
from gateway_v2.dispatch.vector_registry import VectorRegistry
from gateway_v2.edge.rag_deps import Detector as _Detector
from gateway_v2.edge.rag_deps import RagDeps
from gateway_v2.runtime.resources import ResourceContract

__all__ = ("DEV_STORE_ID", "attach_dev_surfaces", "build_dev_rag")

DEV_STORE_ID = "dev-memory"


class _DevGrounder:
    async def check(self, frame_text: str, query: str) -> GroundingVerdict:
        del frame_text, query
        return GroundingVerdict(GROUND_OK)


class _Spawner:
    """Keeps a strong reference to each running job so it is not collected mid-flight."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    def __call__(self, coro: Coroutine[object, object, None]) -> object:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task


def build_dev_rag(
    env: Mapping[str, str], contract: ResourceContract, *, deps: Mapping[str, object]
) -> RagDeps:
    """``deps`` is the dev dependency table: it must carry ``phase_resolvers`` and the detector."""
    del env
    if deps.get("phase_resolvers") is None:
        raise ValueError("RAG needs phase_resolvers: stage scoping cannot bind without them")
    bounds = contract.rag_bounds()
    registry = VectorRegistry({DEV_STORE_ID: InMemoryVectorAdapter()})
    chunk_store = InMemoryChunkStore(
        contract.rag_chunk_store_entries(),
        contract.rag_chunk_store_bytes(),
        bounds.stored_vector_bytes_per_dim,
    )
    embedder = DeterministicHashEmbedder(bounds.embedding_dimension)
    job_store = InMemoryJobStore(bounds.job_store_entries)
    queue = IngestQueue(
        registry=registry,
        chunk_store=chunk_store,
        embedder=embedder,
        job_store=job_store,
        spawn=_Spawner(),
        depth=bounds.ingest_queue_depth,
        concurrency=bounds.ingest_concurrency,
        timeout_s=contract.vector_operation_timeout_s(),
    )
    return RagDeps(
        registry=registry,
        chunk_store=chunk_store,
        store_id_of=lambda org_id: DEV_STORE_ID,
        detector_tier1=cast(_Detector, deps["input_detector"]),
        ingest_queue=queue,
        job_store=job_store,
        token_source=secrets.token_hex,
        embedder=embedder,
        detector_tier1_output=cast(_Detector, deps.get("output_detector")),
        grounder=_DevGrounder(),
    )


def attach_dev_surfaces(
    deps: dict[str, object], mcp: object | None, env: Mapping[str, str], contract: ResourceContract
) -> None:
    """Set ``deps["mcp"]`` when MCP is configured (unchanged) and always ``deps["rag"]``."""
    if mcp is not None:
        deps["mcp"] = mcp
    deps["rag"] = build_dev_rag(env, contract, deps=deps)
