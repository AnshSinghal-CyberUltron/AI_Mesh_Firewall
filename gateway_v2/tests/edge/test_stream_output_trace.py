"""A streamed output withhold still carries the six-stage console frame.

The pipeline emits ``output_blocked`` itself and sets the terminal latch. GW14b
§5.4 still requires the ``amf.trace`` object on that stream. The frame follows
the error and is not a content chunk.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

from gateway_v2.domain.decision import Decision, Disposition
from gateway_v2.edge.cancel import KillLatch
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.routes import (  # type: ignore[attr-defined]
    ChatRoute,
    _ProducedModeHolder,
    _TerminalLatch,
)
from gateway_v2.edge.wire.sse import SSEEncoder
from gateway_v2.egress.stream import StreamStats


class _Capture:
    __slots__ = ("frames",)

    def __init__(self) -> None:
        self.frames: list[bytes] = []

    async def send(self, event: Mapping[str, object]) -> None:
        if event.get("type") != "http.response.body":
            return
        body = event.get("body")
        if isinstance(body, (bytes, bytearray)) and body:
            self.frames.append(bytes(body))


def _decision(action: Disposition, rule: str) -> Decision:
    return Decision(
        disposition=action,
        per_finding=(),
        transformations=(),
        findings=(),
        plan_version="1.2.test",
        deciding_rules=(rule,) if rule else (),
        unavailable_detectors=(),
    )


def test_output_withhold_still_emits_the_six_stage_trace() -> None:
    holds = RequestHolds()
    holds.note_request("zs-output-block")
    holds.note_input(_decision(Disposition.ALLOW, ""))
    holds.note_output(_decision(Disposition.BLOCK, "fw-pii-output"))
    produced = _ProducedModeHolder(holds)
    produced.stats = StreamStats(
        output_action="block",
        output_decision=holds.output_decision,
    )
    encoder = SSEEncoder()
    encoder.error("output_blocked")
    sent = _TerminalLatch()
    sent.set()
    capture = _Capture()

    async def _drive() -> None:
        await ChatRoute._finish_stream(
            None,  # type: ignore[arg-type]
            capture.send,
            encoder,
            KillLatch(),
            sent,
            produced,
        )

    asyncio.run(_drive())

    assert len(capture.frames) == 1
    frame = capture.frames[0]
    assert frame.startswith(b"event: amf.trace\n")
    assert b'"content"' not in frame
    payload = json.loads(frame.split(b"data: ", 1)[1].split(b"\n", 1)[0])
    assert payload["zeroshield"]["action"] == "block"
    stages = {stage["name"]: stage for stage in payload["pipeline_trace"]["stages"]}
    assert list(stages) == [
        "auth",
        "rate_limit",
        "input_scan",
        "model_routing",
        "model_output",
        "output_guardrail",
    ]
    assert stages["input_scan"]["action"] == "allow"
    assert stages["model_output"]["ran"] is True
    assert stages["output_guardrail"]["action"] == "block"
    assert stages["output_guardrail"]["deciding_rule"] == "fw-pii-output"
    assert "kill_switch" not in stages
