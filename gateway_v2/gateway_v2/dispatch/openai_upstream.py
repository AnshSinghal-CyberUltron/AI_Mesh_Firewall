"""The OpenAI-compatible generation upstream (GW36 S1).

:class:`OpenAIUpstreamProvider` is the production
:class:`~gateway_v2.dispatch.provider.ProviderClient` for an OpenAI-compatible chat endpoint
(the organization's provider / BYOK, or the T02 recorder in the local lab). It posts the
request ``edge`` rendered from the VERIFIED, masked deltas (``body[BODY_OPENAI_KEY]``, see
``edge/upstream_body.py``) and never the internal ``Transform`` pack: a body without the
rendered request posts nothing.

Failures the gateway itself detects end the stream with a typed ``UpstreamEvent.gateway_error``
(a declared code ``edge`` renders through the error envelope), never as content and never as an
empty success:

* a non-2xx status -> the declared ``upstream_*`` code for that status (the provider's error body
  is not read and not echoed; its own 401/403 is ``upstream_auth_error``, a gateway fault);
* a 2xx that is not ``text/event-stream``, an undecodable event, residual bytes, or an end of body
  without ``[DONE]`` -> ``STREAM_MALFORMED_UPSTREAM``;
* a connect / read failure -> ``upstream_unavailable``; a timeout -> ``upstream_timeout``.

A provider's own mid-stream ``{"error": ...}`` event keeps the GW12 R11 path (``error_frame``,
scanned before any byte of it is forwarded).

One pooled ``httpx.AsyncClient`` serves every request of a worker; it is created inside the running
event loop on first use and closed by :meth:`aclose` (lifespan shutdown). A request's upstream read
is cut by closing its iterator (a cancelled or closed consumer), which exits the stream context;
:meth:`abort` therefore has nothing of its own to cut and is a no-op, so one request can never cut
another's stream.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.domain.posture import STREAM_MALFORMED_UPSTREAM
from gateway_v2.egress.stream import DownstreamFrame

if TYPE_CHECKING:
    import httpx

__all__ = (
    "BODY_ENDPOINT_KEY",
    "BODY_OPENAI_KEY",
    "CHAT_ENDPOINT",
    "OpenAIUpstreamProvider",
    "httpx_client_factory",
    "status_code",
)

#: ``UpstreamRequest.body`` keys under which ``edge`` puts the rendered OpenAI request and the
#: endpoint path it is posted to (relative to the provider base URL).
BODY_OPENAI_KEY = "openai_body"
BODY_ENDPOINT_KEY = "openai_endpoint"
CHAT_ENDPOINT = "chat/completions"

UPSTREAM_REQUEST_UNBUILDABLE = "upstream_request_unbuildable"
UPSTREAM_UNAVAILABLE = "upstream_unavailable"
UPSTREAM_TIMEOUT = "upstream_timeout"
_UPSTREAM_ERROR = "upstream_error"
_STATUS_CODES: Mapping[int, str] = MappingProxyType(
    {
        400: "upstream_invalid_request",
        413: "upstream_invalid_request",
        422: "upstream_invalid_request",
        401: "upstream_auth_error",
        403: "upstream_auth_error",
        404: "upstream_not_found",
        408: UPSTREAM_TIMEOUT,
        504: UPSTREAM_TIMEOUT,
        429: "upstream_rate_limited",
    }
)
#: The OpenAI SSE ``delta.role`` decodes as a ``role`` channel; it is metadata, not content.
_ROLE_CHANNEL = "role"
_EVENT_STREAM = "text/event-stream"
_HTTP_OK_FIRST = 200
_HTTP_OK_END = 300


class SSEDecoderLike(Protocol):
    """The injected decoder (``edge/wire/sse.py`` ``SSEDecoder``; ``dispatch`` cannot import it)."""

    def feed(self, raw: bytes) -> tuple[DownstreamFrame, ...]: ...

    def malformed(self) -> bool: ...

    def finished(self) -> bool: ...


ClientFactory = Callable[[], "httpx.AsyncClient"]


def httpx_client_factory(*, api_key: str, timeout_s: float, connections: int) -> ClientFactory:
    """A factory for the pooled client: bearer auth, one timeout, ``connections`` pooled sockets.

    ``connections`` comes from the caller's ``ResourceContract`` (``pool_size(PROVIDER)``), so no
    capacity literal lives here. ``httpx`` is imported when the factory runs, not at import.
    """

    def _factory() -> httpx.AsyncClient:
        import httpx

        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        limits = httpx.Limits(max_connections=connections, max_keepalive_connections=connections)
        return httpx.AsyncClient(headers=headers, timeout=timeout_s, limits=limits)

    return _factory


def status_code(status: int) -> str:
    """The declared code for a non-2xx provider status."""
    return _STATUS_CODES.get(status, _UPSTREAM_ERROR)


def _failure(code: str) -> UpstreamEvent:
    return UpstreamEvent(text_deltas=(), final=True, gateway_error=code)


def _plain(value: object) -> object:
    """A JSON-serialisable copy of the frozen rendered body (mappings -> dict, tuples -> list)."""
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


class OpenAIUpstreamProvider:
    """A pooled, per-request-safe :class:`ProviderClient` for an OpenAI-compatible endpoint."""

    #: GW28 D-6: opening a stream keeps no instance-level latch, so one instance may serve
    #: concurrent requests and be a routing candidate's client. Checked on ``type(client)``.
    per_request_safe: ClassVar[bool] = True
    __slots__ = (
        "_base_url",
        "_client",
        "_client_factory",
        "_connections",
        "_decoder_factory",
        "_loop",
    )

    def __init__(
        self,
        *,
        base_url: str,
        decoder_factory: Callable[[], SSEDecoderLike],
        client_factory: ClientFactory,
        connections: int = 0,
    ) -> None:
        self._connections = connections
        self._base_url = base_url.rstrip("/")
        self._decoder_factory = decoder_factory
        self._client_factory = client_factory
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def connections(self) -> int:
        """Pooled sockets declared by the builder (0 = not declared); read for the boot budget."""
        return self._connections

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        """Stream the rendered request to the provider as ``UpstreamEvent``s."""
        return self._stream(request)

    async def abort(self) -> None:
        """No-op: a request's read is cut by closing its own iterator (see the module docstring)."""
        return None

    async def aclose(self) -> None:
        """Close the pooled client (lifespan shutdown)."""
        client, self._client, self._loop = self._client, None, None
        if client is not None:
            await client.aclose()

    def _pooled(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._client is None or self._loop is not loop:
            self._client = self._client_factory()
            self._loop = loop
        return self._client

    async def _stream(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        body = request.body.get(BODY_OPENAI_KEY)
        endpoint = request.body.get(BODY_ENDPOINT_KEY)
        if not isinstance(body, Mapping) or not isinstance(endpoint, str):
            yield _failure(UPSTREAM_REQUEST_UNBUILDABLE)
            return
        import httpx

        url = f"{self._base_url}/{endpoint}"
        try:
            async with self._pooled().stream("POST", url, json=_plain(body)) as response:
                if not _HTTP_OK_FIRST <= response.status_code < _HTTP_OK_END:
                    yield _failure(status_code(response.status_code))
                    return
                content_type = response.headers.get("content-type", "")
                if not content_type.startswith(_EVENT_STREAM):
                    yield _failure(STREAM_MALFORMED_UPSTREAM)
                    return
                async for event in _drain(response, self._decoder_factory()):
                    yield event
        except httpx.TimeoutException:
            yield _failure(UPSTREAM_TIMEOUT)
        except (httpx.HTTPError, OSError):
            yield _failure(UPSTREAM_UNAVAILABLE)


async def _drain(response: Any, decoder: SSEDecoderLike) -> AsyncIterator[UpstreamEvent]:
    """Decode the SSE body, one frame behind, so the last content event carries ``final=True``."""
    pending: DownstreamFrame | None = None
    async for raw in response.aiter_bytes():
        frames = decoder.feed(raw)
        if decoder.malformed():
            yield _failure(STREAM_MALFORMED_UPSTREAM)
            return
        for frame in frames:
            if frame.error_code is not None:
                yield UpstreamEvent(text_deltas=(), final=True, error_frame=frame.error_code)
                return
            if pending is not None:
                yield _content(pending, final=False)
            pending = frame
    if decoder.malformed() or not decoder.finished():
        yield _failure(STREAM_MALFORMED_UPSTREAM)
        return
    if pending is not None:
        yield _content(pending, final=True)


def _content(frame: DownstreamFrame, *, final: bool) -> UpstreamEvent:
    deltas = tuple(pair for pair in frame.text_deltas if pair[0] != _ROLE_CHANNEL)
    return UpstreamEvent(text_deltas=deltas, final=final)
