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
