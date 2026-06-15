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
        vectors = []
        for i, doc_id in enumerate(ids):
            emb = embeddings[i]
            meta = dict(metadatas[i]) if metadatas and i < len(metadatas) else {}
            meta["content"] = documents[i]
            vectors.append({"id": doc_id, "values": emb, "metadata": meta})
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

    def _list_indexes_sync(self) -> list[str]:
        pc = self._get_client()
        indexes = pc.list_indexes()
        if hasattr(indexes, "indexes") and indexes.indexes:
            return [idx.name if hasattr(idx, "name") else str(idx) for idx in indexes.indexes]
        if hasattr(indexes, "__iter__"):
            return [idx.name if hasattr(idx, "name") else str(idx) for idx in indexes]
        return []

    async def list_collections(self, project_id: str = "") -> list[str]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self._list_indexes_sync)


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
