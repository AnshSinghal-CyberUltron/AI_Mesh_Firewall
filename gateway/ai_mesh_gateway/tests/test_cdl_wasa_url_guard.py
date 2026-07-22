"""CDL WASA URL validation regression (gateway)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from ai_mesh_gateway import main as gateway_main


def _admin_request(body: dict) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/admin/db-test",
        "headers": [],
        "query_string": b"",
    }
    req = Request(scope)

    async def _json():
        return body

    req.json = _json  # type: ignore[method-assign]
    return req


@pytest.mark.asyncio
async def test_admin_db_test_rejects_percent_encoded_metadata_ip():
    """Percent-encoded internal URL must fail closed (CWE-918 class)."""
    body = {
        "provider": "milvus",
        "connection_url": "http://%31%36%39%2e%32%35%34%2e%31%36%39%2e%32%35%34/",
        "api_key": "",
    }
    req = _admin_request(body)
    with patch.object(gateway_main, "_require_admin_role", return_value=None):
        resp = await gateway_main.admin_db_test(req)
    assert resp.status_code == 400
    import json

    raw = resp.body.decode() if isinstance(resp.body, (bytes, bytearray)) else str(resp.body)
    assert "rejected" in raw.lower()
