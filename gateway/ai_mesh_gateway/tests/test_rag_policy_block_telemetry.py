"""Early RAG policy denials must emit rag_* block telemetry for M2.1 lane attribution."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import httpx
import pytest
import pytest_asyncio

from ai_mesh_gateway import main as gateway_main
from ai_mesh_gateway import middleware as gw_middleware
from ai_mesh_gateway.tests import test_openai_sdk_compat as sdk_compat


@pytest.fixture
def auth_ctx():
    return SimpleNamespace(
        user_id=42,
        prefix="zs_test_",
        organization_id="org-1",
        org_slug="zeroshield",
    )


def test_emit_rag_policy_block_telemetry_query(monkeypatch, auth_ctx):
    captured: list[dict] = []

    def _capture(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(gateway_main, "_emit_telemetry", _capture)
    gateway_main._emit_rag_policy_block_telemetry(
        auth_ctx=auth_ctx,
        collection_name="customer_docs",
        project_id="proj-1",
        operation="query",
        code="rag_access_denied",
        detail="Access denied: no policy for collection 'customer_docs'.",
        vector_db_type="pinecone",
    )

    assert len(captured) == 1
    evt = captured[0]
    assert evt["status_code"] == 403
    assert evt["event_type"] == "rag_query_blocked"
    assert evt["action"] == "block"
    assert evt["metadata"]["collection"] == "customer_docs"
    assert evt["metadata"]["code"] == "rag_access_denied"
    assert evt["metadata"]["source"] == "rag"


def test_emit_rag_policy_block_telemetry_ingest(monkeypatch, auth_ctx):
    captured: list[dict] = []

    monkeypatch.setattr(gateway_main, "_emit_telemetry", lambda **kw: captured.append(kw))
    gateway_main._emit_rag_policy_block_telemetry(
        auth_ctx=auth_ctx,
        collection_name="customer_docs",
        project_id="proj-1",
        operation="insert",
        code="rag_access_denied",
        detail="denied",
    )

    assert captured[0]["event_type"] == "rag_ingest_blocked"


@pytest_asyncio.fixture()
async def rag_app(monkeypatch):
    auth_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    key_hash = hashlib.sha256(sdk_compat.API_KEY.encode("utf-8")).hexdigest()
    await auth_redis.set(f"auth:apikey:{key_hash}", json.dumps(sdk_compat._auth_payload()))

    async def _get_redis(self):
        return auth_redis

    monkeypatch.setattr(gw_middleware.AuthMiddleware, "_get_redis", _get_redis)

    config_sync = MagicMock()
    config_sync.get_config = MagicMock(return_value=dict(sdk_compat.TEST_CONFIG))
    config_sync.get_model_routing = MagicMock(return_value=[])
    config_sync.reload_models_now = AsyncMock()

    vector_policy_sync = MagicMock()
    vector_policy_sync.get_policy = MagicMock(return_value=None)

    monkeypatch.setattr(gateway_main, "CONFIG", dict(sdk_compat.TEST_CONFIG))
    monkeypatch.setattr(gateway_main, "CONFIG_SYNC", config_sync)
    monkeypatch.setattr(gateway_main, "LLM_ROUTER", MagicMock())
    monkeypatch.setattr(gateway_main, "RAG_PIPELINE", MagicMock())
    monkeypatch.setattr(gateway_main, "VECTOR_POLICY_SYNC", vector_policy_sync)
    monkeypatch.setattr(gateway_main, "REDIS_CLIENT", None)
    monkeypatch.setattr(gateway_main, "_alert_vector_policy_miss", lambda **_kw: None)
    monkeypatch.setattr(
        gateway_main,
        "_enforce_org_tpm_rate_limit",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        gateway_main,
        "_enforce_org_burst_rpm",
        AsyncMock(return_value=None),
    )

    yield gateway_main.app
    await auth_redis.aclose()


@pytest.mark.asyncio
async def test_rag_query_unknown_collection_emits_rag_query_blocked(rag_app, monkeypatch):
    captured: list[dict] = []

    def _capture(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(gateway_main, "_emit_telemetry", _capture)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=rag_app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {sdk_compat.API_KEY}"},
    ) as client:
        resp = await client.post(
            "/v1/rag/query",
            json={
                "collection": "customer_docs",
                "query": "what is our refund policy?",
                "vector_db_type": "pinecone",
            },
        )

    assert resp.status_code == 403
    assert resp.json().get("code") == "rag_access_denied"

    rag_blocks = [e for e in captured if e.get("event_type") == "rag_query_blocked"]
    assert len(rag_blocks) == 1
    assert rag_blocks[0]["action"] == "block"
    assert rag_blocks[0]["metadata"]["collection"] == "customer_docs"
    assert rag_blocks[0]["metadata"]["code"] == "rag_access_denied"
