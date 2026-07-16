"""Per-collection embedding model.

A vector provider config carries ONE org-default embedding model, but a
per-collection VectorCollectionPolicy may pin its own so one org can serve
indexes of different dimensions/models (e.g. a 1024-dim e5 collection and a
1536-dim OpenAI collection). ``_resolve_vector_client`` must let that
``policy.embedding_model`` override the provider default when supplied, while
still taking the org's CREDENTIALS (provider api_key + embedding_api_key) from
the provider config. Empty/None override → provider default (exact prior
behaviour, so the 2-arg call is unchanged).
"""
from __future__ import annotations

import main


class _Sync:
    def __init__(self, cfg):
        self.cfg = cfg

    def get_provider_config(self, org_id, provider_type):
        if provider_type == self.cfg.get("provider_type", "pinecone"):
            return self.cfg
        return None


def _cfg():
    return {
        "is_active": True,
        "provider_type": "pinecone",
        "api_key": "prov-key",
        "embedding_model": "multilingual-e5-large",
        "embedding_api_key": "embed-key",
    }


def test_override_replaces_provider_default(monkeypatch):
    monkeypatch.setattr(main, "VECTOR_PROVIDER_SYNC", _Sync(_cfg()))
    client, ptype = main._resolve_vector_client(
        "pinecone", org_id=1, embedding_model_override="llama-text-embed-v2"
    )
    assert ptype == "pinecone"
    assert client._embedding_model == "llama-text-embed-v2"
    # Credentials still resolve from the provider config, NOT the override.
    assert client._api_key == "prov-key"
    assert client._embedding_api_key == "embed-key"


def test_empty_or_blank_override_falls_back_to_provider_default(monkeypatch):
    monkeypatch.setattr(main, "VECTOR_PROVIDER_SYNC", _Sync(_cfg()))
    for override in (None, "", "   "):
        client, _ = main._resolve_vector_client(
            "pinecone", org_id=1, embedding_model_override=override
        )
        assert client._embedding_model == "multilingual-e5-large", override


def test_two_arg_call_is_unchanged(monkeypatch):
    # Back-compat: callers that never pass the override (e.g. the delete path)
    # keep the provider-default embedding model.
    monkeypatch.setattr(main, "VECTOR_PROVIDER_SYNC", _Sync(_cfg()))
    client, _ = main._resolve_vector_client("pinecone", org_id=1)
    assert client._embedding_model == "multilingual-e5-large"


def test_two_collections_get_distinct_models(monkeypatch):
    # The same org/provider resolves DIFFERENT embedding models for two
    # collections whose policies pin different models — the core contract.
    monkeypatch.setattr(main, "VECTOR_PROVIDER_SYNC", _Sync(_cfg()))
    c1, _ = main._resolve_vector_client("pinecone", org_id=1, embedding_model_override="multilingual-e5-large")
    c2, _ = main._resolve_vector_client("pinecone", org_id=1, embedding_model_override="llama-text-embed-v2")
    assert c1._embedding_model == "multilingual-e5-large"
    assert c2._embedding_model == "llama-text-embed-v2"
    assert c1 is not c2
