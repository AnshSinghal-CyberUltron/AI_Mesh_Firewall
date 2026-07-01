"""Regression: MCP OAuth start/status must require auth + matching org scope.

These routes previously bypassed authentication entirely (a substring match in the
gateway middleware) while taking ``org_slug`` from the URL path — so any caller
could, with no key, probe a victim org's token existence (status oracle) or start
an OAuth flow under the victim's token namespace (start). Confirmed as a live
Org-B→Org-A cross-org breach. ``_require_org_scope`` is the gate now applied to
both handlers; the provider→gateway ``/gateway/oauth/callback`` redirect is the
ONLY OAuth route that stays unauthenticated (validated by its signed flow state).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_oauth_proxy import _require_org_scope  # noqa: E402


class _State:
    pass


class _Auth:
    def __init__(self, org_slug):
        self.org_slug = org_slug


class _Req:
    def __init__(self, auth):
        self.state = _State()
        if auth is not None:
            self.state.auth_context = auth


def test_missing_auth_returns_401():
    resp = _require_org_scope(_Req(None), "org-a")
    assert resp is not None and resp.status_code == 401


def test_cross_org_returns_403():
    # authenticated as org-b but URL path says org-a -> breach attempt
    resp = _require_org_scope(_Req(_Auth("org-b")), "org-a")
    assert resp is not None and resp.status_code == 403


def test_same_org_passes():
    resp = _require_org_scope(_Req(_Auth("org-a")), "org-a")
    assert resp is None


def test_auth_context_present_but_org_none_returns_403():
    # a key with no org_slug must not be able to act on a named org's namespace
    resp = _require_org_scope(_Req(_Auth(None)), "org-a")
    assert resp is not None and resp.status_code == 403
