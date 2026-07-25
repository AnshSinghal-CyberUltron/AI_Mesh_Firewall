"""LANE T2/SB1 (lifecycle red-team wf_a29f8f21): the stdio broker path
(broker_send_jsonrpc) must NOT retry a non-idempotent tools/call after the request
was dispatched — a post-dispatch failure may mean the tool ALREADY executed upstream,
so retrying double-executes the side effect. Parity with broker_send_rpc (ws/http/sse).
"""
import httpx
import pytest

import mcp_sandbox_client as sc


@pytest.fixture(autouse=True)
def _broker_key(monkeypatch):
    monkeypatch.setenv("MCP_BROKER_INTERNAL_KEY", "test-broker-key")


class _Stub:
    """AsyncClient stub: first .request raises, later ones return 200."""
    def __init__(self, exc, ok_after=1):
        self._exc = exc
        self._ok_after = ok_after
        self.calls = 0

    async def request(self, method, url, json=None, headers=None):
        self.calls += 1
        if self.calls <= self._ok_after:
            raise self._exc
        return httpx.Response(200, json={"result": "ok"}, request=httpx.Request(method, url))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


_CFG = {"server_slug": "mailer", "command": "node", "args": [], "env_vars": {}}


@pytest.mark.asyncio
async def test_stdio_toolcall_not_retried_after_dispatch(monkeypatch):
    # A ReadTimeout is a POST-dispatch failure (not pre-send) -> the tool may have run.
    stub = _Stub(httpx.ReadTimeout("read timed out"))
    monkeypatch.setattr(sc.httpx, "AsyncClient", lambda *a, **k: stub)
    with pytest.raises(RuntimeError, match="double execution"):
        await sc.broker_send_jsonrpc("orgA", _CFG, "tools/call", {"name": "send_email"})
    assert stub.calls == 1, f"tools/call was dispatched {stub.calls}x — double-execution"


@pytest.mark.asyncio
async def test_stdio_toolslist_still_retried_after_dispatch(monkeypatch):
    # tools/list IS idempotent -> a post-dispatch failure retries and succeeds.
    stub = _Stub(httpx.ReadTimeout("read timed out"), ok_after=1)
    monkeypatch.setattr(sc.httpx, "AsyncClient", lambda *a, **k: stub)
    out = await sc.broker_send_jsonrpc("orgA", _CFG, "tools/list", {})
    assert out == {"result": "ok"}
    assert stub.calls == 2, "idempotent tools/list should retry once after a transient failure"


@pytest.mark.asyncio
async def test_stdio_toolcall_pre_send_error_still_retried(monkeypatch):
    # A pre-send ConnectError means the tool could NOT have run -> safe to retry.
    stub = _Stub(httpx.ConnectError("connection refused"), ok_after=1)
    monkeypatch.setattr(sc.httpx, "AsyncClient", lambda *a, **k: stub)
    monkeypatch.setattr(sc, "_sleep_backoff", lambda *_a, **_k: _noop())
    out = await sc.broker_send_jsonrpc("orgA", _CFG, "tools/call", {"name": "send_email"})
    assert out == {"result": "ok"}
    assert stub.calls == 2, "a pre-send failure on tools/call is safe to retry"


async def _noop():
    return None
