"""``/v1/responses`` over the shipped chat lifecycle (GW15 close-out, task 6.1).

A Responses request is translated into a chat request and run through
:meth:`~gateway_v2.edge.openai_chat.ChatSurface.run_lifecycle`, the same
pre-checks, identity, plan read, admission, budget, and input enforcement chat
uses. This module holds no scanner and makes no security decision.

This handler writes the client bytes itself. A refusal from the lifecycle is
JSON even when the caller asked to stream. A proceed reuses the chat
non-streaming renderer or the chat stream, captures what they write, and
translates it.

A stored record holds the masked input the provider received and the enforced
reply, never the raw body from before masking.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from gateway_v2.dispatch.transform import BODY_DELTAS_KEY
from gateway_v2.domain.posture import STREAM_MALFORMED_UPSTREAM
from gateway_v2.edge import errors
from gateway_v2.edge.asgi_sse import (
    ASGIReceive,
    ASGIScope,
    ASGISend,
    end_sse,
    read_body,
    send_sse,
    start_sse,
)
from gateway_v2.edge.audit_emit import emit_chat_audit
from gateway_v2.edge.console_projection import console_from_holds
from gateway_v2.edge.openai_chat import REFUSE_IDENTITY_UNRESOLVED, ChatSurface, LifecycleOutcome
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.responses_store import MemoryResponseStore, ResponseStore, StoredResponse
from gateway_v2.edge.routes import ChatRoute, RouteHandler, _emit_error_code, emit_response
from gateway_v2.edge.wire.openai import CanonicalRequest
from gateway_v2.edge.wire.responses import (
    RESPONSES_MALFORMED_BODY,
    ChatBody,
    TranslateRefusal,
    json_ready,
    new_id,
    response_object,
    translate_request,
)
from gateway_v2.edge.wire.responses_events import ResponsesEventTranslator

__all__ = (
    "RESPONSE_NOT_FOUND",
    "RESPONSE_STORE_UNAVAILABLE",
    "RESPONSES_PATH_PREFIX",
    "ResponsesRoute",
    "responses_entries",
)

#: A stored id that is missing, expired, or owned by another org.
RESPONSE_NOT_FOUND = "response_not_found"
#: The store could not record a response the caller asked to store.
RESPONSE_STORE_UNAVAILABLE = "response_store_unavailable"
#: The prefix the GET and DELETE id routes are registered under.
RESPONSES_PATH_PREFIX = "/v1/responses/"

_CHAT_PATH = "/v1/chat/completions"
_INPUT_ITEMS_SUFFIX = "/input_items"
_OK = 200
_CONTENT_LENGTH = "content-length"
_REPLAY_JOIN = "\n"


@dataclass(frozen=True, slots=True)
class _Create:
    """What the handler learned from the body before the chat lifecycle ran."""

    chat_bytes: bytes
    roles: tuple[str, ...]
    prefix_count: int
    model: str
    store: bool
    previous_id: str | None
    org_id: str | None


class _Capture:
    """A per-request ASGI ``send`` that records one HTTP response instead of writing it."""

    __slots__ = ("body", "headers", "status")

    def __init__(self) -> None:
        self.status = 0
        self.headers: tuple[tuple[str, str], ...] = ()
        self.body = bytearray()

    async def send(self, event: Mapping[str, object]) -> None:
        kind = event.get("type")
        if kind == "http.response.start":
            status = event.get("status")
            self.status = status if isinstance(status, int) else 0
            self.headers = _decode_headers(event.get("headers"))
        elif kind == "http.response.body":
            chunk = event.get("body")
            if isinstance(chunk, (bytes, bytearray)):
                self.body.extend(chunk)


@dataclass(frozen=True, slots=True)
class ResponsesRoute:
    """The Responses surface. Holds the booted chat route and one store."""

    chat: ChatRoute
    store: ResponseStore

    async def handle(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        """``POST /v1/responses``."""
        holds = RequestHolds()
        outcome: LifecycleOutcome | None = None
        try:
            body = await read_body(receive)
            prepared = await self._prepare(scope, body)
            if isinstance(prepared, str):
                await emit_response(send, errors.render_http(prepared))
                return
            if isinstance(prepared, TranslateRefusal):
                await emit_response(send, errors.render_http(prepared.code, param=prepared.param))
                return
            chat_scope = {**scope, "path": _CHAT_PATH, "raw_path": _CHAT_PATH.encode("ascii")}
            req, outcome = await ChatSurface(deps=self.chat).run_lifecycle(
                chat_scope, prepared.chat_bytes, holds
            )
            if outcome.refuse_code is not None or req is None:
                await self._refuse(send, outcome, holds)
                return
            if req.streaming:
                await self._stream(send, prepared, req, outcome, holds)
                return
            await self._non_stream(send, prepared, req, outcome, holds)
        finally:
            refused = None if outcome is None else outcome.refuse_code
            emit_chat_audit(self.chat.audit, holds, refused)
            holds.release(self.chat.admission, self.chat.quota)

    async def handle_get(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        """``GET /v1/responses/{id}`` and ``GET /v1/responses/{id}/input_items``."""
        del receive
        response_id, items = _path_id(scope)
        org_id = await self._org(scope)
        if org_id is None:
            await emit_response(send, errors.render_http(REFUSE_IDENTITY_UNRESOLVED))
            return
        record = None if response_id is None else await self.store.get(org_id, response_id)
        if record is None:
            await emit_response(send, errors.render_http(RESPONSE_NOT_FOUND))
            return
        if items:
            data = [dict(item) for item in record.input_items]
            payload: dict[str, object] = {
                "object": "list",
                "data": data,
                "first_id": data[0].get("id") if data else None,
                "last_id": data[-1].get("id") if data else None,
                "has_more": False,
            }
        else:
            payload = dict(record.response)
        await emit_response(send, _json_ok(payload))

    async def handle_delete(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        """``DELETE /v1/responses/{id}``."""
        del receive
        response_id, items = _path_id(scope)
        org_id = await self._org(scope)
        if org_id is None:
            await emit_response(send, errors.render_http(REFUSE_IDENTITY_UNRESOLVED))
            return
        deleted = (
            response_id is not None
            and not items
            and await self.store.delete(org_id, response_id)
        )
        if not deleted:
            await emit_response(send, errors.render_http(RESPONSE_NOT_FOUND))
            return
        await emit_response(
            send, _json_ok({"id": response_id, "object": "response", "deleted": True})
        )

    async def _prepare(self, scope: ASGIScope, body: bytes) -> _Create | TranslateRefusal | str:
        """Size check, translate, and resolve ``previous_response_id``. Returns a refusal code."""
        pre_check = ChatSurface(deps=self.chat)._pre_checks(scope, body)
        if pre_check is not None:
            return pre_check
        obj = _decode_object(body)
        if obj is None:
            return TranslateRefusal(RESPONSES_MALFORMED_BODY)
        first = translate_request(obj)
        if isinstance(first, TranslateRefusal):
            return first
        previous = obj.get("previous_response_id")
        if previous is not None and (not isinstance(previous, str) or not previous):
            return TranslateRefusal(RESPONSES_MALFORMED_BODY)
        org_id: str | None = None
        replay: tuple[Mapping[str, object], ...] = ()
        chat_body: ChatBody | TranslateRefusal = first
        if previous is not None:
            org_id = await self._org(scope)
            if org_id is None:
                return REFUSE_IDENTITY_UNRESOLVED
            record = await self.store.get(org_id, previous)
            if record is None:
                return RESPONSE_NOT_FOUND
            replay = record.replay_messages
            chat_body = translate_request(obj, replay)
        if isinstance(chat_body, TranslateRefusal):
            return chat_body
        return _created(obj, chat_body, replay, previous, org_id)

    async def _org(self, scope: ASGIScope) -> str | None:
        identity = await self.chat.identity.resolve(scope)
        return None if identity is None else identity.org_id

    async def _refuse(
        self, send: ASGISend, outcome: LifecycleOutcome, holds: RequestHolds
    ) -> None:
        """The lifecycle refusal as JSON, with the console object when a decision exists."""
        await _emit_error_code(
            send,
            outcome.refuse_code or REFUSE_IDENTITY_UNRESOLVED,
            outcome.request_id,
            outcome.refuse_param,
            self.chat.contract.max_output_tokens_boundary(),
            console_from_holds(holds, output_action=None, provider_called=False),
        )

    async def _non_stream(
        self,
        send: ASGISend,
        prepared: _Create,
        req: CanonicalRequest,
        outcome: LifecycleOutcome,
        holds: RequestHolds,
    ) -> None:
        """Chat's non-streaming render, captured and translated into a Response object."""
        plan, destination, upstream = outcome.plan, outcome.destination, outcome.body
        assert plan is not None and destination is not None and upstream is not None
        capture = _Capture()
        await ChatSurface(deps=self.chat).collect_and_render(
            req, plan, destination, upstream, capture.send, holds
        )
        if capture.status != _OK:
            await emit_response(
                send,
                errors.HTTPResponse(
                    status=capture.status, body=bytes(capture.body), headers=capture.headers
                ),
            )
            return
        completion = _decode_object(bytes(capture.body))
        if completion is None or not _org_consistent(holds, prepared):
            code = STREAM_MALFORMED_UPSTREAM if completion is None else RESPONSE_STORE_UNAVAILABLE
            await emit_response(send, errors.render_http(code, request_id=holds.request_id))
            return
        obj = response_object(completion, model=prepared.model, store=prepared.store)
        obj["previous_response_id"] = prepared.previous_id
        text = obj.get("output_text")
        if prepared.store and not await self._persist(
            holds, prepared, req, upstream, obj, text if isinstance(text, str) else ""
        ):
            await emit_response(
                send, errors.render_http(RESPONSE_STORE_UNAVAILABLE, request_id=holds.request_id)
            )
            return
        headers = tuple(
            (name, value) for name, value in capture.headers if name != _CONTENT_LENGTH
        )
        await emit_response(
            send, errors.HTTPResponse(status=_OK, body=_dumps(obj), headers=headers)
        )

    async def _stream(
        self,
        send: ASGISend,
        prepared: _Create,
        req: CanonicalRequest,
        outcome: LifecycleOutcome,
        holds: RequestHolds,
    ) -> None:
        """Chat's stream, translated to typed events as it arrives."""
        plan, destination, upstream = outcome.plan, outcome.destination, outcome.body
        assert plan is not None and destination is not None and upstream is not None
        translator = ResponsesEventTranslator(
            response_id=new_id("resp"),
            item_id=new_id("msg"),
            model=prepared.model,
            created_at=int(time.time()),
        )
        await start_sse(send)
        await send_sse(send, translator.start())

        async def forward(event: Mapping[str, object]) -> None:
            if event.get("type") != "http.response.body":
                return
            chunk = event.get("body")
            if isinstance(chunk, (bytes, bytearray)) and chunk:
                out = translator.feed(bytes(chunk))
                if out:
                    await send_sse(send, out)

        await self.chat._stream_response(forward, plan, destination, upstream, holds)
        failure = await self._stream_failure(translator, prepared, holds, req, upstream)
        await send_sse(send, translator.finish(failure_code=failure))
        await end_sse(send)

    async def _stream_failure(
        self,
        translator: ResponsesEventTranslator,
        prepared: _Create,
        holds: RequestHolds,
        req: CanonicalRequest,
        upstream: Mapping[str, object],
    ) -> str | None:
        """The code that fails an otherwise clean stream, or ``None``."""
        final = translator.final_response()
        if final.get("status") != "completed":
            return None
        if not _org_consistent(holds, prepared):
            return RESPONSE_STORE_UNAVAILABLE
        if not prepared.store:
            return None
        final["store"] = True
        final["previous_response_id"] = prepared.previous_id
        if await self._persist(holds, prepared, req, upstream, final, translator.output_text):
            return None
        return RESPONSE_STORE_UNAVAILABLE

    async def _persist(
        self,
        holds: RequestHolds,
        prepared: _Create,
        req: CanonicalRequest,
        upstream: Mapping[str, object],
        response: Mapping[str, object],
        assistant_text: str,
    ) -> bool:
        """Store the response, the masked turn, and the enforced reply under the lifecycle org."""
        org_id = holds.org_id
        if not org_id:
            return False
        turn = _masked_turn(req, upstream, prepared.roles)
        if turn is None:
            return False
        replay: list[Mapping[str, object]] = [
            {"role": role, "content": text} for _, role, text in turn if role != "system"
        ]
        if assistant_text:
            replay.append({"role": "assistant", "content": assistant_text})
        items = tuple(
            _input_item(role, text) for index, role, text in turn if index >= prepared.prefix_count
        )
        record = StoredResponse(
            response=dict(response), replay_messages=tuple(replay), input_items=items
        )
        return await self.store.put(org_id, record)


