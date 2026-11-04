"""GW35: a stream cut is an error for a fault and a block for a policy cut; a kill is unchanged."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

import pytest

from gateway_v2.audit.record import AuditRecord
from gateway_v2.domain import posture
from gateway_v2.domain.decision import Decision, Disposition
from gateway_v2.edge.audit_emit import emit_chat_audit, note_stream_output
from gateway_v2.edge.cancel import KillLatch
from gateway_v2.edge.produced_holder import ProducedModeHolder, record_cut
from gateway_v2.edge.request_holds import RequestHolds
from gateway_v2.edge.routes import ChatRoute, _TerminalLatch  # type: ignore[attr-defined]
from gateway_v2.edge.wire.sse import SSEEncoder
from gateway_v2.egress.stream import StreamStats
from gateway_v2.egress.strict import StrictStats

_ALLOW = Decision(
    disposition=Disposition.ALLOW, per_finding=(), transformations=(), findings=(),
    plan_version="1.2.test", deciding_rules=(), unavailable_detectors=(),
)


def _holds(*, dispatched: bool = True) -> RequestHolds:
    holds = RequestHolds()
    holds.note_tenant("org-a")
    holds.note_request("zs-org-a-1")
    holds.note_identity_resolved()
    holds.note_plan("1.2.test")
    holds.note_input(_ALLOW)
    if dispatched:
        holds.mark_dispatched()
    return holds


def _finish(holds: RequestHolds, stats: object, reason: str | None, *, terminal: bool = False
            ) -> tuple[dict[str, object] | None, list[AuditRecord]]:
    produced = ProducedModeHolder(holds)
    produced.stats = stats  # type: ignore[assignment]
    if stats is not None:
        note_stream_output(holds, stats, "1.2.test")
    latch = KillLatch()
    if reason is not None:
        latch.kill(reason)
    encoder = SSEEncoder()
    sent = _TerminalLatch()
    if terminal:
        encoder.error(reason or "x")
        sent.set()
    frames: list[bytes] = []

    async def send(event: Mapping[str, object]) -> None:
        body = event.get("body")
        if event.get("type") == "http.response.body" and isinstance(body, (bytes, bytearray)):
            frames.append(bytes(body))

    asyncio.run(ChatRoute._finish_stream(None, send, encoder, latch, sent, produced))  # type: ignore[arg-type]
    payload = None
    for frame in frames:
        if frame.startswith(b"event: amf.trace\n"):
            payload = json.loads(frame.split(b"data: ", 1)[1].split(b"\n", 1)[0])
    records: list[AuditRecord] = []
    emit_chat_audit(lambda r: records.append(r) or True, holds, None)
    return payload, records


def _stages(payload: Mapping[str, object]) -> dict[str, tuple[str, bool]]:
    trace = payload["pipeline_trace"]
    return {s["name"]: (s["action"], s["ran"]) for s in trace["stages"]}  # type: ignore[index]


@pytest.mark.parametrize(
    "code",
    [
        posture.STREAM_INTER_CHUNK_TIMEOUT,
        posture.STREAM_IDLE_TIMEOUT,
        posture.STREAM_WRITE_TIMEOUT,
        posture.STREAM_MAX_DURATION,
    ],
)
def test_an_operational_cut_is_model_output_error_and_never_an_output_block(code: str) -> None:
    holds = _holds()
    payload, records = _finish(holds, StreamStats(output_action="allow"), code)
    assert payload is not None
    stages = _stages(payload)
    assert stages["model_output"] == ("error", True)
    assert payload["zeroshield"]["action"] == "error"  # type: ignore[index]
    assert "block" not in {a for a, _ in stages.values()}
    assert holds.output_action in ("", "allow") and holds.failure_code == code
    for record in records:
        assert record.detail["console"]["pipeline_trace"] == payload["pipeline_trace"]  # type: ignore[index]


@pytest.mark.parametrize("code", [posture.STREAM_KEY_REVOKED, posture.STREAM_PLAN_CHANGED])
def test_a_policy_cut_is_an_output_block(code: str) -> None:
    holds = _holds()
    payload, records = _finish(holds, StreamStats(output_action="allow"), code)
    assert payload is not None
    stages = _stages(payload)
    assert stages["output_guardrail"] == ("block", True)
    assert payload["zeroshield"]["action"] == "block"  # type: ignore[index]
    assert all(r.detail["console"]["zeroshield"] == payload["zeroshield"] for r in records)  # type: ignore[index]


@pytest.mark.parametrize("code", [posture.STREAM_KILLED, "killed"])
def test_a_kill_switch_cut_records_nothing(code: str) -> None:
    holds = _holds()
    payload, _ = _finish(holds, StreamStats(output_action="allow"), code)
    assert payload is not None
    assert holds.failure_code == "" and holds.output_action == ""
    assert payload["zeroshield"]["action"] == "allow"  # type: ignore[index]
    assert set(_stages(payload).values()) == {("allow", True)}


def test_record_cut_never_forwards_error_to_the_output_action() -> None:
    holds = _holds()
    produced = ProducedModeHolder(holds)
    record_cut(produced, posture.STREAM_MALFORMED_UPSTREAM)
    assert holds.output_action == ""
    holds.note_output_action("error")
    holds.note_output_action(None)
    holds.note_output_action("")
    assert holds.output_action == ""


def test_in_stream_plan_unavailable_is_a_routing_error_before_dispatch() -> None:
    holds = _holds(dispatched=False)
    payload, records = _finish(holds, None, posture.PLAN_UNAVAILABLE)
    assert payload is not None
    stages = _stages(payload)
    assert stages["model_routing"] == ("error", True)
    assert stages["model_output"] == ("skip", False)
    assert stages["output_guardrail"] == ("skip", False)
    assert payload["zeroshield"]["action"] == "error"  # type: ignore[index]
    assert records and records[0].detail["console"]["pipeline_trace"] == payload["pipeline_trace"]  # type: ignore[index]


@pytest.mark.parametrize(
    ("code", "guard", "final"),
    [
        (posture.STREAM_MALFORMED_UPSTREAM, ("skip", False), "error"),
        (posture.STREAM_INTER_CHUNK_TIMEOUT, ("skip", False), "error"),
        (posture.STREAM_STRICT_BUFFER_OVERFLOW, ("block", True), "block"),
    ],
)
def test_strict_pipeline_failures_rely_on_the_failure_code(
    code: str, guard: tuple[str, bool], final: str,
) -> None:
    holds = _holds()
    payload, records = _finish(
        holds, StrictStats(incomplete=True, error=code), None, terminal=True,
    )
    assert payload is not None
    assert _stages(payload)["output_guardrail"] == guard
    assert payload["zeroshield"]["action"] == final  # type: ignore[index]
    assert all(r.detail["console"]["zeroshield"] == payload["zeroshield"] for r in records)  # type: ignore[index]


def test_a_pipeline_output_block_with_a_decision_keeps_its_rule() -> None:
    holds = _holds()
    blocked = Decision(
        disposition=Disposition.BLOCK, per_finding=(), transformations=(), findings=(),
        plan_version="1.2.test", deciding_rules=("fw-pii-output",), unavailable_detectors=(),
    )
    stats = StreamStats(error="output_blocked", incomplete=True, output_decision=blocked,
                        output_action="block")
    payload, _ = _finish(holds, stats, None, terminal=True)
    assert payload is not None
    guard = next(s for s in payload["pipeline_trace"]["stages"] if s["name"] == "output_guardrail")  # type: ignore[index]
    assert guard["action"] == "block" and guard["deciding_rule"] == "fw-pii-output"


def test_a_stream_refused_before_the_provider_opens_is_a_routing_error_in_the_audit() -> None:
    holds = _holds(dispatched=False)
    holds.note_failure(posture.STREAM_BUFFER_UNAVAILABLE)
    records: list[AuditRecord] = []
    emit_chat_audit(lambda r: records.append(r) or True, holds, None)
    console = records[0].detail["console"]
    stages = {s["name"]: (s["action"], s["ran"]) for s in console["pipeline_trace"]["stages"]}  # type: ignore[index]
    assert stages["model_routing"] == ("error", True)
    assert console["zeroshield"]["action"] == "error"  # type: ignore[index]
