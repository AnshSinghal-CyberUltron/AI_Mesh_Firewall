"""Phase 0.1 honesty: full_nine_stages is measured, not MODE==chat.

The capacity predicate must fail when the loadtest stub is in the numbers.
Run: gateway/.venv/bin/python -m pytest scripts/perf/test_gateway_pipeline_bench_honesty.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import gateway_pipeline_bench as bench  # noqa: E402


def _pct(p50: float) -> dict:
    return {"n": 10, "mean": p50, "p50": p50, "p90": p50, "p95": p50, "p99": p50, "max": p50}


def test_health_mode_is_not_full_nine_and_fails_capacity():
    h = bench.build_honesty_report(
        mode="health",
        stage_latency_ms={},
        unique_prompt=True,
        stub_llm_env="",
        completion_ids={},
    )
    assert h["full_nine_stages"] is False
    assert h["capacity_eligible"] is False
    assert h["capacity_predicate"] == "fail"
    assert "mode_not_chat" in h["capacity_fail_reasons"]


def test_chat_without_stage_samples_is_not_full_nine():
    """The old lie: MODE==chat implied full_nine_stages=True with no scan proof."""
    h = bench.build_honesty_report(
        mode="chat",
        stage_latency_ms={},
        unique_prompt=True,
        stub_llm_env="",
        completion_ids={"chatcmpl-real-xyz": 8},
    )
    assert h["full_nine_stages"] is False
    assert h["capacity_eligible"] is False
    assert "not_full_nine_stages" in h["capacity_fail_reasons"]


def test_scans_off_zero_input_scan_is_not_full_nine():
    h = bench.build_honesty_report(
        mode="chat",
        stage_latency_ms={
            "input_scan": _pct(0.0),
            "output_guardrail": _pct(4.2),
        },
        unique_prompt=True,
        stub_llm_env="",
        completion_ids={"chatcmpl-real-xyz": 8},
    )
    assert h["full_nine_stages"] is False


def test_missing_output_guardrail_is_not_full_nine():
    h = bench.build_honesty_report(
        mode="chat",
        stage_latency_ms={"input_scan": _pct(11.0)},
        unique_prompt=True,
        stub_llm_env="",
        completion_ids={"chatcmpl-real-xyz": 8},
    )
    assert h["full_nine_stages"] is False


def test_live_input_scan_and_output_guard_is_full_nine_and_capacity_pass():
    h = bench.build_honesty_report(
        mode="chat",
        stage_latency_ms={
            "input_scan": _pct(12.4),
            "output_guardrail": _pct(3.1),
        },
        unique_prompt=True,
        stub_llm_env="",
        completion_ids={"chatcmpl-abc": 10},
    )
    assert h["full_nine_stages"] is True
    assert h["stub_llm"] is False
    assert h["capacity_eligible"] is True
    assert h["capacity_predicate"] == "pass"
    assert h["capacity_fail_reasons"] == []


def test_stub_completion_id_fails_capacity_even_if_scans_ran():
    h = bench.build_honesty_report(
        mode="chat",
        stage_latency_ms={
            "input_scan": _pct(12.4),
            "output_guardrail": _pct(3.1),
        },
        unique_prompt=True,
        stub_llm_env="",
        completion_ids={"chatcmpl-loadtest-stub": 50},
    )
    assert h["full_nine_stages"] is True
    assert h["stub_llm"] is True
    assert h["capacity_eligible"] is False
    assert h["capacity_predicate"] == "fail"
    assert "stub_llm" in h["capacity_fail_reasons"]


def test_stub_env_fails_capacity_even_without_captured_ids():
    h = bench.build_honesty_report(
        mode="chat",
        stage_latency_ms={
            "input_scan": _pct(12.4),
            "output_guardrail": _pct(3.1),
        },
        unique_prompt=True,
        stub_llm_env="1",
        completion_ids={},
    )
    assert h["stub_llm"] is True
    assert h["capacity_eligible"] is False
    assert "stub_llm" in h["capacity_fail_reasons"]


def test_repeated_prompt_fails_capacity():
    h = bench.build_honesty_report(
        mode="chat",
        stage_latency_ms={
            "input_scan": _pct(12.4),
            "output_guardrail": _pct(3.1),
        },
        unique_prompt=False,
        stub_llm_env="",
        completion_ids={"chatcmpl-abc": 10},
    )
    assert h["full_nine_stages"] is True
    assert h["capacity_eligible"] is False
    assert "not_unique_prompt" in h["capacity_fail_reasons"]


def test_extract_completion_id_from_openai_body():
    assert bench.extract_completion_id({"id": "chatcmpl-loadtest-stub"}) == "chatcmpl-loadtest-stub"
    assert bench.extract_completion_id({"id": "chatcmpl-abc"}) == "chatcmpl-abc"
    assert bench.extract_completion_id({"error": {}}) is None
    assert bench.extract_completion_id("nope") is None
