"""Regression: when the OUTPUT guard acts on a stream, the stamped output_guardrail
stage must be internally coherent — action AND guard_action AND guard_reason all
reflect the guard's verdict, not a stale pre-stream "allow".
"""
from ai_mesh_gateway.stream_orchestration import _stamp_output_stage_action, StreamRunMetrics


def _base_trace():
    # An output_guardrail stage as built PRE-stream (guard hasn't run yet -> allow).
    return {
        "stages": [
            {"name": "input_scan", "action": "allow", "guard_action": "redact"},
            {
                "name": "output_guardrail",
                "action": "allow",
                "guard_action": "allow",
                "guard_reason": "ZeroShield Output Guard — enforcement: ALLOW",
            },
        ]
    }


def _output_stage(pt):
    return next(s for s in pt["stages"] if s["name"] == "output_guardrail")


def test_blocked_stream_output_stage_is_coherent():
    m = StreamRunMetrics()
    m.output_blocked = True
    m.record_guard_action("block", detail="advisory: mentions sensitive data")
    pt = _stamp_output_stage_action(_base_trace(), m)
    st = _output_stage(pt)
    assert st["action"] == "block"
    assert st["guard_action"] == "block", "guard_action must match the stamped action (no allow/block contradiction)"
    assert "BLOCK" in st["guard_reason"] and "ALLOW" not in st["guard_reason"]
    assert st["detail"] == "advisory: mentions sensitive data"


def test_redacted_stream_output_stage_is_coherent():
    m = StreamRunMetrics()
    m.record_guard_action("redact")
    pt = _stamp_output_stage_action(_base_trace(), m)
    st = _output_stage(pt)
    assert st["action"] == "redact"
    assert st["guard_action"] == "redact"
    assert "REDACT" in st["guard_reason"]


def test_clean_stream_leaves_output_stage_untouched():
    m = StreamRunMetrics()  # guard did not act
    pt = _stamp_output_stage_action(_base_trace(), m)
    st = _output_stage(pt)
    assert st["action"] == "allow"
    assert st["guard_action"] == "allow", "a clean stream must NOT stamp a phantom action"


def _base_trace_with_model_output():
    return {
        "stages": [
            {"name": "input_scan", "action": "allow"},
            {"name": "model_output", "action": "allow", "content": "", "detail": "No completion body"},
            {"name": "output_guardrail", "action": "allow", "guard_action": "allow"},
        ]
    }


def _stage(pt, name):
    return next(s for s in pt["stages"] if s["name"] == name)


def test_streamed_output_backfilled_into_model_output_stage():
    # Clean stream (guard allowed) that produced output -> the empty model_output
    # stage must be back-filled so the Scan Detail shows what the model produced.
    m = StreamRunMetrics()
    m.output_snippet = "I have processed the user record."
    pt = _stamp_output_stage_action(_base_trace_with_model_output(), m)
    mo = _stage(pt, "model_output")
    assert mo["content"] == "I have processed the user record."
    assert mo["detail"] == "Streamed model output"  # replaced "No completion body"
    assert pt.get("stream") is True
    assert pt.get("output_text") == "I have processed the user record."
    # guard did not act -> output_guardrail stays allow
    assert _stage(pt, "output_guardrail")["action"] == "allow"


def test_backfill_does_not_overwrite_existing_model_output_content():
    m = StreamRunMetrics()
    m.output_snippet = "reconstructed"
    base = _base_trace_with_model_output()
    _stage(base, "model_output")["content"] = "already captured (non-stream body)"
    pt = _stamp_output_stage_action(base, m)
    assert _stage(pt, "model_output")["content"] == "already captured (non-stream body)"


def test_no_output_and_no_guard_action_returns_trace_unchanged():
    m = StreamRunMetrics()  # no output_snippet, guard did not act
    base = _base_trace_with_model_output()
    pt = _stamp_output_stage_action(base, m)
    assert "stream" not in pt and "output_text" not in pt
    assert _stage(pt, "model_output")["content"] == ""
