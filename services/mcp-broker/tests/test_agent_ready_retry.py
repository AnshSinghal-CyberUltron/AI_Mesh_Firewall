"""Unit tests for the sandbox agent cold-start readiness retry (bug #4).

_post_agent_rpc must tolerate the window where a freshly (re)started sandbox
container is "running" but its in-container HTTP agent has not bound its socket
yet: retry the POST with bounded backoff (re-ensuring between attempts) instead
of failing the first request, which the gateway surfaces as
"MCP sandbox is temporarily unavailable".

Run: PYTHONPATH=services/mcp-broker/src gateway/.venv/bin/python -m pytest \
       services/mcp-broker/tests/test_agent_ready_retry.py -o asyncio_mode=auto -q
"""

import httpx
import pytest

import sandbox.routes as routes


class _FakeResp:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code

    def json(self):
        return {"ok": True}


class _FakeClient:
    """Fails its POST `fail_until` times (agent booting), then returns 200."""

    calls = 0
    fail_until = 0

    def __init__(self, *_a, **_k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def post(self, _url, json=None):  # noqa: A002 - matches httpx signature
        type(self).calls += 1
        if type(self).calls <= type(self).fail_until:
            raise httpx.ConnectError("agent socket not ready")
        return _FakeResp(200)


class _FakeInfo:
    status = "running"
    agent_url = "http://sandbox:8790"


class _FakeDockerManager:
    def ensure(self, _org):
        return _FakeInfo()


def _install(monkeypatch, fail_until):
    _FakeClient.calls = 0
    _FakeClient.fail_until = fail_until
    monkeypatch.setattr(routes, "_AGENT_READY_BASE_DELAY", 0.0)
    monkeypatch.setattr(routes, "_AGENT_READY_MAX_DELAY", 0.0)
    monkeypatch.setattr(routes.httpx, "AsyncClient", _FakeClient)


@pytest.mark.asyncio
async def test_retries_until_agent_ready(monkeypatch):
    _install(monkeypatch, fail_until=3)  # succeeds on the 4th attempt
    resp = await routes._post_agent_rpc(
        _FakeDockerManager(), "org", "http://sandbox:8790/rpc", {"m": 1}, 5.0
    )
    assert resp.status_code == 200
    assert _FakeClient.calls == 4


@pytest.mark.asyncio
async def test_first_call_success_no_retry(monkeypatch):
    _install(monkeypatch, fail_until=0)
    resp = await routes._post_agent_rpc(
        _FakeDockerManager(), "org", "http://sandbox:8790/rpc", {"m": 1}, 5.0
    )
    assert resp.status_code == 200
    assert _FakeClient.calls == 1


@pytest.mark.asyncio
async def test_raises_after_exhausting_retries(monkeypatch):
    _install(monkeypatch, fail_until=999)  # never ready
    monkeypatch.setattr(routes, "_AGENT_READY_RETRIES", 4)
    with pytest.raises(httpx.HTTPError):
        await routes._post_agent_rpc(
            _FakeDockerManager(), "org", "http://sandbox:8790/rpc", {"m": 1}, 5.0
        )
    assert _FakeClient.calls == 4
