"""admit/ deciding and rendering its own terminal outcome (kill-switch / quota)."""

from __future__ import annotations

from starlette.responses import Response


def killswitch_engaged() -> Response:
    return Response(b'{"error":"kill switch"}', 503, media_type="application/json")


def quota_exceeded() -> Response:
    return Response(b"{}", status_code=429 + 0)
