"""Item 01 — the gateway MCP failure classifier: every named case maps to a clean
(code, message), and the message NEVER leaks the raw cause (exit codes, upstream
HTML/bodies, internal hostnames, 'sandbox-agent logs')."""
import json

import httpx
import pytest

from ai_mesh_gateway import mcp_error_classifier as C


# ── process crash / OOM / start (exit codes + signals) ──
@pytest.mark.parametrize("exit_code,expect", [
    (-9, C.MCP_OUT_OF_MEMORY), (137, C.MCP_OUT_OF_MEMORY),
    (-6, C.MCP_SERVER_CRASHED), (134, C.MCP_SERVER_CRASHED),
    (-11, C.MCP_SERVER_CRASHED), (139, C.MCP_SERVER_CRASHED),
    (1, C.MCP_START_FAILED), (127, C.MCP_START_FAILED),
])
def test_exit_code_classification(exit_code, expect):
    code, msg = C.classify_mcp_failure(exit_code=exit_code)
    assert code == expect
    # NEVER leak the raw exit code number in the client message
    assert str(abs(exit_code)) not in msg or exit_code in (1,)  # '1' can appear in prose safely
    assert "exit" not in msg.lower() and "signal" not in msg.lower() and "sigabrt" not in msg.lower()


def test_oom_message_is_memory_worded():
    _, msg = C.classify_mcp_failure(exit_code=-9)
    assert "memory" in msg.lower()


# ── HTTP status (echoes the number, never the body) ──
@pytest.mark.parametrize("status,expect,in_msg", [
    (401, C.MCP_AUTH_FAILED, "re-auth"),
    (403, C.MCP_AUTH_FAILED, "re-auth"),
    (405, C.MCP_UPSTREAM_HTTP_ERROR, "HTTP 405"),
    (404, C.MCP_UPSTREAM_HTTP_ERROR, "HTTP 404"),
    (500, C.MCP_UPSTREAM_HTTP_ERROR, "HTTP 500"),
    (503, C.MCP_UPSTREAM_HTTP_ERROR, "HTTP 503"),
])
def test_http_status_classification(status, expect, in_msg):
    code, msg = C.classify_mcp_failure(status=status)
    assert code == expect
    assert in_msg.lower() in msg.lower()


# ── transport exceptions (DNS vs refused vs timeout) ──
def test_dns_failure():
    exc = httpx.ConnectError("[Errno -2] Name or service not known")
    code, msg = C.classify_mcp_failure(exc=exc)
    assert code == C.MCP_DNS_FAILURE
    assert "could not reach" in msg.lower()


def test_connection_refused():
    exc = httpx.ConnectError("[Errno 111] Connection refused")
    code, msg = C.classify_mcp_failure(exc=exc)
    assert code == C.MCP_CONNECTION_REFUSED
    assert "refused" in msg.lower()


def test_timeout_exception():
    exc = httpx.ConnectTimeout("timed out")
    code, msg = C.classify_mcp_failure(exc=exc)
    assert code == C.MCP_TIMEOUT


# ── raw-text fallback fingerprints ──
@pytest.mark.parametrize("raw,expect", [
    ("Unauthorized: invalid_token", C.MCP_AUTH_FAILED),
    ("egress denied by allowlist", C.MCP_EGRESS_DENIED),
    ("ENOSPC: no space left on device", C.MCP_INSUFFICIENT_STORAGE),
    ("process exited with code -9 (out of memory)", C.MCP_OUT_OF_MEMORY),
    ("process exited with code -11 (SIGSEGV, core dumped)", C.MCP_SERVER_CRASHED),
    ("manifest unknown: image not found", C.MCP_IMAGE_UNAVAILABLE),
    ("server did not respond, timed out", C.MCP_TIMEOUT),
    ("getaddrinfo failed for host", C.MCP_DNS_FAILURE),
    ("connection refused", C.MCP_CONNECTION_REFUSED),
    ("failed to start: missing host dependency", C.MCP_START_FAILED),
    ("something totally unexpected", C.MCP_UNAVAILABLE),
])
def test_raw_text_classification(raw, expect):
    code, _ = C.classify_mcp_failure(raw=raw)
    assert code == expect