def responses_entries(
    chat: ChatRoute, store: ResponseStore | None = None
) -> tuple[dict[tuple[str, str], RouteHandler], dict[tuple[str, str], RouteHandler]]:
    """The exact and prefix route entries for the Responses surface.

    ``store`` defaults to a per-process memory store. ``POST /v1/responses`` is one exact
    entry. The id routes are prefix entries, which the app consults only after an exact miss.
    """
    route = ResponsesRoute(chat=chat, store=store if store is not None else MemoryResponseStore())
    exact: dict[tuple[str, str], RouteHandler] = {("POST", "/v1/responses"): route.handle}
    prefix: dict[tuple[str, str], RouteHandler] = {
        ("GET", RESPONSES_PATH_PREFIX): route.handle_get,
        ("DELETE", RESPONSES_PATH_PREFIX): route.handle_delete,
    }
    return exact, prefix


def _created(
    obj: Mapping[str, object],
    chat_body: ChatBody,
    replay: Sequence[Mapping[str, object]],
    previous: str | None,
    org_id: str | None,
) -> _Create:
    payload = json_ready(chat_body.payload)
    assert isinstance(payload, dict)
    messages = payload.get("messages")
    roles = tuple(
        str(message.get("role") or "user")
        for message in (messages if isinstance(messages, list) else [])
        if isinstance(message, dict)
    )
    instructions = obj.get("instructions")
    has_system = isinstance(instructions, str) and bool(instructions.strip())
    model = obj.get("model")
    return _Create(
        chat_bytes=_dumps(payload),
        roles=roles,
        prefix_count=int(has_system) + len(replay),
        model=model if isinstance(model, str) else "",
        store=obj.get("store") is True,
        previous_id=previous,
        org_id=org_id,
    )


