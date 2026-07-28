"""RED-TEAM L5-01: the SSE ``endpoint`` event must not redirect our credentials off-host.

For ``transport: sse`` the sandbox agent learns its POST target from the UNTRUSTED upstream's
SSE ``endpoint`` event. The old reader accepted that value VERBATIM whenever it started with
``http`` (sse_manager.py, ``session.sse_messages_url = data if data.startswith("http")``), with
no allowlist check and no SSRF re-check — and ``_send_sse_jsonrpc_locked`` then POSTs to it with
``session.headers``, i.e. the operator's BYOK ``Authorization: Bearer <token>`` plus any custom
auth header, and every tool-call argument. A malicious or compromised MCP server harvested the
operator's upstream credentials just by naming its own collector host. (Red-team proof: an
attacker host received 3 POSTs carrying both credential headers.)

A second escape lived in the same branch: ``data.startswith("/")`` is ALSO true for a
protocol-relative ``//evil.example/x``, which ``urljoin(base, ...)`` resolves to a cross-host
absolute URL — so the "relative" branch was not safe either.

Every candidate now goes through ``_validated_messages_url``: the configured upstream's own host
is accepted (already validated at session creation), an operator-allowlisted host is accepted
after the same DNS/SSRF check, and anything else is REJECTED fail-closed with the previous target
left untouched.

Run: cd services/mcp-broker/sandbox-image && python -m pytest agent/tests/test_sse_endpoint_ssrf.py -q
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

SANDBOX_IMAGE = Path(__file__).resolve().parents[2]
if str(SANDBOX_IMAGE) not in sys.path:
    sys.path.insert(0, str(SANDBOX_IMAGE))

from agent import sse_manager  # noqa: E402
from agent.upstream_manager import UpstreamSession  # noqa: E402

UPSTREAM = "http://up.example/sse"
BASE = "http://up.example"


def _session(allowed: list[str] | None = None) -> UpstreamSession:
    return UpstreamSession(
        server_slug="srv",
        transport="sse",
        url=UPSTREAM,
        allowed_hosts=list(allowed or []),
        headers={"Authorization": "Bearer sk-operator-DO-NOT-LEAK"},
        client=None,
    )


def _validate(candidate: str, allowed: list[str] | None = None) -> str | None:
    return asyncio.run(
        sse_manager._validated_messages_url(candidate, _session(allowed), BASE)
    )


# ── the attack: an absolute cross-host endpoint ──────────────────────────────────────
@pytest.mark.parametrize("evil", [
    "http://evil.example/steal",
    "https://evil.example/steal",
    "http://evil.example:8080/steal",
    "http://127.0.0.2:42915/steal",          # the red-team's actual collector shape
    "http://up.example.evil.example/steal",  # suffix-confusion on the real host
    "http://xn--up-example/steal",           # punycode lookalike
])
def test_absolute_cross_host_endpoint_is_rejected(evil):
    assert _validate(evil) is None, f"credential-exfil target accepted: {evil}"


def test_protocol_relative_endpoint_is_rejected():
    """``//evil.example/x`` satisfies data.startswith('/') and urljoin() makes it cross-host."""
    from urllib.parse import urljoin
    candidate = urljoin(BASE, "//evil.example/steal")
    assert candidate.startswith("http://evil.example"), "precondition: urljoin escapes the host"
    assert _validate(candidate) is None


@pytest.mark.parametrize("scheme_url", [
    "javascript:alert(1)",
    "file:///etc/passwd",
    "ftp://evil.example/x",
    "",
])
def test_non_http_schemes_are_rejected(scheme_url):
    assert _validate(scheme_url) is None


# ── legitimate traffic must still work ───────────────────────────────────────────────
def test_same_host_absolute_is_accepted():
    url = "http://up.example/messages?sessionId=abc"
    assert _validate(url) == url


def test_same_host_relative_resolved_is_accepted():
    from urllib.parse import urljoin
    url = urljoin(BASE, "/messages?sessionId=abc")
    assert _validate(url) == url


def test_same_host_is_accepted_case_and_dot_insensitively():
    """Host normalisation must not turn a legitimate variant into a rejection."""
    assert _validate("http://UP.example./messages") == "http://UP.example./messages"


def test_operator_allowlisted_other_host_is_accepted(monkeypatch):
    """A DIFFERENT host the operator explicitly allowlisted is honoured (operator-selected),
    after the SSRF check — bypassed here the same way the agent allows internal dev hosts."""
    monkeypatch.setenv("MCP_AGENT_ALLOW_INTERNAL_HOSTS", "1")
    url = "http://msg.example/messages?sessionId=abc"
    assert _validate(url, allowed=["msg.example"]) == url


def test_non_allowlisted_host_still_rejected_even_with_internal_bypass(monkeypatch):
    """The env bypass only relaxes the SSRF/DNS check — it must NOT relax the allowlist."""
    monkeypatch.setenv("MCP_AGENT_ALLOW_INTERNAL_HOSTS", "1")
    assert _validate("http://evil.example/steal", allowed=["msg.example"]) is None
