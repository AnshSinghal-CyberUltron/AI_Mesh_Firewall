"""GW35 in-process harness: the dev-profile app over raw ASGI with a scripted provider.

Captures every audit record the route enqueues, so a test can compare the HTTP console, the SSE
frame and the audit record for the same request. Test fakes live here (outside the layer gates).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from gateway_v2.audit.record import AuditRecord
from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.edge import serve
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

EXAMPLE_PLAN = Path(__file__).resolve().parents[2] / "dev" / "plan.example.json"
KEY = "sk-dev-gw35"
STAGES = ("auth", "rate_limit", "input_scan", "model_routing", "model_output", "output_guardrail")


def contract() -> ResourceContract:
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


class ScriptedProvider:
    """Yields the scripted events for every open; records what it was sent."""

    def __init__(self, events: Sequence[UpstreamEvent]) -> None:
        self.events = tuple(events)
        self.opened = 0

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        del request
        self.opened += 1
        return self._stream()

    async def _stream(self) -> AsyncIterator[UpstreamEvent]:
        for event in self.events:
            yield event

    async def abort(self) -> None:
        return None


def ok_events(text: str = "hello there") -> list[UpstreamEvent]:
    return [UpstreamEvent(text_deltas=(("content", text),), final=True)]


def failed_events(code: str) -> list[UpstreamEvent]:
    return [UpstreamEvent(text_deltas=(), final=True, gateway_error=code)]


@dataclass
class Result:
    status: int
    headers: dict[str, str]
    body: bytes
    records: list[AuditRecord] = field(default_factory=list)

    def json(self) -> dict[str, object]:
        parsed = json.loads(self.body)
        assert isinstance(parsed, dict)
        return parsed

    def trace_frame(self) -> dict[str, object] | None:
        """The ``amf.trace`` SSE payload, when the response carried one."""
        marker = b"event: amf.trace\ndata: "
        if marker not in self.body:
            return None
        raw = self.body.split(marker, 1)[1].split(b"\n", 1)[0]
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)
        return parsed


def stages_of(console: Mapping[str, object]) -> dict[str, dict[str, object]]:
    trace = console["pipeline_trace"]
    assert isinstance(trace, dict)
    return {s["name"]: s for s in trace["stages"]}


def build_app(
    provider: object,
    *,
    mutate_deps: Callable[[dict[str, object]], None] | None = None,
) -> tuple[object, list[AuditRecord], dict[str, object]]:
    env = {
        "AMF_DEV_PROFILE": "1",
        "AMF_DEV_API_KEY": KEY,
        "AMF_DEV_PLAN_PATH": str(EXAMPLE_PLAN),
        "AMF_DEV_MODELS": "echo-1",
        "AMF_DEV_Q_SAFE": "500",
        "AMF_DEV_AUDIT_DRAIN_RATE_PER_S": "1000",
        "AMF_DEV_AUDIT_BYTES_PER_RECORD": "2765",
        "AMF_DEV_BUDGET": "unlimited",
    }
    deps, _logs = serve.build_boot_dependencies(contract=contract(), env=env)
    records: list[AuditRecord] = []

    def _emit(record: AuditRecord) -> bool:
        records.append(record)
        return True

    deps["provider"] = provider
    deps["audit"] = _emit
    if mutate_deps is not None:
        mutate_deps(deps)
    return serve._assemble_app(deps), records, deps


async def post(
    app: object,
    records: list[AuditRecord],
    content: str = "hello",
    *,
    stream: bool = False,
    auth: str | None = f"Bearer {KEY}",
    path: str = "/v1/chat/completions",
    body: bytes | None = None,
) -> Result:
    payload = body or json.dumps(
        {"model": "echo-1", "stream": stream, "messages": [{"role": "user", "content": content}]}
    ).encode()
    headers = [(b"content-type", b"application/json")]
    if auth is not None:
        headers.append((b"authorization", auth.encode()))
    scope = {"type": "http", "method": "POST", "path": path, "headers": headers}
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
    before = len(records)
    await app(scope, _receive, _send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    raw_headers = start.get("headers", [])
    assert isinstance(raw_headers, list)
    headers_out = {bytes(k).decode().lower(): bytes(v).decode() for k, v in raw_headers}
    chunks = b"".join(
        bytes(m.get("body", b""))  # type: ignore[arg-type]
        for m in sent
        if m["type"] == "http.response.body"
    )
    return Result(int(str(start["status"])), headers_out, chunks, records[before:])


def run(coro: object) -> Result:
    return asyncio.run(coro)  # type: ignore[arg-type]
