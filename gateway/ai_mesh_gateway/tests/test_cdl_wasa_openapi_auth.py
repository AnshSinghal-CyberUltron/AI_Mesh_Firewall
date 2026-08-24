"""CDL WASA #12 — gateway OpenAPI/docs must not be auth-exempt."""

from __future__ import annotations

from ai_mesh_gateway.middleware import EXCLUDED_PATHS


def test_gateway_docs_and_openapi_require_auth():
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert path not in EXCLUDED_PATHS, path


def test_health_still_auth_exempt():
    assert "/health" in EXCLUDED_PATHS
    assert "/v1/mcp/health" in EXCLUDED_PATHS