def _masked_turn(
    req: CanonicalRequest, upstream: Mapping[str, object], roles: tuple[str, ...]
) -> list[tuple[int, str, str]] | None:
    """``(message index, role, masked text)`` from the bytes the provider received.

    The staged deltas keep the parsed channel count and order, so each maps back to its
    message through ``input_message_index``. A count mismatch returns ``None`` and the
    caller does not store.
    """
    raw = upstream.get(BODY_DELTAS_KEY)
    if not isinstance(raw, (list, tuple)):
        return None
    texts: list[str] = []
    for pair in raw:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2 or not isinstance(pair[1], str):
            return None
        texts.append(pair[1])
    if len(texts) != len(req.input_message_index):
        return None
    grouped: dict[int, list[str]] = {}
    for index, text in zip(req.input_message_index, texts, strict=True):
        grouped.setdefault(index, []).append(text)
    turn: list[tuple[int, str, str]] = []
    for index in sorted(grouped):
        if index >= len(roles):
            return None
        turn.append((index, roles[index], _REPLAY_JOIN.join(grouped[index])))
    return turn


def _input_item(role: str, text: str) -> Mapping[str, object]:
    return {
        "id": new_id("msg"),
        "type": "message",
        "role": role,
        "content": [{"type": "input_text", "text": text}],
    }


