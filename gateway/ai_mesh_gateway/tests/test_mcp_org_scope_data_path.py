"""Regression: the MCP data-path (jsonrpc / tools/call / tools / health) must
resolve the tenant from the AUTHENTICATED key, never from the client-supplied
URL org_slug.

mcp_proxy._validate_org_scope is called first in every org MCP handler
(org_mcp_jsonrpc, org_mcp_tool_call, org_mcp_tools_list, org_mcp_server_health).
It must: 401 when unauthenticated, 403 when the key's org != URL org (cross-tenant
attempt), and pass (None) only on an exact org match. Verified live: an org-3
key on /gateway/acme-test/... returns 403 org_scope_violation, while no/invalid
key returns 401.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_proxy import _validate_org_scope  # noqa: E402


class _State:
    pass


class _Auth:
    def __init__(self, org_slug, prefix="k_test"):
        self.org_slug = org_slug
        self.prefix = prefix


class _Req:
    def __init__(self, auth):
        self.state = _State()
        if auth is not None:
            self.state.auth_context = auth


def test_missing_auth_returns_401():
    resp = _validate_org_scope(_Req(None), "zeroshield")
    assert resp is not None and resp.status_code == 401


def test_cross_tenant_returns_403():
    # authenticated as acme-test but URL path says zeroshield -> breach attempt
    resp = _validate_org_scope(_Req(_Auth("acme-test")), "zeroshield")
    assert resp is not None and resp.status_code == 403


def test_same_org_passes():
    resp = _validate_org_scope(_Req(_Auth("zeroshield")), "zeroshield")
    assert resp is None


def test_org_none_returns_403():
    # a key with no org_slug must not act on a named org's sandbox
    resp = _validate_org_scope(_Req(_Auth(None)), "zeroshield")
    assert resp is not None and resp.status_code == 403
