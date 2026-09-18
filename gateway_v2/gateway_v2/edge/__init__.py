"""HTTP/SSE edge. The only layer that may assemble ASGI and error envelopes."""

from gateway_v2.edge.app import app

__all__ = ("app",)