def _org_consistent(holds: RequestHolds, prepared: _Create) -> bool:
    """A ``previous_response_id`` read must be for the org the lifecycle admitted."""
    return prepared.org_id is None or prepared.org_id == holds.org_id


def _path_id(scope: ASGIScope) -> tuple[str | None, bool]:
    """``(response id, is input_items)`` from the request path. An invalid id is ``None``."""
    path = scope.get("path")
    if not isinstance(path, str) or not path.startswith(RESPONSES_PATH_PREFIX):
        return None, False
    rest = path[len(RESPONSES_PATH_PREFIX):]
    items = rest.endswith(_INPUT_ITEMS_SUFFIX)
    if items:
        rest = rest[: -len(_INPUT_ITEMS_SUFFIX)]
    if not rest or "/" in rest:
        return None, items
    return rest, items


def _decode_object(body: bytes) -> dict[str, object] | None:
    try:
        decoded = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _decode_headers(raw: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, (list, tuple)):
        return ()
    headers: list[tuple[str, str]] = []
    for pair in raw:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            name, value = pair
            if isinstance(name, bytes) and isinstance(value, bytes):
                headers.append((name.decode("latin-1"), value.decode("latin-1")))
    return tuple(headers)


def _dumps(payload: object) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _json_ok(payload: Mapping[str, object]) -> errors.HTTPResponse:
    return errors.HTTPResponse(
        status=_OK, body=_dumps(dict(payload)), headers=(("content-type", "application/json"),)
    )
