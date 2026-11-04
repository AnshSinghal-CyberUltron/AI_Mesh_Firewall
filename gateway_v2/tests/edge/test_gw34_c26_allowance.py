"""GW34 Task 7: the C26 part-count allowance applies to tool-bearing bodies only (D20)."""
from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any

from gateway_v2.edge import serve
from gateway_v2.edge.openai_chat import REFUSE_PRE_CHECK, ChatSurface
from tests.edge.test_gw34_upstream_body import _pydantic_tool
from tests.edge.test_gw36_s1_upstream_body import EXAMPLE_PLAN, KEY, _contract, _Recorder


def _one_worker_app(provider: _Recorder) -> tuple[Any, Any]:
    contract = dataclasses.replace(_contract(), worker_override=1)
    env = {
        "AMF_DEV_PROFILE": "1", "AMF_DEV_API_KEY": KEY, "AMF_DEV_PLAN_PATH": str(EXAMPLE_PLAN),
        "AMF_DEV_MODELS": "echo-1", "AMF_DEV_Q_SAFE": "500",
        "AMF_DEV_AUDIT_DRAIN_RATE_PER_S": "1000", "AMF_DEV_AUDIT_BYTES_PER_RECORD": "2765",
        "AMF_DEV_BUDGET": "unlimited",
    }
    deps, _logs = serve.build_boot_dependencies(contract=contract, env=env)
    deps["provider"] = provider
    return serve._assemble_app(deps), contract


def _post(app: Any, raw: bytes) -> tuple[int, bytes]:
    return asyncio.run(_raw_call(app, raw))


async def _raw_call(app: Any, raw: bytes) -> tuple[int, bytes]:
    scope = {"type": "http", "method": "POST", "path": "/v1/chat/completions",
             "headers": [(b"authorization", f"Bearer {KEY}".encode()), (b"content-type", b"json")]}
    sent: list[dict[str, Any]] = []
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if delivered:
            await asyncio.sleep(3600)
        delivered = True
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    status = next(int(m["status"]) for m in sent if m["type"] == "http.response.start")
    return status, b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


def _tool_request(n_tools: int) -> bytes:
    body = {"model": "echo-1", "messages": [{"role": "user", "content": "hi"}],
            "tools": [_pydantic_tool(f"tool_{i}") for i in range(n_tools)]}
    return json.dumps(body).encode()


def _commas(count: int) -> bytes:
    """A tool-free body with ``count`` extra ``,`` bytes (each counts as a structural part)."""
    head = '{"model":"echo-1","messages":[{"role":"user","content":"x"}],"metadata":{"k":"'
    return (head + "," * count + '"}}').encode()


def test_a_twenty_tool_request_with_one_worker_exceeds_the_plain_ceiling_but_is_admitted() -> None:
    provider = _Recorder()
    app, contract = _one_worker_app(provider)
    raw = _tool_request(20)
    assert contract.workers() == 1
    assert ChatSurface._part_count(raw) > contract.max_request_parts()  # would have been refused
    status, body = _post(app, raw)
    assert status == 200, body
    [sent] = provider.sent
    assert len(sent["tools"]) == 20


def test_a_tool_free_body_just_over_the_plain_ceiling_is_still_a_pre_check_refusal() -> None:
    provider = _Recorder()
    app, contract = _one_worker_app(provider)
    base = ChatSurface._part_count(_commas(0))
    raw = _commas(contract.max_request_parts() + 1 - base)
    assert ChatSurface._part_count(raw) == contract.max_request_parts() + 1
    status, body = _post(app, raw)
    assert status != 200 and json.loads(body)["error"]["code"] == REFUSE_PRE_CHECK, body
    assert provider.sent == []


def test_a_tool_bearing_body_over_the_allowance_is_refused() -> None:
    provider = _Recorder()
    app, contract = _one_worker_app(provider)
    allowance = contract.tool_bounds().parts_allowance
    ceiling = contract.max_request_parts() + allowance
    marker = ' "tools" '
    head = '{"model":"echo-1","messages":[{"role":"user","content":"x"}],"metadata":{"k":"' + marker
    base = ChatSurface._part_count((head + '"}}').encode())
    raw = (head + "," * (ceiling + 1 - base) + '"}}').encode()
    assert ChatSurface._part_count(raw) == ceiling + 1
    status, body = _post(app, raw)
    assert status != 200 and json.loads(body)["error"]["code"] == REFUSE_PRE_CHECK, body
    assert provider.sent == []

