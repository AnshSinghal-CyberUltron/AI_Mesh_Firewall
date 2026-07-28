"""Streaming governance / trace FIDELITY (telemetry + trace only; NOT enforcement).

These lock the four SSE-streaming reporting fixes. None of them change the actual
redaction/blocking of streamed bytes — they assert the telemetry event + pipeline
trace HONESTLY describe what the OUTPUT guard did:

  FIX 1  no phantom output-guard label — a clean output stream leaves the
         output_guardrail STAGE action = "allow" even when the INPUT was redacted.
  FIX 2  governance-log bucketing — when the output guard acts, the stream event is
         emitted as event_type="output_guard" (§1.7); clean streams keep
         "stream_complete" so the Output Governance Log is not flooded.
  FIX 3  input_scan_ms — the stream pipeline_trace reports real input-scan latency
         when stage_metrics carries tier1_ms/tier2_ms.
  FIX 4  raw_output — the pre-redaction model text is surfaced in the stream event
         (scrubbed through redact_all at the telemetry audit choke).
"""
from __future__ import annotations

import json

import pytest

from ai_mesh_gateway.secure_streaming import SecureStreamingResponse
from ai_mesh_gateway.stream_orchestration import (
    StreamFinalizeHooks,
    StreamLaunchContext,
    StreamRunMetrics,
    build_stream_trace_frame,
    finalize_stream,
)


# ── harness (mirrors tests/test_stream_trace_frame.py) ─────────────────────


def _ctx(**overrides) -> StreamLaunchContext:
    defaults = dict(
        body={"model": "gpt-4o-mini"},
        redacted_prompt=None,
        org_slug="acme",
        model="gpt-4o-mini",
        request_id="zs-stream-fidelity",
    )
    defaults.update(overrides)
    return StreamLaunchContext(**defaults)


def _frame(ctx, metrics, base_zeroshield, **kw) -> dict:
    raw = build_stream_trace_frame(ctx, metrics, base_zeroshield, **kw)
    return json.loads(raw.strip()[6:])


def _stage_map(trace: dict) -> dict[str, dict]:
    return {s["name"]: s for s in trace.get("stages") or []}


