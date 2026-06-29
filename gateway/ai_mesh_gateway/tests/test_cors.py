"""CORS preflight tests for cross-origin browser calls to /v1/*."""
from __future__ import annotations

import importlib

import pytest
from starlette.testclient import TestClient

_FIREWALL_ORIGIN = "https://aimeshfirewall.zeroshield.ai"


@pytest.fixture()
def cors_client(monkeypatch):
    """Reload gateway app with prod CORS allow-list for isolated preflight checks."""
    monkeypatch.setenv("GATEWAY_CORS_ORIGINS", _FIREWALL_ORIGIN)
    monkeypatch.setenv("FRONTEND_ORIGIN", _FIREWALL_ORIGIN)
    import ai_mesh_gateway.main as main_mod

    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_options_preflight_allows_firewall_origin(cors_client):
    res = cors_client.options(
        "/v1/chat/completions",
        headers={
            "Origin": _FIREWALL_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == _FIREWALL_ORIGIN


def test_options_preflight_allows_accept_header_for_sse(cors_client):
    res = cors_client.options(
        "/v1/chat/completions",
        headers={
            "Origin": _FIREWALL_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,accept",
        },
    )
    assert res.status_code == 200
    allowed = res.headers.get("access-control-allow-headers", "").lower()
    assert "accept" in allowed
