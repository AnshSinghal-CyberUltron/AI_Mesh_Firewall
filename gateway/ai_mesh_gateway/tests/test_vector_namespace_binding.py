"""Executed proof: a CLIENT-SUPPLIED `namespace` cannot override the
tenant-bound ``{project_id}__{collection}`` namespace.

VECTOR SECURITY (cross-namespace / unauthorized-namespace-access): the RAG query
API accepts a ``namespace`` body field (main.py ~10689) and the Semantic Search
UI sends it (SemanticSearchPanel.jsx:52). But the Pinecone/Chroma clients derive
the actual namespace/collection SOLELY from the immutable org-bound
``project_id`` and IGNORE the caller's namespace. Consequences:

* SECURITY (the property these tests lock): a hostile ``namespace`` cannot
  redirect a query into another tenant's data — the effective namespace is
  always ``{project_id}__{collection}``.
* FUNCTIONAL (#15, LOW): the ``namespace`` input is therefore a silent no-op —
  a user-facing control that does nothing (ingest ignores it too, so data is
  never namespace-partitioned). Fix is frontend (remove/relabel the field),
  BLOCKED from verification this session (no frontend test harness).
"""
import pytest

from vector_client import PineconeClient, ChromaDBClient


async def test_pinecone_query_ignores_caller_namespace_uses_tenant_binding(monkeypatch):
    captured: dict = {}

    class _FakeIndex:
        def query(self, **kwargs):
            captured.update(kwargs)
            return {"matches": []}

    class _FakePC:
        def Index(self, name):
            captured["index_name"] = name
            return _FakeIndex()

    client = PineconeClient(api_key="fake")
    monkeypatch.setattr(client, "_get_client", lambda: _FakePC())
    # avoid a real embedding call (litellm); the vector value is irrelevant here
    monkeypatch.setattr(client, "_embed", lambda texts, input_type="query": [[0.1, 0.2, 0.3]])

    await client.query(
        collection_name="docs",
        query_text="hello",
        n_results=5,
        namespace="attacker-controlled-ns",   # hostile client-supplied namespace
        project_id="org42-proj",
    )
    # The ACTUAL Pinecone namespace is the tenant binding, NEVER the caller's.
    assert captured["namespace"] == "org42-proj__docs"
    assert captured["namespace"] != "attacker-controlled-ns"


async def test_chroma_query_ignores_caller_namespace_uses_tenant_collection(monkeypatch):
    captured: dict = {}

    class _FakeCollection:
        def query(self, **kwargs):
            captured["query_kwargs"] = kwargs
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    class _FakeChroma:
        def get_collection(self, name):
            captured["collection_name"] = name
            return _FakeCollection()

    client = ChromaDBClient(url="http://chroma.invalid:8000")
    monkeypatch.setattr(client, "_get_client", lambda: _FakeChroma())

    await client.query(
        collection_name="docs",
        query_text="hello",
        n_results=5,
        namespace="attacker-controlled-ns",
        project_id="org42-proj",
    )
    # Chroma isolates by COLLECTION NAME = the tenant binding, never the caller's ns.
    assert captured["collection_name"] == "org42-proj__docs"
    assert "attacker-controlled-ns" not in captured["collection_name"]