class _Capture:
    """Captures the kwargs of every emit_telemetry call."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, **kwargs) -> None:
        self.events.append(kwargs)


class _CleanScanner:
    def redact_pii(self, text: str) -> str:
        return text

    async def scan_output(self, text: str):
        class V:
            threat_type = ""
            matched_patterns = []

        return V()


# ── FIX 1: phantom output-guard label ──────────────────────────────────────


def test_clean_output_no_phantom_output_guard_redact():
    """Symptom: 'output guardrails redacted nothing but shows redacted'.

    The INPUT prompt was redacted (base zeroshield.action = "redact"), but the OUTPUT
    guard did nothing. The output_guardrail STAGE must NOT inherit the input redact.
    """
    metrics = StreamRunMetrics()  # guard_action == "", output_blocked == False
    pt_base = {
        "stages": [
            {"name": "input_scan", "action": "redact", "detail": "input ssn redacted"},
            {"name": "output_guardrail", "action": "allow"},
        ]
    }
    frame = _frame(
        _ctx(redacted_prompt="my ssn is [REDACTED]"),
        metrics,
        {"action": "redact", "detail": "input ssn redacted"},
        pipeline_trace_base=pt_base,
    )
    # Top-level unchanged: the request overall DID redact — the INPUT.
    assert frame["zeroshield"]["action"] == "redact"
    stages = _stage_map(frame["pipeline_trace"])
    # The OUTPUT guardrail stage must stay "allow" — no phantom redact.
    assert stages["output_guardrail"]["action"] == "allow"
    # The input-redaction reason must not leak onto the output stage.
    assert stages["output_guardrail"].get("detail") != "input ssn redacted"
    # Non-mutating: the caller's base trace is untouched.
    assert pt_base["stages"][1]["action"] == "allow"


def test_frame_output_guard_redact_stamps_output_stage():
    """Positive: when the OUTPUT guard actually redacts, its OWN action + detail land on
    the output_guardrail stage (driven by metrics.guard_action, not zs.action)."""
    metrics = StreamRunMetrics()
    metrics.record_guard_action("redact", threat_type="pii", detail="email masked")
    pt_base = {
        "stages": [
            {"name": "input_scan", "action": "allow"},
            {"name": "output_guardrail", "action": "allow"},
        ]
    }
    frame = _frame(_ctx(), metrics, {"action": "allow"}, pipeline_trace_base=pt_base)
    stages = _stage_map(frame["pipeline_trace"])
    assert stages["output_guardrail"]["action"] == "redact"
    assert stages["output_guardrail"]["detail"] == "email masked"


# ── FIX 2: governance-log bucketing (event_type) ───────────────────────────


@pytest.mark.asyncio
async def test_output_guard_redact_stamps_stage_and_buckets_output_guard():
    cap = _Capture()
    metrics = StreamRunMetrics()
    metrics.record_guard_action("redact", threat_type="pii", detail="email masked")
    metrics.append_output("Contact [REDACTED]")
    pt_base = {
        "stages": [
            {"name": "input_scan", "action": "allow"},
            {"name": "output_guardrail", "action": "allow"},
        ]
    }
    await finalize_stream(
        _ctx(),
        metrics,
        StreamFinalizeHooks(emit_telemetry=cap),
        pipeline_trace=pt_base,
    )
    assert len(cap.events) == 1
    ev = cap.events[0]
    # Bucketed into the Output Governance Log (§1.7).
    assert ev["event_type"] == "output_guard"
    # The PERSISTED pipeline_trace output stage reflects the guard's OWN redact.
    stages = _stage_map(ev["metadata"]["pipeline_trace"])
    assert stages["output_guardrail"]["action"] == "redact"
    assert stages["output_guardrail"]["detail"] == "email masked"
    # Non-mutating stamping: base trace untouched.
    assert pt_base["stages"][1]["action"] == "allow"


@pytest.mark.asyncio
async def test_output_blocked_buckets_output_guard_and_stamps_block():
    cap = _Capture()
    metrics = StreamRunMetrics(output_blocked=True)
    metrics.record_guard_action("block", threat_type="secret", detail="api key")
    metrics.append_raw_output("here is sk-live-abc")  # blocked => no client-facing output
    pt_base = {"stages": [{"name": "output_guardrail", "action": "allow"}]}
    await finalize_stream(
        _ctx(),
        metrics,
        StreamFinalizeHooks(emit_telemetry=cap),
        pipeline_trace=pt_base,
    )
    ev = cap.events[0]
    assert ev["event_type"] == "output_guard"
    stages = _stage_map(ev["metadata"]["pipeline_trace"])
    assert stages["output_guardrail"]["action"] == "block"
    # FIX 4: raw output surfaces even on a block (output_snippet is empty there).
    assert ev["metadata"]["raw_output"] == "here is sk-live-abc"


# ── FIX 2 anti-flood: clean / allow streams keep stream_complete ───────────


@pytest.mark.asyncio
async def test_clean_stream_keeps_stream_complete_event_type():
    cap = _Capture()
    metrics = StreamRunMetrics()
    metrics.append_output("Hello there, all good.")
    await finalize_stream(_ctx(), metrics, StreamFinalizeHooks(emit_telemetry=cap))
    assert len(cap.events) == 1
    assert cap.events[0]["event_type"] == "stream_complete"


@pytest.mark.asyncio
async def test_input_redacted_but_clean_output_stays_stream_complete():
    """Anti-flood: an INPUT redaction must NOT push a clean-output stream into §1.7,
    yet the top-level verdict still records the input redaction."""
    cap = _Capture()
    metrics = StreamRunMetrics()  # output guard did nothing
    await finalize_stream(
        _ctx(redacted_prompt="my ssn is [REDACTED]"),
        metrics,
        StreamFinalizeHooks(emit_telemetry=cap),
    )
    ev = cap.events[0]
    assert ev["event_type"] == "stream_complete"
    assert ev["action"] == "redact"  # input-redaction verdict fidelity preserved


# ── FIX 3: input_scan_ms ────────────────────────────────────────────────────


def test_pipeline_trace_input_scan_ms_from_tier_metrics():
    """The stream trace's input_scan latency is 0 without stage_metrics (the '<1ms'
    symptom) and reports tier1_ms+tier2_ms once they are forwarded."""
    from ai_mesh_gateway.pipeline_trace import build_pipeline_trace

    trace0 = build_pipeline_trace(prompt="hi", stage_metrics=None, final_action="allow")
    assert _stage_map(trace0)["input_scan"]["latency_ms"] == 0

    trace1 = build_pipeline_trace(
        prompt="hi", stage_metrics={"tier1_ms": 3.2, "tier2_ms": 8.5}, final_action="allow"
    )
    lat = _stage_map(trace1)["input_scan"]["latency_ms"]
    assert lat > 0
    assert abs(lat - 11.7) < 0.5


@pytest.mark.asyncio
async def test_launch_stream_forwards_stage_metrics_to_pipeline_trace(monkeypatch):
    """FIX 3 plumbing: _launch_chat_stream_response accepts stage_metrics and forwards it
    to build_pipeline_trace (which previously omitted it, hard-zeroing input_scan_ms)."""
    from ai_mesh_gateway import main as gateway_main
    import pipeline_trace as bare_pt  # the SAME module main.py imports build_pipeline_trace from

    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return {"stages": []}

    monkeypatch.setattr(bare_pt, "build_pipeline_trace", _capture)
    # main.CONFIG is populated at app startup; a bare test import leaves it None, so
    # supply an empty config for the org_config.get(..., CONFIG.get(...)) defaults.
    monkeypatch.setattr(gateway_main, "CONFIG", {})

    class _Req:
        headers = {}
        client = None

    # Firewall-disabled path (secure_output_scan=False) avoids scanner/guard/LLM deps;
    # the returned StreamingResponse does not iterate the generator, so no I/O runs.
    gateway_main._launch_chat_stream_response(
        request=_Req(),
        body={"model": "gpt-4o-mini", "stream": True},
        org_config={},
        org_slug="acme",
        auth_ctx=None,
        redacted_prompt=None,
        secure_output_scan=False,
        stage_metrics={"tier1_ms": 3.2, "tier2_ms": 8.5},
    )
    assert captured.get("stage_metrics") == {"tier1_ms": 3.2, "tier2_ms": 8.5}


# ── FIX 4: raw_output ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_raw_output_present_and_distinct_from_response_snippet():
    cap = _Capture()
    metrics = StreamRunMetrics()
    metrics.record_guard_action("redact", threat_type="pii")
    metrics.append_output("Contact [REDACTED]")        # post-redaction (client-facing)
    metrics.append_raw_output("Contact john@acme.com")  # pre-redaction (raw model)
    await finalize_stream(_ctx(), metrics, StreamFinalizeHooks(emit_telemetry=cap))
    md = cap.events[0]["metadata"]
    assert md["raw_output"] == "Contact john@acme.com"
    assert md["response_snippet"] == "Contact [REDACTED]"


@pytest.mark.asyncio
async def test_secure_stream_captures_pre_redaction_raw_output():
    """FIX 4 accumulator: SecureStreamingResponse records the pre-redaction full_text
    onto StreamRunMetrics.raw_output_snippet at flush time."""
    async def inner():
        yield 'data: {"choices":[{"delta":{"content":"hello world. "}}]}\n\n'
        yield "data: [DONE]\n\n"

    metrics = StreamRunMetrics()
    secure = SecureStreamingResponse(
        inner_generator=inner(),
        scanner=_CleanScanner(),
        buffer_max_bytes=4096,
        max_buffer_chunks=8,
        output_guard=None,
        stream_metrics=metrics,
        enforcement_mode="block",
    ).__aiter__()
    _ = [c async for c in secure]
    assert "hello world." in metrics.raw_output_snippet
