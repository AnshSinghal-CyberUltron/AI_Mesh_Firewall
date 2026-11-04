"""Gateway routes that gateway-v2 does not implement, and the 410 answer for them (GW35).

With ``AMF_GATEWAY_BACKEND=v1`` (the default) nothing here changes behaviour. With ``v2`` the
control proxies answer these paths with HTTP 410 ``retired_on_gateway_v2`` instead of forwarding
to a gateway that cannot serve them, so the console shows a labelled "retired" panel and not a
spinner or an invented success. The table is checked against the gateway's admin ledger by
``gateway_v2/tests/lint/test_retired_routes_ledger.py``.
"""
from __future__ import annotations

import logging
from types import MappingProxyType

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework.response import Response

logger = logging.getLogger(__name__)

RETIRED_CODE = "retired_on_gateway_v2"
_BREAKER = "gateway-v2 sheds with CoDel and has no Redis circuit breaker."

#: gateway path (as the proxies call it) -> why it is retired.
RETIRED_ON_V2 = MappingProxyType(
    {
        "/v1/admin/circuit-breaker-state": _BREAKER,
        "/v1/admin/circuit-breaker-trigger": _BREAKER,
        "/v1/admin/circuit-breaker-reset": _BREAKER,
        "/v1/admin/bedrock-test": "gateway-v2 has no Bedrock guard to probe.",
        "/v1/admin/db-test": "gateway-v2 has no vector database client; GW18 owns that test.",
        "/v1/mcp/internal/discover-tools": "gateway-v2 serves MCP at /v1/mcp (GW17), not here.",
        "/v1/mcp/internal/tools-call": "gateway-v2 serves MCP at /v1/mcp (GW17), not here.",
        "/v1/mcp/internal/oauth-token-mirror": "gateway-v2 keeps no mirrored OAuth tokens.",
    }
)


def gateway_backend() -> str:
    """``v1`` or ``v2``. Anything else raises: there is no fallback."""
    value = getattr(settings, "AMF_GATEWAY_BACKEND", "v1")
    if value not in ("v1", "v2"):
        raise ImproperlyConfigured(f"AMF_GATEWAY_BACKEND must be 'v1' or 'v2', got {value!r}")
    return value


def is_retired(gateway_path: str) -> bool:
    """True when the backend is v2 and ``gateway_path`` is not served by it."""
    return gateway_backend() == "v2" and gateway_path in RETIRED_ON_V2


def retired_response(gateway_path: str) -> Response:
    """HTTP 410 in the proxy envelope. Pure; makes no network call."""
    logger.info("retired_on_gateway_v2 path=%s", gateway_path)
    return Response(
        {
            "status": "error",
            "data": {
                "code": RETIRED_CODE,
                "message": RETIRED_ON_V2.get(gateway_path, "Retired on gateway-v2."),
                "path": gateway_path,
            },
        },
        status=410,
    )
