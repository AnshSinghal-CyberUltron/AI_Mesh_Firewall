"""BYOK-aware RAG collection helper tests."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_mesh_gateway.rag_collections import (
    coerce_org_id,
    iter_vector_clients_for_org,
    no_provider_configured_payload,
    rag_vector_available,
    resolve_vector_client_for_org,
)


class _FakeProviderSync:
    def __init__(self, providers: list[dict] | None = None):
        self._providers = providers or []

    def get_org_providers(self, org_id):
        return self._providers

    def get_provider_config(self, org_id, provider_type):
        for cfg in self._providers:
            if cfg.get("provider_type") == provider_type:
                return cfg
        return None


class _FakeClient:
    def __init__(self, collections: list[str] | None = None):
        self._collections = collections or []

    async def list_collections(self, project_id: str):
        return list(self._collections)


def test_coerce_org_id_parses_numeric_string():
    assert coerce_org_id("42") == 42
    assert coerce_org_id(7) == 7
    assert coerce_org_id("") is None
    assert coerce_org_id(None) is None


def test_no_provider_configured_payload_shape():
    payload = no_provider_configured_payload("acme")
    assert payload["project_id"] == "acme"
    assert payload["collections"] == {}
    assert payload["rag_available"] is False
    assert payload["reason"] == "no_provider_configured"


def test_rag_vector_available_false_when_empty():
    assert rag_vector_available(
        1,
        vector_clients={},
        provider_sync=_FakeProviderSync([]),
    ) is False


def test_iter_vector_clients_uses_env_clients():
    client = _FakeClient()
    clients = iter_vector_clients_for_org(
        None,
        vector_clients={"pinecone": client},
        provider_sync=None,
    )
    assert clients == [("pinecone", client)]


def test_resolve_vector_client_for_org_falls_back_to_env():
    client = _FakeClient()
    resolved, ptype = resolve_vector_client_for_org(
        "pinecone",
        None,
        vector_clients={"pinecone": client},
        provider_sync=None,
    )
    assert resolved is client
    assert ptype == "pinecone"


@pytest.mark.asyncio
async def test_list_collections_for_clients_normalizes_namespace():
    from ai_mesh_gateway.rag_collections import list_collections_for_clients

    client = _FakeClient(["demo__docs", "other"])
    result = await list_collections_for_clients("demo", [("pinecone", client)])
    assert result == {"pinecone": ["docs", "other"]}


@pytest.mark.asyncio
async def test_admin_list_soft_empty_via_helpers():
    """Regression: empty VECTOR_CLIENTS + no org providers must not imply 503."""
    clients = iter_vector_clients_for_org(
        99,
        vector_clients={},
        provider_sync=_FakeProviderSync([]),
    )
    assert clients == []
    payload = no_provider_configured_payload("zeroshield")
    assert payload["rag_available"] is False
