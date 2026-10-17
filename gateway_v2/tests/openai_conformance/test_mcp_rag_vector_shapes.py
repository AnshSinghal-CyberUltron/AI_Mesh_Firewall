"""GW01 extra surfaces: MCP JSON-RPC, /v1/rag/*, /v1/vector/* error shapes.

Stock OpenAI SDK has no MCP/RAG/native-vector methods. These cells prove the
HTTP envelopes are JSON (never HTML) and that unauthenticated calls do not
leak stack traces. Against ASGI with lifespan off the native routers are
unmounted (404) — that is the recorded v1-ASGI baseline. Live TCP (:8300)
exercises the mounted routes.

**Disposition — this is a v1-arm conformance file (consistent with the
live-uvicorn disposition).** Its cells build the v1 FastAPI app
(``ai_mesh_gateway.main.app``) through the ``sdk_app`` / ``sdk_client`` fixtures,
which call ``T._make_sdk_app(...)`` (``T`` is ``test_openai_sdk_compat``). They
carry no ``build_app`` v2 wiring, so they target the V1_App and have no v2
equivalent to re-point at the shared ``wire_contract`` helper. Under a v2 run the
collection gate (``conftest.py``) skips every ``sdk_app`` / ``sdk_client`` cell
("v1-app cell; v2 wire contract proved by the v2 arm"); the v1 package
``ai_mesh_gateway`` is a separate CI job's dependency and is absent from this
venv, so these cells RUN only where the v1 package is installed (CI). The module
collects cleanly under both apps because the v1 import is lazy inside
``T._make_sdk_app`` (never at collection time).
"""

from __future__ import annotations

import json

import httpx
import openai
import pytest
import pytest_asyncio

from tests.openai_conformance import test_openai_sdk_compat as T

AUTH = {"Authorization": f"Bearer {T.API_KEY}"}
JSONRPC = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/list",
    "params": {},
}


@pytest_asyncio.fixture()
async def sdk_app(monkeypatch):
    app, auth_redis = await T._make_sdk_app(monkeypatch, redis_client=None)
    yield app
    await auth_redis.aclose()


@pytest_asyncio.fixture()
async def sdk_client(sdk_app):
    client = T._stock_client(sdk_app)
    yield client
    await client.close()


@pytest_asyncio.fixture()
async def raw(sdk_app):
    transport = httpx.ASGITransport(app=sdk_app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers=AUTH,
    ) as client:
        yield client


def _is_json_object(resp: httpx.Response) -> bool:
    ctype = resp.headers.get("content-type", "")
    if "json" not in ctype and resp.status_code not in {404, 405}:
        return False
    try:
        body = resp.json()
    except json.JSONDecodeError:
        return False
    return isinstance(body, dict)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["/v1/mcp", "/v1/rag/query", "/v1/vector/query"],
)
async def test_native_surfaces_unauthenticated_are_not_html(sdk_app, path: str) -> None:
    transport = httpx.ASGITransport(app=sdk_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(path, json=JSONRPC if path.endswith("/mcp") else {"q": "x"})
    assert "text/html" not in resp.headers.get("content-type", "")
    assert resp.status_code in {401, 403, 404, 405, 422}
    if resp.content:
        snippet = resp.text[:200].lower()
        assert "traceback" not in snippet
        assert "<html" not in snippet


@pytest.mark.asyncio
async def test_mcp_jsonrpc_authenticated_envelope_is_json(raw: httpx.AsyncClient) -> None:
    resp = await raw.post("/v1/mcp", json=JSONRPC)
    assert _is_json_object(resp) or resp.status_code in {404, 405}
    if resp.status_code < 500 and resp.content and "json" in resp.headers.get("content-type", ""):
        body = resp.json()
        assert "error" in body or "jsonrpc" in body or "detail" in body


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/rag/query", "/v1/rag/ingest", "/v1/vector/query"])
async def test_rag_vector_authenticated_envelope_is_json(
    raw: httpx.AsyncClient,
    path: str,
) -> None:
    resp = await raw.post(path, json={"query": "x", "collection": "c"})
    assert resp.status_code in {400, 401, 403, 404, 405, 422, 503}
    assert "text/html" not in resp.headers.get("content-type", "")
    if resp.content and "json" in resp.headers.get("content-type", ""):
        assert isinstance(resp.json(), dict)


@pytest.mark.asyncio
async def test_stock_sdk_has_no_mcp_or_rag_methods(sdk_client: openai.AsyncOpenAI) -> None:
    assert not hasattr(sdk_client, "mcp")
    assert not hasattr(sdk_client, "rag")
    with pytest.raises(openai.NotFoundError):
        await sdk_client.vector_stores.create(name="gw01-exfil")
