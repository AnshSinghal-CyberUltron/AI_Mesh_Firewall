"""GW27 test harness: the dev-profile app, a plan carrying ``controls``, a recording provider."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

from gateway_v2.dispatch.openai_upstream import BODY_OPENAI_KEY
from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY
from gateway_v2.edge import serve
from gateway_v2.runtime.resources import ResourceContract

KEY = "dev-key-change-me"
ORG = "dev-org"


def contract(workers: int = 1) -> ResourceContract:
    return ResourceContract(
        cpu_quota=4.0, memory_limit=2 * 1024**3, fd_limit=4096, guard_capacity=None,
        target_p99_ms=20.0, utilization_cap=0.75, per_worker_rss=400 * 1024**2,
        worker_override=workers, cpu_source="t", mem_source="t", fd_source="t",
    )


class Recording:
    """A provider that records every open (so zero-provider-call refusals are countable)."""

    def __init__(self) -> None:
        self.opens = 0
        self.openai_bodies: list[Mapping[str, object]] = []

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        self.opens += 1
        openai = request.body.get(BODY_OPENAI_KEY)
        if isinstance(openai, Mapping):
            self.openai_bodies.append(openai)
        return self._stream(request)

    async def _stream(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        raw = request.body.get(BODY_DELTAS_KEY, ())
        pairs = raw if isinstance(raw, tuple) else ()
        first = pairs[0][0] if pairs else ""
        if first == "embeddings_input":
            yield UpstreamEvent(text_deltas=(("embeddings_input", "[0.0, 0.5]"),), final=True)
            return
        yield UpstreamEvent(text_deltas=(("content", "ok"),), final=True)

    async def abort(self) -> None:
        return None


def write_plan(path: Path, controls: Mapping[str, object] | None) -> None:
    rule = {
        "rule_id": "never", "category": "exfil", "mode": "enforce", "action": "block",
        "threshold": None, "priority": 10, "scope": "input", "on_unavailable": "fail_closed",
        "surfaces": ["chat", "embeddings"],
        "condition": {"kind": "keywords", "field": "", "direction": "input",
                      "keywords": ["zz-never-matches-zz"], "pattern": "", "detector_class": "",
                      "pipeline_stage": ""},
    }
    body: dict[str, object] = {"org_id": ORG, "streaming_mode": "incremental", "rules": [rule]}
    if controls is not None:
        body["controls"] = {"version": 1, **controls}
        body["requires"] = ["controls/1"]
    path.write_text(json.dumps(body), encoding="utf-8")


def build(
    tmp_path: Path, controls: Mapping[str, object] | None, provider: Recording,
    **overrides: object,
) -> tuple[object, dict[str, object]]:
    plan = tmp_path / "plan.json"
    write_plan(plan, controls)
    env = {
        "AMF_DEV_PROFILE": "1", "AMF_DEV_API_KEY": KEY, "AMF_DEV_PLAN_PATH": str(plan),
        "AMF_DEV_MODELS": "echo-1", "AMF_DEV_Q_SAFE": "500",
        "AMF_DEV_AUDIT_DRAIN_RATE_PER_S": "1000", "AMF_DEV_AUDIT_BYTES_PER_RECORD": "2765",
        "AMF_DEV_BUDGET": "unlimited",
    }
    deps, _logs = serve.build_boot_dependencies(contract=contract(), env=env)
    deps["provider"] = provider
    deps.update(overrides)
    return serve._assemble_app(deps), deps  # noqa: SLF001


async def post(app: object, path: str, request_body: Mapping[str, object]) -> tuple[int, bytes]:
    payload = json.dumps(request_body).encode()
    scope = {
        "type": "http", "method": "POST", "path": path,
        "headers": [(b"authorization", f"Bearer {KEY}".encode()), (b"content-type", b"json")],
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
    status = next(int(str(m["status"])) for m in sent if m["type"] == "http.response.start")
    body = b"".join(bytes(m.get("body", b"")) for m in sent if m["type"] == "http.response.body")  # type: ignore[arg-type]
    return status, body


CHAT = ("/v1/chat/completions", {"model": "echo-1", "stream": False, "max_tokens": 2000,
                                 "messages": [{"role": "user", "content": "hi"}]})
CHAT_STREAM = ("/v1/chat/completions", {"model": "echo-1", "stream": True, "max_tokens": 2000,
                                         "messages": [{"role": "user", "content": "hi"}]})
RESPONSES = ("/v1/responses", {"model": "echo-1", "input": "hi", "max_output_tokens": 2000})
EMBEDDINGS = ("/v1/embeddings", {"model": "echo-1", "input": "hi"})
# One request per minute with burst 2: the third back-to-back request is refused.
BURST_TWO = {"rate": {"enabled": True, "requests_per_minute": 1, "burst": 2}}


def run(app: object, call: tuple[str, Mapping[str, object]]) -> tuple[int, bytes]:
    return asyncio.run(post(app, call[0], call[1]))
