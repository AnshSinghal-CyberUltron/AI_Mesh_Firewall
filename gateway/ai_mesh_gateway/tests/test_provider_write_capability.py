"""Executed evidence for the Milvus half-implemented-provider gap (#13).

Milvus is a SELECTABLE vector provider in the control plane
(policy/vector_provider_models.py: VECTOR_PROVIDER_CHOICES includes
("milvus", "Milvus")), and main._resolve_vector_client builds a real
MilvusClient for a milvus-configured org (on connection_url). But the
gateway's MilvusClient is QUERY-ONLY — it implements neither ``add`` (ingest)
nor ``delete``. Consequences proven elsewhere by code path:

* /v1/rag/ingest -> ``client.add(...)`` -> AttributeError -> caught -> a
  generic 500 "RAG ingestion failed." (indistinguishable from a transient
  error; the org retries forever).
* /v1/rag/documents DELETE -> the per-provider loop calls ``_client.delete``,
  AttributeError is caught, and the handler returns 200 ``deleted: 0`` — a
  FALSE SUCCESS. Against an externally-populated Milvus index (query works, so
  this is a real deployment) a GDPR-style erasure silently does nothing.

These tests lock the capability gap (root cause) and the provider-agnostic
``_provider_supports`` guard that makes the handlers fail LOUDLY instead.
"""
from vector_client import MilvusClient, PineconeClient, ChromaDBClient


def test_milvus_client_is_query_only():
    """Root cause: MilvusClient implements query/health but NOT add/delete."""
    m = MilvusClient(uri="http://milvus.invalid:19530")
    # Read path is implemented (query works — externally-populated index).
    assert callable(getattr(m, "query", None))
    assert callable(getattr(m, "health_check", None))
    # Write / lifecycle ops are ABSENT — the source of the ingest-500 and the
    # delete false-success.
    assert not callable(getattr(m, "add", None)), "MilvusClient unexpectedly has add()"
    assert not callable(getattr(m, "delete", None)), "MilvusClient unexpectedly has delete()"
    assert not callable(getattr(m, "create_collection", None))
    assert not callable(getattr(m, "delete_collection", None))


def test_pinecone_and_chroma_support_write():
    """Contrast: the fully-implemented providers DO expose add + delete."""
    p = PineconeClient(api_key="fake-key")
    c = ChromaDBClient(url="http://chroma.invalid:8000")
    for client in (p, c):
        assert callable(getattr(client, "add", None))
        assert callable(getattr(client, "delete", None))


def test_reranker_is_pinecone_only_silent_noop_elsewhere():
    """#14 evidence: reranker_model is offered for EVERY provider in the frontend
    (VectorProviderConfigPanel renders the field unconditionally; the serializer
    stores it; VectorProviderConfig.to_gateway_dict ships it) — but only
    PineconeClient implements rerank()/_reranker_model. Chroma/Milvus clients
    have NEITHER, so the retriever's ``hasattr(client, "rerank") and
    client._reranker_model`` guard is False and a configured reranker is a SILENT
    no-op (retrieved docs never reordered). Root cause lives here at the client
    layer; the FIX (reject reranker on non-Pinecone in the control-plane
    serializer + gate the frontend field) is BLOCKED from verification this
    session (Django not installed; frontend has no test harness)."""
    p = PineconeClient(api_key="fake", reranker_model="bge-reranker-v2-m3")
    c = ChromaDBClient(url="http://chroma.invalid:8000")
    m = MilvusClient(uri="http://milvus.invalid:19530")
    # Pinecone: reranker is real (method + configured model attribute).
    assert callable(getattr(p, "rerank", None))
    assert getattr(p, "_reranker_model", "") == "bge-reranker-v2-m3"
    # Chroma + Milvus: NO rerank method and NO _reranker_model -> the retriever
    # skips reranking silently even when the org configured one.
    for client in (c, m):
        assert not callable(getattr(client, "rerank", None))
        assert not getattr(client, "_reranker_model", "")
    # ChromaDBClient.__init__ doesn't even accept reranker_model (so the resolver
    # cannot wire it through) — proves the gap is structural, not a passthrough bug.
    import inspect
    assert "reranker_model" not in inspect.signature(ChromaDBClient.__init__).parameters
    assert "reranker_model" in inspect.signature(PineconeClient.__init__).parameters


def test_provider_supports_instance_guard():
    """The instance-level guard used at the sync add site + delete loop."""
    import main
    m = MilvusClient(uri="http://milvus.invalid:19530")
    p = PineconeClient(api_key="fake")
    c = ChromaDBClient(url="http://chroma.invalid:8000")
    assert main._provider_supports(p, "add") is True
    assert main._provider_supports(p, "delete") is True
    assert main._provider_supports(c, "add") is True
    assert main._provider_supports(c, "delete") is True
    # query-only Milvus -> blocked (this is what stops the ingest-500 /
    # delete false-success)
    assert main._provider_supports(m, "add") is False
    assert main._provider_supports(m, "delete") is False


def test_provider_type_supports_classlevel_guard():
    """The class-level pre-branch guard (no instance/executor constructed)."""
    import main
    # milvus class lacks add/delete -> pre-branch ingest guard returns 501
    assert main._provider_type_supports("milvus", "add") is False
    assert main._provider_type_supports("milvus", "delete") is False
    # fully-implemented providers pass
    assert main._provider_type_supports("pinecone", "add") is True
    assert main._provider_type_supports("chroma", "delete") is True
    # 'custom' -> supported at class level (http URL resolves to Chroma); the
    # instance-level guard blocks a concrete non-http Milvus at the call site
    assert main._provider_type_supports("custom", "add") is True
    # unknown / env-default provider -> do NOT pre-block (call site guards)
    assert main._provider_type_supports("", "add") is True
    assert main._provider_type_supports("bogus", "add") is True
    # case-insensitive
    assert main._provider_type_supports("MILVUS", "add") is False
