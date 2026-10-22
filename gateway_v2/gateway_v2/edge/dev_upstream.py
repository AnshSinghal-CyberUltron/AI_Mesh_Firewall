"""DEV PROFILE optional real upstream: the shipped ``HttpProviderClient`` to one OpenAI URL.

Wired ONLY when ``AMF_DEV_PROFILE=1`` AND ``AMF_DEV_PROVIDER_URL`` is set. Three seams in the
shipped client do not line up with a stock OpenAI-compatible endpoint, and each is adapted here
(dev-only, named in the boot log) rather than silently papered over:

1. **Destination.** ``DispatchRouter.select(plan)`` returns a LABEL
   (``amf://upstream/<org>/<version>#<fp>``), not a URL — the plan carries no provider/model
   field — and ``HttpProviderClient`` POSTs to ``request.destination`` verbatim. The adapter
   replaces the label with the configured chat-completions URL.
2. **Body.** ``HttpProviderClient._provider_body`` sends ``{"model", "text_deltas", "stream"}``
   — the gateway's internal pack, which an OpenAI endpoint rejects. The adapter sends
   ``{"model", "messages", "stream": true}``. The shipped ``Transform`` keeps ``(channel, text)``
   pairs only, so ROLES ARE NOT PRESERVED: every forwarded input channel becomes a ``user``
   message (named in the boot log).
3. **HTTP status.** The shipped ``_drain_response`` never reads the status code, so a 4xx/5xx JSON
   body (no SSE ``\\n\\n`` terminator) decodes to NO frames and the stream ends as an EMPTY
   success. The adapter fails CLOSED on a non-2xx status with a terminal error frame.

A fresh client is built per ``open`` (the shipped client keeps its abort latch + stream handle as
instance state, and the frozen ``ChatRoute`` shares ONE provider across requests). ``edge`` may
import ``dispatch`` and ``edge.wire.sse``; ``httpx`` stays a lazy import (``conformance`` extra).
"""

from __future__ import annotations

import sys
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from gateway_v2.dispatch.http_provider import HttpProviderClient, SSEDecoderLike
from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY, BODY_MODEL_KEY
from gateway_v2.domain import posture
from gateway_v2.edge.wire.sse import SSEDecoder

if TYPE_CHECKING:
    import httpx

__all__ = ("DevUpstreamProvider", "openai_messages")

#: The role every forwarded input channel is sent as (roles are not carried by ``Transform``).
_FORWARDED_ROLE = "user"
#: The channel the SSE decoder emits for an OpenAI ``delta.role`` (metadata, not content).
_ROLE_CHANNEL = "role"
#: First and one-past-last HTTP status of the success class (wire constants, not capacities).
_HTTP_OK_FIRST = 200
_HTTP_OK_END = 300


def openai_messages(body: Any) -> list[dict[str, str]]:
    """Map the ``Transform`` pack's ``text_deltas`` to OpenAI ``messages`` (all ``user``)."""
    raw = body.get(BODY_DELTAS_KEY, ())
    messages: list[dict[str, str]] = []
    if isinstance(raw, (tuple, list)):
        for item in raw:
            if isinstance(item, (tuple, list)) and len(item) == 2 and isinstance(item[1], str):
                messages.append({"role": _FORWARDED_ROLE, "content": item[1]})
    return messages


class _OpenAIBodyClient(HttpProviderClient):
    """The shipped client with the OpenAI body + destination + status adaptations."""

    __slots__ = ("_url",)

    def __init__(self, *, url: str, api_key: str, timeout_s: float) -> None:
        def _client_factory() -> httpx.AsyncClient:
            import httpx

            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            return httpx.AsyncClient(headers=headers, timeout=timeout_s)

        def _decoder_factory() -> SSEDecoderLike:
            return SSEDecoder()

        super().__init__(
            client_factory=_client_factory,
            decoder_factory=_decoder_factory,
            clock=time.monotonic,
        )
        self._url = url

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        return super().open(UpstreamRequest(destination=self._url, body=request.body))

    @staticmethod
    def _provider_body(request: UpstreamRequest) -> dict[str, object]:
        return {
            "model": request.body.get(BODY_MODEL_KEY, ""),
            "messages": openai_messages(request.body),
            "stream": True,
        }

    async def _drain_response(
        self,
        response: httpx.Response,
        decoder: SSEDecoderLike,
    ) -> AsyncIterator[UpstreamEvent]:
        if not _HTTP_OK_FIRST <= response.status_code < _HTTP_OK_END:
            # Fail closed: a non-2xx upstream is never an empty success. Status only, no body
            # (the body may echo prompt content).
            sys.stderr.write(f"dev-upstream: HTTP {response.status_code} from provider\n")
            sys.stderr.flush()
            yield UpstreamEvent(
                text_deltas=(), final=True, error_frame=posture.STREAM_MALFORMED_UPSTREAM
            )
            return
        async for event in super()._drain_response(response, decoder):
            # An OpenAI chunk's ``delta.role`` decodes as a ``role`` channel; it is not content.
            deltas = tuple(pair for pair in event.text_deltas if pair[0] != _ROLE_CHANNEL)
            yield UpstreamEvent(
                text_deltas=deltas, final=event.final, error_frame=event.error_frame
            )


class DevUpstreamProvider:
    """A ``ProviderClient`` that opens a FRESH adapted ``HttpProviderClient`` per request."""

    __slots__ = ("_api_key", "_inflight", "_timeout_s", "_url")

    def __init__(self, *, url: str, api_key: str, timeout_s: float) -> None:
        self._url = url
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._inflight: set[HttpProviderClient] = set()

    @property
    def url(self) -> str:
        return self._url

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        return self._stream(request)

    async def _stream(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        client = _OpenAIBodyClient(url=self._url, api_key=self._api_key, timeout_s=self._timeout_s)
        self._inflight.add(client)
        try:
            async for event in client.open(request):
                yield event
        finally:
            self._inflight.discard(client)

    async def abort(self) -> None:
        """Abort every in-flight stream (the protocol's ``abort`` carries no request identity)."""
        for client in tuple(self._inflight):
            await client.abort()
