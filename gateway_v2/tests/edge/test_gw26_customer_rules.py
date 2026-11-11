"""GW26: customer conditions run on chat, embeddings, responses, and MCP."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY
from gateway_v2.edge import serve
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals
from tests.edge._mcp_fixtures import (
    Broker,
    Control,
    call,
    mcp_deps,
    server_row,
)

_KEY = "dev-key-change-me"
_COMPETITOR = "AcmeRival"
_CARD = "ZsCardToken99"
_EMBED = "EmbedOnlyToken"
_REDACTED = "[REDACTED]"
_SURFACES = ["chat", "embeddings", "rag", "vector", "mcp"]


def _contract() -> ResourceContract:
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=400 * 1024 * 1024 * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals, target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=400 * 1024 * 1024
    )


def _rule(
    rule_id: str,
    keywords: tuple[str, ...],
    *,
    action: str,
    scope: str,
    surfaces: list[str],
) -> dict[str, object]:
    return {
        "rule_id": rule_id,
        "category": "exfil",
        "mode": "enforce",
        "action": action,
        "threshold": None,
        "priority": 10,
        "scope": scope,
        "on_unavailable": "fail_closed",
        "surfaces": surfaces,
        "condition": {
            "kind": "keywords",
            "field": "",
            "direction": scope,
            "keywords": list(keywords),
            "pattern": "",
            "detector_class": "",
            "pipeline_stage": "",
        },
    }


def _plan(path: Path) -> None:
    body = {
        "org_id": "dev-org",
        "streaming_mode": "incremental",
        "rules": [
            _rule(
                "competitor-chat",
                (_COMPETITOR,),
                action="block",
                scope="input",
                surfaces=["chat"],
            ),
            _rule(
                "card-output",
                (_CARD,),
                action="redact",
                scope="output",
                surfaces=_SURFACES,
            ),
            _rule(
                "embed-only",
                (_EMBED,),
                action="redact",
                scope="input",
                surfaces=["embeddings"],
            ),
        ],
    }
    path.write_text(json.dumps(body), encoding="utf-8")


class _Recording:
    def __init__(self) -> None:
        self.received: list[str] = []

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        return self._stream(request)

    async def _stream(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        raw = request.body.get(BODY_DELTAS_KEY, ())
        pairs = raw if isinstance(raw, tuple) else ()
        deltas = tuple((str(item[0]), str(item[1])) for item in pairs)
        self.received.append("\n".join(text for _channel, text in deltas))
        if deltas and deltas[0][0] == "embeddings_input":
            vector = json.dumps([0.0, 0.5])
            yield UpstreamEvent(text_deltas=(("embeddings_input", vector),), final=True)
            return
        yield UpstreamEvent(text_deltas=(("content", "echo: " + self.received[-1]),), final=True)

    async def abort(self) -> None:
        return None


def _app(plan: Path, provider: _Recording) -> tuple[object, Broker]:
    env = {
        "AMF_DEV_PROFILE": "1",
        "AMF_DEV_API_KEY": _KEY,
        "AMF_DEV_PLAN_PATH": str(plan),
        "AMF_DEV_MODELS": "echo-1",
        "AMF_DEV_Q_SAFE": "500",
        "AMF_DEV_AUDIT_DRAIN_RATE_PER_S": "1000",
        "AMF_DEV_AUDIT_BYTES_PER_RECORD": "2765",
        "AMF_DEV_BUDGET": "unlimited",
    }
    deps, _logs = serve.build_boot_dependencies(contract=_contract(), env=env)
    deps["provider"] = provider
    reply = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "ok"}]}}
    broker = Broker(reply)
    servers = [server_row("search")]
    tools = {"known_tools": ["find"], "enabled_tools": ["find"], "disabled_tools": []}
    deps["mcp"] = mcp_deps(broker, Control(servers, tools), "dev-org=acme")
    return serve._assemble_app(deps), broker


async def _post(app: object, path: str, request_body: Mapping[str, object]) -> tuple[int, bytes]:
    payload = json.dumps(request_body).encode()
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [(b"authorization", f"Bearer {_KEY}".encode()), (b"content-type", b"json")],
    }
    sent: list[Mapping[str, object]] = []
    delivered = False

    async def _receive() -> Mapping[str, object]:
        nonlocal delivered
        if delivered:
            await asyncio.sleep(3600)
        delivered = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def _send(message: Mapping[str, object]) -> None:
        sent.append(message)

    assert callable(app)
    await app(scope, _receive, _send)
    status = next(
        int(str(item["status"])) for item in sent if item["type"] == "http.response.start"
    )
    body = b"".join(
        bytes(item.get("body", b""))  # type: ignore[arg-type]
        for item in sent
        if item["type"] == "http.response.body"
    )
    return status, body


def test_chat_keyword_blocks_and_mcp_does_not(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    _plan(plan)
    provider = _Recording()
    app, broker = _app(plan, provider)
    status, body = asyncio.run(
        _post(
            app,
            "/v1/chat/completions",
            {
                "model": "echo-1",
                "stream": False,
                "messages": [{"role": "user", "content": _COMPETITOR}],
            },
        )
    )
    assert status == 400
    assert b"input_blocked" in body
    assert provider.received == []

    status, body = asyncio.run(
        _post(
            app,
            "/v1/mcp/search",
            call("tools/call", {"name": "find", "arguments": {"q": _COMPETITOR}}),
        )
    )
    assert b"input_blocked" not in body
    assert status == 200, body
    assert broker.requests
    sent = json.dumps(broker.requests[0])
    assert _COMPETITOR in sent


def test_output_card_redacts_on_chat_and_responses(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    _plan(plan)
    provider = _Recording()
    app, _broker = _app(plan, provider)
    chat_status, chat_body = asyncio.run(
        _post(
            app,
            "/v1/chat/completions",
            {
                "model": "echo-1",
                "stream": False,
                "messages": [{"role": "user", "content": f"note {_CARD}"}],
            },
        )
    )
    assert chat_status == 200, chat_body
    assert _CARD.encode() not in chat_body
    assert _REDACTED.encode() in chat_body
    assert provider.received and _CARD in provider.received[0]

    responses_status, responses_body = asyncio.run(
        _post(app, "/v1/responses", {"model": "echo-1", "input": f"note {_CARD}"})
    )
    assert responses_status == 200, responses_body
    assert _CARD.encode() not in responses_body
    assert _REDACTED.encode() in responses_body


def test_embeddings_redact_follows_the_surface_list(tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    _plan(plan)
    provider = _Recording()
    app, _broker = _app(plan, provider)
    blocked_status, blocked_body = asyncio.run(
        _post(app, "/v1/embeddings", {"model": "echo-1", "input": _COMPETITOR})
    )
    assert blocked_status == 200, blocked_body
    assert b"input_blocked" not in blocked_body
    assert provider.received[-1] == _COMPETITOR

    provider.received.clear()
    redacted_status, redacted_body = asyncio.run(
        _post(app, "/v1/embeddings", {"model": "echo-1", "input": _EMBED})
    )
    assert redacted_status == 200, redacted_body
    assert provider.received
    assert _EMBED not in provider.received[-1]
    assert _REDACTED in provider.received[-1]
