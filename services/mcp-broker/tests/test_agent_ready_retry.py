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


class _FakeStreamResp:
    """A streaming response body the CHG-0151 capped reader consumes via aiter_bytes()."""

    def __init__(self, status_code: int = 200):
        self.status_code = status_code
        self.headers = {}
        self.request = httpx.Request("POST", "http://sandbox:8790/rpc")

    async def aiter_bytes(self):
        yield b'{"ok": true}'


class _FakeStreamCtx:
    def __init__(self, client, headers):
        self._client = client
        self._headers = headers

    async def __aenter__(self):
        cls = type(self._client)
        cls.calls += 1
        cls.last_headers = self._headers
        # Cold-start: the agent socket isn't bound yet -> ConnectError on stream open,
        # exactly the transport error _post_agent_rpc retries on.
        if cls.calls <= cls.fail_until:
            raise httpx.ConnectError("agent socket not ready")
        return _FakeStreamResp(200)

    async def __aexit__(self, *_a):
        return False


class _FakeClient:
    """Fails its stream-open `fail_until` times (agent booting), then returns 200.

    CHG-0151: _post_agent_rpc now STREAMS the reply (client.stream) with a byte cap instead
    of client.post, so the fake exposes stream() (an async CM) rather than post().
    """

    calls = 0
    fail_until = 0
    last_headers = None  # CHG-0121: capture the forwarded headers for the trace test

    def __init__(self, *_a, **_k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    def stream(self, _method, _url, json=None, headers=None):  # noqa: A002 - matches httpx
        return _FakeStreamCtx(self, headers)


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


# ── CHG-0121: forward the X-Request-ID correlation id to the in-container agent (last
# trace hop), so the trace is continuous gateway → broker → sandbox agent.


@pytest.mark.asyncio
async def test_forwards_x_request_id_header_to_agent(monkeypatch):
    _install(monkeypatch, fail_until=0)
    await routes._post_agent_rpc(
        _FakeDockerManager(), "org", "http://sandbox:8790/rpc", {"m": 1}, 5.0,
        request_id="trace-agent-7",
    )
    assert _FakeClient.last_headers == {"X-Request-ID": "trace-agent-7"}


@pytest.mark.asyncio
async def test_no_request_id_sends_no_header(monkeypatch):
    _install(monkeypatch, fail_until=0)
    await routes._post_agent_rpc(
        _FakeDockerManager(), "org", "http://sandbox:8790/rpc", {"m": 1}, 5.0,
    )
    assert _FakeClient.last_headers is None  # no spurious empty header


# ── CHG-0136: opt-in broker→agent key (X-Sandbox-Agent-Key) — second cross-tenant
# isolation layer the agent verifies; attached only when MCP_AGENT_INTERNAL_KEY is set.


@pytest.mark.asyncio
async def test_sends_agent_key_header_when_configured(monkeypatch):
    monkeypatch.setenv("MCP_AGENT_INTERNAL_KEY", "broker-key-xyz")
    _install(monkeypatch, fail_until=0)
    await routes._post_agent_rpc(
        _FakeDockerManager(), "org", "http://sandbox:8790/rpc", {"m": 1}, 5.0,
        request_id="trace-9",
    )
    assert _FakeClient.last_headers == {
        "X-Request-ID": "trace-9",
        "X-Sandbox-Agent-Key": "broker-key-xyz",
    }


@pytest.mark.asyncio
async def test_no_agent_key_header_when_unset(monkeypatch):
    monkeypatch.delenv("MCP_AGENT_INTERNAL_KEY", raising=False)
    _install(monkeypatch, fail_until=0)
    await routes._post_agent_rpc(
        _FakeDockerManager(), "org", "http://sandbox:8790/rpc", {"m": 1}, 5.0,
    )
    assert _FakeClient.last_headers is None  # unset + no request_id -> no headers at all