# ── LEAK PREVENTION: the client message must never carry the raw cause ──
def test_message_never_leaks_raw_exception_or_host():
    exc = httpx.ConnectError("[Errno -2] getaddrinfo failed for secret-internal-host.corp.local:8931")
    code, msg = C.classify_mcp_failure(exc=exc)
    assert "secret-internal-host" not in msg
    assert "corp.local" not in msg
    assert "errno" not in msg.lower()
    assert "getaddrinfo" not in msg.lower()


def test_message_never_leaks_html_body_or_agent_logs():
    raw = ("<html><head><title>405 Not Allowed</title></head><body>nginx/1.25</body></html> "
           "sandbox-agent logs: /var/log/agent stderr tail")
    code, msg = C.classify_mcp_failure(raw=raw)
    assert "<html>" not in msg and "nginx" not in msg
    assert "sandbox-agent" not in msg.lower() and "stderr" not in msg.lower()


def test_sanitize_sync_returns_clean_body_with_ref():
    exc = httpx.ConnectError("[Errno 111] Connection refused by 10.1.2.3:9931")
    body = C.sanitize_mcp_error_sync(exc=exc, org_slug="zeroshield", server_slug="linear")
    assert set(body) == {"error", "code", "ref"}
    assert body["code"] == C.MCP_CONNECTION_REFUSED
    assert len(body["ref"]) == 12
    assert "10.1.2.3" not in body["error"] and "errno" not in body["error"].lower()


@pytest.mark.asyncio
async def test_sanitize_async_returns_clean_body_even_with_redis_down():
    # The best-effort Redis diagnostic write must NEVER break the error path:
    # _persist_diagnostic swallows its own failures, so with no Redis reachable
    # (default localhost:6379) sanitize_mcp_error still returns a clean body.
    body = await C.sanitize_mcp_error(status=405, org_slug="o", server_slug="s")
    assert body["code"] == C.MCP_UPSTREAM_HTTP_ERROR and "HTTP 405" in body["error"]
    assert len(body["ref"]) == 12


@pytest.mark.asyncio
async def test_diagnostic_write_uses_gateway_raw_key_and_full_cause(monkeypatch):
    # CLEANUP-05: the dev-only diagnostic must be written under the PLAIN key
    # mcp:diag:<ref> (JSON, 7d TTL) with the FULL raw cause — the exact key + format
    # control's staff-only endpoint reads back via _read_gateway_diagnostic. This
    # closes the write(gateway)↔read(control) loop the live check proved on the read side.
    import redis.asyncio as aioredis

    written = {}

    class _FakeRedis:
        async def set(self, key, val, ex=None):
            written.update(key=key, val=val, ex=ex)

        async def aclose(self):
            pass

    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: _FakeRedis())
    exc = httpx.ConnectError("[Errno -2] getaddrinfo failed: secret-internal-host.corp:8931")
    body = await C.sanitize_mcp_error(exc=exc, org_slug="zeroshield", server_slug="ruflo")

    assert written["key"] == f"mcp:diag:{body['ref']}"          # PLAIN key, not django-cache prefixed
    assert written["ex"] == C._DIAG_TTL_SECONDS                 # 7d TTL
    rec = json.loads(written["val"])
    assert rec["code"] == C.MCP_DNS_FAILURE and rec["ref"] == body["ref"]
    assert rec["org_slug"] == "zeroshield" and rec["server_slug"] == "ruflo"
    # the FULL cause (host + errno) is preserved SERVER-SIDE (dev diagnostic) ...
    assert "secret-internal-host.corp" in rec["raw_cause"]
    # ... but NEVER in the client body
    assert "secret-internal-host" not in json.dumps(body)
