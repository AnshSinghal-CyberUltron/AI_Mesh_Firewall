"""
Vector DB client abstraction for the Gateway Data Plane.

Provides a unified async interface for querying different vector databases
(ChromaDB, Pinecone, Milvus). All synchronous operations are offloaded to a
ThreadPoolExecutor to avoid blocking the async event loop.

Namespace isolation is enforced at the client level: the actual collection
name is always constructed as ``{project_id}__{collection_name}`` to prevent
cross-tenant leakage.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol, runtime_checkable

LOG = logging.getLogger("gateway.vector_client")

DEFAULT_THREAD_POOL_SIZE = 4

# Upsert-time embedding-validity band (E11 poisoning guard). A legitimate dense
# embedding has a finite L2 norm in a sane range — typical models emit either
# unit-normalized vectors (norm ~= 1.0) or unnormalized vectors with norms of a
# few tens. A vector whose magnitude lands far outside this band, or that is
# all-zero / near-zero / non-finite, is a poisoning or corruption signal, not a
# real document embedding. The band is deliberately WIDE (and absolute) so it
# fires only on unambiguous garbage — zero false positives on legitimate
# embeddings — unlike per-collection distance calibration (the F12 lesson).
_EMBEDDING_MIN_L2_NORM = 1e-3
_EMBEDDING_MAX_L2_NORM = 1e4
_INF = float("inf")


def _embedding_validity_reason(vector: Any) -> str | None:
    """Return a rejection reason if ``vector`` is a degenerate/adversarial
    embedding that must NOT be written to the vector store, else ``None``.

    Rejects (clear poisoning / corruption signals, ~zero false positives on
    legitimate embeddings):
      * empty / non-sequence
      * any NaN or +/-inf component
      * any non-numeric component
      * all-zero or near-zero L2 norm (norm < ``_EMBEDDING_MIN_L2_NORM``)
      * abnormal L2 magnitude (non-finite, or outside the sane absolute band)

    Pure-Python L2 + ``isfinite``-style checks (no numpy/math dependency, to
    match ``byok_embedder``). The L2 sum is accumulated as we validate each
    component so a single pass both finds garbage components and the norm.
    """
    if vector is None:
        return "empty/non-sequence"
    try:
        n = len(vector)
    except TypeError:
        return "empty/non-sequence"
    if n == 0:
        return "empty"
    sq_sum = 0.0
    for x in vector:
        try:
            xf = float(x)
        except (TypeError, ValueError, OverflowError):
            return "non-numeric component"
        if xf != xf:
            return "NaN component"
        if xf == _INF or xf == -_INF:
            return "inf component"
        sq_sum += xf * xf
    # sq_sum can overflow to inf for very large (poisoned) magnitudes.
    if sq_sum != sq_sum or sq_sum == _INF:
        return "non-finite L2 norm"
    norm = sq_sum ** 0.5
    if norm < _EMBEDDING_MIN_L2_NORM:
        return f"near-zero L2 norm ({norm:.3g})"
    if norm > _EMBEDDING_MAX_L2_NORM:
        return f"abnormal L2 norm ({norm:.3g})"
    return None


def filter_valid_embedding_batch(
    documents: list[str],
    ids: list[str],
    embeddings: list[Any],
    metadatas: list[dict[str, Any]] | None,
    *,
    context: str = "",
) -> tuple[list[str], list[str], list[Any], list[dict[str, Any]] | None, int]:
    """Drop any embedding that fails the upsert-time validity guard, dropping
    its paired id / document / metadata IN LOCKSTEP so the remaining batch stays
    index-aligned. Returns the filtered ``(documents, ids, embeddings,
    metadatas, rejected_count)``.

    This is the vector-write poisoning/anomaly guard (E11 part b): rag_ingest
    embeds and upserts with no validity check, so a corrupt/adversarial vector
    (NaN/inf, all-zero, or wildly-scaled magnitude) would be written and later
    surface as a "real" nearest neighbour. We refuse to persist such vectors.
    Logs a count of rejected vectors; never raises (a single poisoned row must
    not fail the whole legitimate batch).
    """
    keep_docs: list[str] = []
    keep_ids: list[str] = []
    keep_emb: list[Any] = []
    keep_meta: list[dict[str, Any]] | None = [] if metadatas is not None else None
    rejected = 0
    for i, emb in enumerate(embeddings):
        reason = _embedding_validity_reason(emb)
        if reason is not None:
            rejected += 1
            _id = ids[i] if i < len(ids) else "?"
            LOG.warning(
                "E11 upsert guard: rejecting poisoned/degenerate embedding "
                "(id=%s, reason=%s, context=%s) — vector + its doc/metadata "
                "dropped, NOT written.",
                _id, reason, context or "upsert",
            )
            continue
        keep_emb.append(emb)
        if i < len(ids):
            keep_ids.append(ids[i])
        if i < len(documents):
            keep_docs.append(documents[i])
        if keep_meta is not None and metadatas is not None and i < len(metadatas):
            keep_meta.append(metadatas[i])
    return keep_docs, keep_ids, keep_emb, keep_meta, rejected


@runtime_checkable
class VectorDBClient(Protocol):
    """Protocol defining the vector DB client interface."""

    async def query(
        self,
        collection_name: str,
        query_text: str,
        n_results: int,
        where: dict[str, Any] | None,
        namespace: str,
        project_id: str,
    ) -> list[dict[str, Any]]:
        ...

    async def health_check(self) -> bool:
        ...


class ChromaDBClient:
    """
    Async ChromaDB HTTP client for the Gateway Data Plane.

    Uses chromadb.HttpClient (synchronous) wrapped in ThreadPoolExecutor
    to avoid blocking the event loop. Collection names are namespaced by
    project_id to enforce tenant isolation.
    """

    def __init__(
        self,
        url: str,
        auth_token: str = "",
        thread_pool_size: int = DEFAULT_THREAD_POOL_SIZE,
    ) -> None:
        self._url = url
        self._auth_token = auth_token
        self._executor = ThreadPoolExecutor(
            max_workers=thread_pool_size,
            thread_name_prefix="chroma",
        )
        self._client = None
        LOG.info("ChromaDBClient initialized (url=%s)", url)

    def _get_client(self):
        """Lazy-init the ChromaDB HTTP client."""
        if self._client is None:
            try:
                import chromadb

                settings = chromadb.config.Settings(
                    chroma_api_impl="chromadb.api.fastapi.FastAPI",
                    anonymized_telemetry=False,
                )
                host = self._url.replace("http://", "").replace("https://", "").split(":")[0]
                port_str = self._url.split(":")[-1].rstrip("/")
                port = int(port_str) if port_str.isdigit() else 8000

                kwargs: dict[str, Any] = {
                    "host": host,
                    "port": port,
                    "settings": settings,
                }
                if self._auth_token:
                    kwargs["headers"] = {"Authorization": f"Bearer {self._auth_token}"}

                self._client = chromadb.HttpClient(**kwargs)
                LOG.info("ChromaDB HTTP client connected to %s:%d", host, port)
            except Exception:
                LOG.exception("Failed to initialize ChromaDB client")
                raise
        return self._client

    def _build_collection_name(self, project_id: str, collection_name: str) -> str:
        """Construct the namespaced collection name for tenant isolation."""
        return f"{project_id}__{collection_name}"

    def _query_sync(
        self,
        collection_name: str,
        query_text: str,
        n_results: int,
        where: dict[str, Any] | None,
        project_id: str,
    ) -> list[dict[str, Any]]:
        """Synchronous query execution, run inside ThreadPoolExecutor."""
        client = self._get_client()
        namespaced = self._build_collection_name(project_id, collection_name)

        try:
            collection = client.get_collection(name=namespaced)
        except Exception:
            LOG.warning("Collection '%s' not found in ChromaDB", namespaced)
            return []

        query_params: dict[str, Any] = {
            "query_texts": [query_text],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            query_params["where"] = where

        try:
            results = collection.query(**query_params)
        except Exception:
            LOG.exception("ChromaDB query failed for collection '%s'", namespaced)
            return []

        documents: list[dict[str, Any]] = []
        ids = results.get("ids", [[]])[0]
        docs = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        for i, doc_id in enumerate(ids):
            documents.append({
                "id": doc_id,
                "content": docs[i] if i < len(docs) else "",
                "metadata": metadatas[i] if i < len(metadatas) else {},
                "distance": distances[i] if i < len(distances) else 0.0,
            })

        return documents

    async def query(
        self,
        collection_name: str,
        query_text: str,
        n_results: int = 10,
        where: dict[str, Any] | None = None,
        namespace: str = "",
        project_id: str = "",
    ) -> list[dict[str, Any]]:
        """Async query execution via ThreadPoolExecutor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            self._query_sync,
            collection_name,
            query_text,
            n_results,
            where,
            project_id,
        )

    # ── Ingestion / deletion / collection management ──

    def _add_sync(
        self,
        collection_name: str,
        documents: list[str],
        ids: list[str],
        metadatas: list[dict[str, Any]] | None,
        project_id: str,
    ) -> int:
        """Synchronous add documents to a ChromaDB collection."""
        # NOTE (E11 upsert guard): Chroma embeds documents SERVER-SIDE inside
        # ``collection.add`` — the gateway never sees the resulting vectors here,
        # so the embedding-validity guard (filter_valid_embedding_batch) has no
        # vector to inspect at this choke point. The guard applies where the
        # gateway itself produces the vectors before writing (PineconeClient.
        # _upsert_sync). Text-level content scanning still gates Chroma ingest
        # upstream in the RAG pipeline.
        client = self._get_client()
        namespaced = self._build_collection_name(project_id, collection_name)
        collection = client.get_or_create_collection(name=namespaced)
        kwargs: dict[str, Any] = {"ids": ids, "documents": documents}
        if metadatas:
            kwargs["metadatas"] = metadatas
        collection.add(**kwargs)
        return len(ids)

    async def add(
        self,
        collection_name: str,
        documents: list[str],
        ids: list[str],
        metadatas: list[dict[str, Any]] | None = None,
        project_id: str = "",
    ) -> int:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor, self._add_sync,
            collection_name, documents, ids, metadatas, project_id,
        )

    def _delete_sync(self, collection_name: str, ids: list[str], project_id: str) -> int:
        client = self._get_client()
        namespaced = self._build_collection_name(project_id, collection_name)
        try:
            collection = client.get_collection(name=namespaced)
        except Exception:
            return 0
        collection.delete(ids=ids)
        return len(ids)

    async def delete(self, collection_name: str, ids: list[str], project_id: str = "") -> int:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor, self._delete_sync, collection_name, ids, project_id,
        )

    def _list_collections_sync(self, project_id: str = "") -> list[str]:
        client = self._get_client()
        collections = client.list_collections()
        names = [c.name if hasattr(c, "name") else str(c) for c in collections]
        if project_id:
            prefix = f"{project_id}__"
            names = [n for n in names if n.startswith(prefix)]
        return names

    async def list_collections(self, project_id: str = "") -> list[str]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor, self._list_collections_sync, project_id,
        )

    def _create_collection_sync(self, collection_name: str, project_id: str) -> str:
        client = self._get_client()
        namespaced = self._build_collection_name(project_id, collection_name)
        client.get_or_create_collection(name=namespaced)
        return namespaced

    async def create_collection(self, collection_name: str, project_id: str = "") -> str:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor, self._create_collection_sync, collection_name, project_id,
        )

    def _delete_collection_sync(self, collection_name: str, project_id: str) -> bool:
        client = self._get_client()
        namespaced = self._build_collection_name(project_id, collection_name)
        try:
            client.delete_collection(name=namespaced)
            return True
        except Exception:
            LOG.warning("Failed to delete ChromaDB collection '%s'", namespaced)
            return False

    async def delete_collection(self, collection_name: str, project_id: str = "") -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor, self._delete_collection_sync, collection_name, project_id,
        )

    async def health_check(self) -> bool:
        """Check if ChromaDB is reachable."""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(self._executor, self._get_client().heartbeat)
            return True
        except Exception:
            return False


