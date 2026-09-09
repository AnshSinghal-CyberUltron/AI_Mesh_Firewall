"""The trace projection must shrink bytes without breaking the honesty checks — task 6.

MEASURED (one allowed non-streaming request, 3.5 KB prompt): the response is 38,225 B, of
which pipeline_trace is 35,572 B — 91%. Inside it, `stages` is 28,354 B and 23,538 B of
that is echoed text. Reduced to name/action/latency the stages are 587 B; a metrics-only
trace is 684 B, a 52x reduction.

R2 is the constraint that matters here. `drive.py` and `load.py` assert all nine stages
ran and read per-stage latencies; the nine-stage assertion is what stops a SHORTER
pipeline being reported as a full one. A projection that dropped those fields would
silently disable that check while appearing to work.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ai_mesh_gateway"))

from ai_mesh_gateway.trace_projection import (  # noqa: E402
    HARNESS_REQUIRED_ROOT_KEYS,
    HARNESS_REQUIRED_STAGE_KEYS,
    TEXT_PAYLOAD_KEYS,
    project_pipeline_trace,
)

STAGES = ("auth", "rate_limit", "policy", "input_scan", "kill_switch",
          "model_routing", "model_input", "model_output", "output_guardrail")


def _trace() -> dict:
    big = "summarise the deployment runbook. " * 40
    return {
        "stages": [
            {"name": n, "action": "allow", "latency_ms": 1.25, "threat_type": "none",
             "category": "none", "content": big, "prompt_in": big, "prompt_out": big,
             "matched_patterns": ["p1"]}
            for n in STAGES
        ],
        "t_addon_pre_ms": 3.5, "t_addon_post_ms": 2.0,
        "total_ms": 505.0, "overhead_ms": 1.1,
        "input_text": big, "output_text": big, "final_response": big,
        "prompt_submitted": big, "prompt_preview": big,
        "guard_summary": {"findings": 0},
    }


def test_full_mode_is_identity_not_a_copy():
    """The default must cost one env lookup and change nothing."""
    t = _trace()
    assert project_pipeline_trace(t, mode="full") is t


def test_metrics_mode_keeps_everything_the_harness_reads():
    """R2: the nine-stage assertion and the tail attribution must still work."""
    out = project_pipeline_trace(_trace(), mode="metrics")

    names = [s["name"] for s in out["stages"]]
    assert names == list(STAGES), f"nine-stage assertion would break: got {names}"
    for s in out["stages"]:
        for k in HARNESS_REQUIRED_STAGE_KEYS:
            assert k in s, f"stage {s.get('name')!r} lost {k!r}; tail attribution breaks"
        assert isinstance(s["latency_ms"], (int, float))
    for k in HARNESS_REQUIRED_ROOT_KEYS:
        assert k in out, f"root lost {k!r}; the firewall tax becomes uncomputable"


def test_metrics_mode_drops_every_text_payload():
    out = project_pipeline_trace(_trace(), mode="metrics")
    for k in TEXT_PAYLOAD_KEYS:
        assert k not in out, f"root still carries text payload {k!r}"
    for s in out["stages"]:
        for k in TEXT_PAYLOAD_KEYS:
            assert k not in s, f"stage {s['name']!r} still carries text payload {k!r}"


def test_metrics_mode_is_dramatically_smaller():
    import json
    t = _trace()
    full = len(json.dumps(t))
    small = len(json.dumps(project_pipeline_trace(t, mode="metrics")))
    assert small < full / 10, (
        f"projection only got {full} B down to {small} B; the measured shape of this "
        f"payload is 98% text duplication, so a <10x reduction means text survived")


def test_projection_never_introduces_a_key():
    """R3: it removes fields. It must never add or rename one."""
    t = _trace()
    out = project_pipeline_trace(t, mode="metrics")
    assert set(out) <= set(t), f"projection added root keys: {set(out) - set(t)}"
    for src, dst in zip(t["stages"], out["stages"]):
        assert set(dst) <= set(src), f"projection added stage keys: {set(dst) - set(src)}"


@pytest.mark.parametrize("bad", [None, {}, {"stages": None}, {"stages": []}, "notadict"])
def test_degenerate_traces_do_not_raise(bad):
    """A trace is diagnostic data; projecting it must never be able to fail a request."""
    project_pipeline_trace(bad, mode="metrics")
    project_pipeline_trace(bad, mode="full")


def test_unknown_mode_falls_back_to_full():
    """An operator typo must not silently strip diagnostics."""
    t = _trace()
    assert project_pipeline_trace(t, mode="mtrics") is t
