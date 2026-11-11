"""DEV PROFILE in-process pipeline: what the PROVIDER receives vs what the CLIENT receives.

Drives the dev-profile app over raw ASGI with a recording provider so the two sides of the model
boundary can be asserted separately — something the echo/e2e test cannot distinguish, because the
output guard masks the echoed reply either way.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest

from gateway_v2.dispatch.provider import UpstreamEvent, UpstreamRequest
from gateway_v2.dispatch.transform import BODY_DELTAS_KEY
from gateway_v2.edge import serve
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

EXAMPLE_PLAN = Path(__file__).resolve().parents[2] / "dev" / "plan.example.json"
SSN = "123-45-6789"
KEY = "sk-dev-pipeline"


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


class _RecordingEcho:
    """Records every forwarded body and echoes it (same reply shape as the dev echo)."""

    def __init__(self) -> None:
        self.received: list[str] = []

    def open(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        return self._stream(request)

    async def _stream(self, request: UpstreamRequest) -> AsyncIterator[UpstreamEvent]:
        deltas = request.body.get(BODY_DELTAS_KEY, ())
        assert isinstance(deltas, tuple)
        text = "\n".join(str(item[1]) for item in deltas)
        self.received.append(text)
        yield UpstreamEvent(text_deltas=(("content", "echo: " + text),), final=True)

    async def abort(self) -> None:
        return None


def _app(provider: _RecordingEcho) -> object:
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
    deps, _logs = serve.build_boot_dependencies(contract=_contract(), env=env)
    deps["provider"] = provider
    return serve._assemble_app(deps)


async def _post(app: object, content: str, *, stream: bool) -> tuple[int, bytes]:
    body = json.dumps(
        {"model": "echo-1", "stream": stream, "messages": [{"role": "user", "content": content}]}
    ).encode()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": [(b"authorization", f"Bearer {KEY}".encode()), (b"content-type", b"json")],
    }
    sent: list[Mapping[str, object]] = []
    delivered = False

    async def _receive() -> Mapping[str, object]:
        nonlocal delivered
        if delivered:
            await asyncio.sleep(3600)
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def _send(message: Mapping[str, object]) -> None:
        sent.append(message)

    assert callable(app)
    await app(scope, _receive, _send)
    status = next(int(str(m["status"])) for m in sent if m["type"] == "http.response.start")
    payload = b"".join(
        bytes(m.get("body", b""))  # type: ignore[arg-type]
        for m in sent
        if m["type"] == "http.response.body"
    )
    return status, payload


@pytest.mark.parametrize("stream", [False, True])
def test_client_never_sees_raw_ssn_and_aws_key_is_blocked(stream: bool) -> None:
    provider = _RecordingEcho()
    app = _app(provider)
    status, payload = asyncio.run(_post(app, f"My SSN is {SSN} thanks", stream=stream))
    assert status == 200
    assert SSN.encode() not in payload and b"[REDACTED]" in payload
    status, payload = asyncio.run(_post(app, "key AKIAIOSFODNN7EXAMPLE", stream=stream))
    assert b"input_blocked" in payload
    assert len(provider.received) == 1, "a BLOCKed input must make zero provider calls"


def test_input_redact_masks_pii_before_the_provider() -> None:
    provider = _RecordingEcho()
    asyncio.run(_post(_app(provider), f"My SSN is {SSN} thanks", stream=False))
    assert provider.received and SSN not in provider.received[0]