class PineconeClient:
    """
    Async Pinecone client for the Gateway Data Plane.

    Uses pinecone-client SDK with namespace-based tenant isolation.
    The project_id maps to the Pinecone namespace, and collection_name
    maps to the Pinecone index name.
    """

    def __init__(
        self,
        api_key: str,
        environment: str = "",
        embedding_model: str = "text-embedding-3-small",
        thread_pool_size: int = DEFAULT_THREAD_POOL_SIZE,
        embedding_api_key: str = "",
        reranker_model: str = "",
        is_org_byok: bool = False,
    ) -> None:
        self._api_key = api_key
        self._environment = environment
        self._embedding_model = embedding_model
        # BYOK key for an EXTERNAL (litellm) embedding model — e.g. an
        # OpenRouter / OpenAI-compatible key from the org's VectorProviderConfig.
        # Empty -> embed_texts uses gateway-environment credentials (legacy).
        self._embedding_api_key = embedding_api_key or ""
        # Per-org Pinecone-hosted reranker (e.g. 'bge-reranker-v2-m3'). Empty
        # -> no reranking (the retriever skips rerank()).
        self._reranker_model = reranker_model or ""
        # True when this client authenticates with the ORG'S OWN Pinecone key
        # (per-org BYOK), so every index on the account belongs to this org and
        # list_collections must return them ALL. False for the SHARED env-default
        # client, where one account holds many tenants' indexes and listing must
        # stay namespace-scoped to avoid leaking other tenants' collection names.
        self._is_org_byok = bool(is_org_byok)
        self._executor = ThreadPoolExecutor(
            max_workers=thread_pool_size,
            thread_name_prefix="pinecone",
        )
        self._pc = None
        LOG.info(
            "PineconeClient initialized (embedding_model=%s, reranker_model=%s)",
            embedding_model, reranker_model or "(none)",
        )

    async def rerank(
        self,
        query_text: str,
        documents: list[dict[str, Any]],
        top_n: int | None = None,
    ) -> list[dict[str, Any]]:
        """Rerank retrieved documents via Pinecone's hosted rerank Inference API.

        Reorders ``documents`` by semantic relevance to ``query_text`` using the
        org's configured ``reranker_model``. No-op (returns input unchanged) when
        no reranker is configured or on any provider error (fail-open on rerank —
        the un-reranked order is still valid retrieval).
        """
        if not self._reranker_model or not documents:
            return documents
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor, self._rerank_sync, query_text, documents, top_n
        )

    def _rerank_sync(
        self,
        query_text: str,
        documents: list[dict[str, Any]],
        top_n: int | None,
    ) -> list[dict[str, Any]]:
        try:
            pc = self._get_client()
            texts = [str(d.get("content", "") or "") for d in documents]
            resp = pc.inference.rerank(
                model=self._reranker_model,
                query=query_text,
                documents=texts,
                top_n=top_n or len(documents),
                return_documents=False,
            )
            reordered: list[dict[str, Any]] = []
            for row in resp.data:
                idx = getattr(row, "index", None)
                if idx is None and hasattr(row, "get"):
                    idx = row.get("index")
                if idx is None or not (0 <= int(idx) < len(documents)):
                    continue
                doc = dict(documents[int(idx)])
                score = getattr(row, "score", None)
                if score is None and hasattr(row, "get"):
                    score = row.get("score")
                if score is not None:
                    doc["rerank_score"] = float(score)
                reordered.append(doc)
            return reordered or documents
        except Exception:
            LOG.warning(
                "Pinecone rerank failed (model=%s); keeping retrieval order (fail-open)",
                self._reranker_model, exc_info=True,
            )
            return documents

    # Pinecone-hosted embedding models served via the Inference API. When the
    # configured embedding_model is one of these, queries/passages are embedded
    # by Pinecone itself using the same API key — no OpenAI/Bedrock key needed.
    PINECONE_INFERENCE_MODELS = {
        "multilingual-e5-large",
        "llama-text-embed-v2",
        "pinecone-sparse-english-v0",
    }

    def _get_client(self):
        """Lazy-init the Pinecone client."""
        if self._pc is None:
            try:
                from pinecone import Pinecone

                self._pc = Pinecone(api_key=self._api_key)
                LOG.info("Pinecone client connected")
            except Exception:
                LOG.exception("Failed to initialize Pinecone client")
                raise
        return self._pc

    def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        """Embed ``texts`` for the configured model.

        Pinecone-hosted models go through the Pinecone Inference API (the
        provider API key grants access — no external embedding credential), with
        ``input_type`` 'query' or 'passage' for asymmetric models like e5.
        Any other model falls back to LiteLLM (OpenAI/Bedrock/etc.).
        """
        from byok_embedder import embed_texts

        model = self._embedding_model
        is_pinecone_hosted = (
            model in self.PINECONE_INFERENCE_MODELS or model.startswith("pinecone-")
        )
        # Pinecone-hosted models are BYOK via the org's Pinecone key (used by the
        # pinecone_client). External models route through litellm using the org's
        # per-model BYOK ``embedding_api_key`` (e.g. OpenRouter) when configured,
        # falling back to gateway-environment credentials when empty. embed_texts
        # is FAIL-CLOSED: it raises on any zero/empty/wrong-shaped vector instead
        # of letting a meaningless embedding silently poison retrieval.
        return embed_texts(
            list(texts),
            embedding_model=model,
            input_type=input_type,
            pinecone_client=self._get_client() if is_pinecone_hosted else None,
            provider_api_key=None if is_pinecone_hosted else (self._embedding_api_key or None),
        )

    def _query_sync(
        self,
        collection_name: str,
        query_text: str,
        n_results: int,
        where: dict[str, Any] | None,
        project_id: str,
    ) -> list[dict[str, Any]]:
        """Synchronous Pinecone query execution."""
        pc = self._get_client()
        namespace = f"{project_id}__{collection_name}"

        try:
            index = pc.Index(collection_name)
        except Exception:
            LOG.warning("Pinecone index '%s' not accessible", collection_name)
            return []

        query_params: dict[str, Any] = {
            "namespace": namespace,
            "top_k": n_results,
            "include_metadata": True,
        }

        if where:
            query_params["filter"] = where

        # Fail-CLOSED embedding: if the query cannot be embedded we must NOT
        # fall back to a zero vector — that returns arbitrary nearest neighbours
        # as if they were real matches (silently-wrong RAG). Embed OUTSIDE the
        # result-swallowing try so the error propagates to the caller as an
        # explicit failure instead of garbage (or silently-empty) results.
        # 'query' input_type for asymmetric models like e5.
        query_vector = self._embed([query_text], input_type="query")[0]

        try:
            from pinecone import QueryResponse
            response: QueryResponse = index.query(
                vector=query_vector,
                **query_params,
            )
        except Exception:
            LOG.exception("Pinecone query failed for index '%s' namespace '%s'", collection_name, namespace)
            return []

        documents: list[dict[str, Any]] = []
        for match in response.get("matches", []):
            metadata = match.get("metadata", {})
            # NOTE: `metadata.pop("content", metadata.pop("text", ""))` would
            # evaluate the inner pop unconditionally (Python evaluates all args
            # before the call), deleting the 'text' field from the returned
            # metadata even when 'content' is present. Pop sequentially instead.
            _content = metadata.pop("content", None)
            if _content is None:
                _content = metadata.pop("text", "")
            _raw_score = match.get("score", 0.0)
            documents.append({
                "id": match.get("id", ""),
                "content": _content,
                "metadata": metadata,
                "distance": 1.0 - _raw_score,
                # Cosine similarity (higher = more relevant). Emitted so the
                # retriever's relevance-threshold filter has the key it reads
                # (it defaulted to 1.0 — a silent no-op — when only `distance`
                # was present, so degenerate matches were never filtered). (H6)
                "score": _raw_score,
            })

        return documents

    async def query(
        self,
        collection_name: str,
        query_text: str,
        n_results: int = 10,
        where: dict[str, Any] | None = None,
        namespace: str = "",
        project_id: str = "",
    ) -> list[dict[str, Any]]:
        """Async query via ThreadPoolExecutor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            self._query_sync,
            collection_name,
            query_text,
            n_results,
            where,
            project_id,
        )

    async def health_check(self) -> bool:
        """Check if Pinecone is reachable."""
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(self._executor, self._get_client().list_indexes)
            return True
        except Exception:
            return False

    # ── Ingestion / deletion / index management ──

    def _upsert_sync(
        self,
        collection_name: str,
        documents: list[str],
        ids: list[str],
        metadatas: list[dict[str, Any]] | None,
        project_id: str,
    ) -> int:
        pc = self._get_client()
        namespace = f"{project_id}__{collection_name}"
        try:
            index = pc.Index(collection_name)
        except Exception:
            LOG.warning("Pinecone index '%s' not accessible for upsert", collection_name)
            raise

        # 'passage' input_type for documents (asymmetric models like e5 embed
        # queries and passages differently).
        embeddings = self._embed(documents, input_type="passage")

        # E11 (b) upsert-time poisoning/anomaly guard: refuse to WRITE any
        # degenerate/adversarial embedding (NaN/inf, all-zero/near-zero norm, or
        # an abnormal L2 magnitude). Drop the vector AND its paired id/doc/
        # metadata in lockstep so the persisted batch stays index-aligned.
        documents, ids, embeddings, metadatas, rejected = filter_valid_embedding_batch(
            documents, ids, embeddings, metadatas,
            context=f"pinecone:{namespace}",
        )
        if rejected:
            LOG.warning(
                "E11 upsert guard: dropped %d poisoned embedding(s) before "
                "Pinecone upsert (namespace=%s); %d valid vector(s) remain.",
                rejected, namespace, len(embeddings),
            )

        vectors = []
        for i, doc_id in enumerate(ids):
            emb = embeddings[i]
            meta = dict(metadatas[i]) if metadatas and i < len(metadatas) else {}
            meta["content"] = documents[i]
            vectors.append({"id": doc_id, "values": emb, "metadata": meta})
        if not vectors:
            # Entire batch was poisoned/degenerate — nothing legitimate to write.
            return 0
        index.upsert(vectors=vectors, namespace=namespace)
        return len(vectors)

    async def upsert(
        self,
        collection_name: str,
        documents: list[str],
        ids: list[str],
        metadatas: list[dict[str, Any]] | None = None,
        project_id: str = "",
    ) -> int:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor, self._upsert_sync,
            collection_name, documents, ids, metadatas, project_id,
        )

    # Alias for unified interface
    async def add(
        self,
        collection_name: str,
        documents: list[str],
        ids: list[str],
        metadatas: list[dict[str, Any]] | None = None,
        project_id: str = "",
    ) -> int:
        return await self.upsert(collection_name, documents, ids, metadatas, project_id)

    def _delete_sync(self, collection_name: str, ids: list[str], project_id: str) -> int:
        pc = self._get_client()
        namespace = f"{project_id}__{collection_name}"
        try:
            index = pc.Index(collection_name)
            index.delete(ids=ids, namespace=namespace)
            return len(ids)
        except Exception:
            LOG.warning("Pinecone delete failed for index '%s'", collection_name)
            return 0

    async def delete(self, collection_name: str, ids: list[str], project_id: str = "") -> int:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor, self._delete_sync, collection_name, ids, project_id,
        )

    def _list_indexes_sync(self, project_id: str = "") -> list[str]:
        pc = self._get_client()
        indexes = pc.list_indexes()
        if hasattr(indexes, "indexes") and indexes.indexes:
            raw = [idx.name if hasattr(idx, "name") else str(idx) for idx in indexes.indexes]
        elif hasattr(indexes, "__iter__"):
            raw = [idx.name if hasattr(idx, "name") else str(idx) for idx in indexes]
        else:
            return []
        # Tenant isolation is namespace-based (namespace = f"{project_id}__{name}").
        # On a SHARED account (env-default key) returning ALL index names leaks the
        # infra/other-tenant collection set, so keep only indexes where THIS project
        # has a namespace; fail-closed (skip on stats error).
        #
        # A per-org BYOK client authenticates with the ORG'S OWN key — every index
        # on that account belongs to this org, so namespace-filtering would wrongly
        # hide the org's own collections (the "Existing Collections (0)" bug when a
        # freshly-connected org, or a key that has not yet ingested under this exact
        # project_id, lists its indexes). Return them all for BYOK.
        if not project_id or self._is_org_byok:
            return raw
        prefix = f"{project_id}__"
        scoped: list[str] = []
        for name in raw:
            try:
                stats = pc.Index(name).describe_index_stats()
                ns = stats.get("namespaces") if isinstance(stats, dict) else getattr(stats, "namespaces", None)
                if ns and any(str(k).startswith(prefix) for k in ns):
                    scoped.append(name)
            except Exception:
                continue
        return scoped

    async def list_collections(self, project_id: str = "") -> list[str]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self._list_indexes_sync, project_id)


class MilvusClient:
    """
    Async Milvus client for the Gateway Data Plane.

    Uses pymilvus SDK with partition-based tenant isolation.
    The project_id maps to the partition name within the collection.
    """

    def __init__(
        self,
        uri: str,
        token: str = "",
        thread_pool_size: int = DEFAULT_THREAD_POOL_SIZE,
    ) -> None:
        self._uri = uri
        self._token = token
        self._executor = ThreadPoolExecutor(
            max_workers=thread_pool_size,
            thread_name_prefix="milvus",
        )
        self._connections_initialized: set[str] = set()
        LOG.info("MilvusClient initialized (uri=%s)", uri)

    def _ensure_connection(self, alias: str = "default") -> None:
        """Lazy-init the Milvus connection."""
        if alias not in self._connections_initialized:
            try:
                from pymilvus import connections

                connect_params: dict[str, Any] = {"alias": alias, "uri": self._uri}
                if self._token:
                    connect_params["token"] = self._token
                connections.connect(**connect_params)
                self._connections_initialized.add(alias)
                LOG.info("Milvus connection established (alias=%s)", alias)
            except Exception:
                LOG.exception("Failed to connect to Milvus")
                raise

    def _query_sync(
        self,
        collection_name: str,
        query_text: str,
        n_results: int,
        where: dict[str, Any] | None,
        project_id: str,
    ) -> list[dict[str, Any]]:
        """Synchronous Milvus search execution."""
        self._ensure_connection()
        namespaced = f"{project_id}__{collection_name}"

        try:
            from pymilvus import Collection

            collection = Collection(name=namespaced)
            collection.load()
        except Exception:
            LOG.warning("Milvus collection '%s' not found or not loadable", namespaced)
            return []

        search_params: dict[str, Any] = {
            "metric_type": "COSINE",
            "params": {"nprobe": 10},
        }

        expr = None
        if where:
            conditions = []
            for field_name, field_value in where.items():
                if isinstance(field_value, str):
                    conditions.append(f'{field_name} == "{field_value}"')
                elif isinstance(field_value, (int, float)):
                    conditions.append(f"{field_name} == {field_value}")
            if conditions:
                expr = " and ".join(conditions)

        # FAIL-CLOSED: the Milvus client does not embed the query text (no
        # embedding model is wired here), so a real vector search is not
        # possible. Searching with a placeholder zero vector returns arbitrary
        # nearest-neighbours as if they were real matches — silently-wrong RAG.
        # RAISE (instead of a quiet empty-200) so the retriever surfaces a
        # retrieval_error/block, consistent with the Pinecone fail-closed path
        # and the byok_embedder contract.
        try:
            from byok_embedder import EmbeddingConfigError
        except ImportError:  # pragma: no cover - top-level import path
            from .byok_embedder import EmbeddingConfigError  # type: ignore[no-redef]
        raise EmbeddingConfigError(
            f"Milvus query embedding is not implemented for collection "
            f"'{namespaced}'; refusing to search with a placeholder vector "
            f"(fail-closed). Configure a provider with an embedding model."
        )

    async def query(
        self,
        collection_name: str,
        query_text: str,
        n_results: int = 10,
        where: dict[str, Any] | None = None,
        namespace: str = "",
        project_id: str = "",
    ) -> list[dict[str, Any]]:
        """Async query via ThreadPoolExecutor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            self._query_sync,
            collection_name,
            query_text,
            n_results,
            where,
            project_id,
        )

    async def health_check(self) -> bool:
        """Check if Milvus is reachable."""
        try:
            self._ensure_connection()
            return True
        except Exception:
            return False


def get_vector_client(vector_db_type: str, clients: dict[str, Any]) -> VectorDBClient | None:
    """Select the vector DB client for an operation based on the policy's vector_db_type."""
    return clients.get(vector_db_type)
