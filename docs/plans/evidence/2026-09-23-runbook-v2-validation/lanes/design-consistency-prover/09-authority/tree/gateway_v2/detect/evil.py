"""A detector that short-circuits with an HTTP response without importing edge/."""

from __future__ import annotations

from starlette.responses import PlainTextResponse, Response

FORBIDDEN = 403


class Blocked(Exception):
    """Raised by a detector; edge/ maps it to 403."""


def positional() -> Response:
    return Response(None, 403)                                   # positional status


def named() -> Response:
    return PlainTextResponse("no", status_code=FORBIDDEN)        # status via a Name


def five_hundred() -> Response:
    return Response(status_code=503)                             # 5xx is not covered


def short_circuit(text: str) -> None:
    if "x" in text:
        raise Blocked(text)                                      # raise = short-circuit
