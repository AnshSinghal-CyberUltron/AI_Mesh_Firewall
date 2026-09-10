"""P0.0 Counted_Sample classifier — fail closed on unlike traces.

Run: python3 -m pytest scripts/perf/e2e/test_p0_classify.py -q
"""
from __future__ import annotations

import uuid

import pytest

from p0_classify import (
    NINE_STAGES,
    TOKEN_FLOOR,
    build_chat_body,
    classify_sample,
    firewall_tax_ms,
    prompt_fingerprint,
)


def _stages(*, skip_model: bool = False, action: str = "allow"):
    out = []
    for name in NINE_STAGES:
        st_action = "skip" if skip_model and name in ("model_input", "model_output", "output_guardrail") else action
        out.append({"name": name, "action": st_action, "latency_ms": 0.0 if st_action == "skip" else 1.2})
    return out


def _allow_body(**over):
    body = {
        "id": "chatcmpl-loadtest-stub",
        "object": "chat.completion",
        "usage": {"prompt_tokens": 8, "completion_tokens": 32, "total_tokens": 40},
        "pipeline_trace": {
            "final_action": "allow",
            "total_latency_ms": 40.0,
            "stages": _stages(),
        },
        "zeroshield": {},
    }
    body.update(over)
    return body


def test_nine_stage_names_match_pipeline_contract():
    assert NINE_STAGES == (
        "auth",
        "kill_switch",
        "rate_limit",
        "policy",
        "input_scan",
        "model_routing",
        "model_input",
        "model_output",
        "output_guardrail",
    )


def test_allow_stub_200_is_counted():
    r = classify_sample(200, _allow_body(), stream=False)
    assert r.class_name == "counted"
    assert r.counted is True


def test_http_400_content_filter_with_trace_is_not_counted():
    body = {
        "error": {"code": "content_filter", "message": "blocked"},
        "pipeline_trace": {
            "final_action": "block",
            "total_latency_ms": 8.0,
            "stages": _stages(skip_model=True, action="block"),
        },
    }
    r = classify_sample(400, body, stream=False)
    assert r.counted is False
    assert r.class_name == "http_400"
    assert firewall_tax_ms(body["pipeline_trace"]) == 8.0  # wall − 0 — the cheap-tax lie if counted


def test_redact_200_is_not_counted():
    body = _allow_body()
    body["pipeline_trace"]["final_action"] = "redact"
    r = classify_sample(200, body, stream=False)
    assert r.counted is False
    assert r.class_name == "redact"


@pytest.mark.parametrize("action", ["flag", "rewrite"])
def test_flag_rewrite_200_is_not_counted(action):
    body = _allow_body()
    body["pipeline_trace"]["final_action"] = action
    r = classify_sample(200, body, stream=False)
    assert r.counted is False
    assert r.class_name == "flag_or_rewrite"


def test_scan_only_200_is_not_counted():
    body = _allow_body(id="chatcmpl-scan-xyz")
    body["zeroshield"] = {"scan_only": True}
    body["usage"] = {"completion_tokens": 0}
    r = classify_sample(200, body, stream=False)
    assert r.counted is False
    assert r.class_name == "scan_only"


def test_stream_and_ttft_are_not_counted():
    body = _allow_body()
    assert classify_sample(200, body, stream=True).class_name == "stream"
    body["pipeline_trace"]["ttft_ms"] = 12.0
    assert classify_sample(200, body, stream=False).class_name == "stream"


def test_short_completion_tokens_not_counted_even_if_request_max_tokens_32():
    body = _allow_body()
    body["usage"]["completion_tokens"] = 16
    r = classify_sample(200, body, stream=False)
    assert r.counted is False
    assert r.class_name == "short_tokens"
    assert TOKEN_FLOOR == 32


def test_skip_model_stage_is_not_counted():
    body = _allow_body()
    body["pipeline_trace"]["stages"] = _stages(skip_model=True)
    r = classify_sample(200, body, stream=False)
    assert r.counted is False
    assert r.class_name == "skip_model"


@pytest.mark.parametrize("status,cls", [(401, "http_401"), (403, "http_403"), (429, "http_429"), (503, "http_503"), (0, "status_0")])
def test_error_statuses(status, cls):
    r = classify_sample(status, _allow_body(), stream=False)
    assert r.counted is False
    assert r.class_name == cls


def test_http_201_is_not_counted_only_200():
    r = classify_sample(201, _allow_body(), stream=False)
    assert r.counted is False
    assert r.class_name == "http_not_200"


def test_firewall_tax_nonstream_subtracts_model_output():
    trace = {
        "total_latency_ms": 100.0,
        "stages": [
            {"name": "input_scan", "latency_ms": 10.0, "action": "allow"},
            {"name": "model_output", "latency_ms": 70.0, "action": "allow"},
            {"name": "output_guardrail", "latency_ms": 20.0, "action": "allow"},
        ],
    }
    assert firewall_tax_ms(trace) == 30.0


def test_firewall_tax_none_on_stream_fixture():
    trace = {"total_latency_ms": 50.0, "ttft_ms": 12.0, "stages": []}
    assert firewall_tax_ms(trace, stream=True) is None


def test_build_chat_body_pins_factory():
    nonce = str(uuid.uuid4())
    body = build_chat_body(nonce)
    assert body["max_tokens"] == 32
    assert body["stream"] is False
    assert body["enable_routing"] is False
    content = body["messages"][0]["content"]
    assert nonce in content
    assert len(content.encode("utf-8")) < 256


def test_two_wire_bodies_differ():
    a = build_chat_body(str(uuid.uuid4()))
    b = build_chat_body(str(uuid.uuid4()))
    assert prompt_fingerprint(a) != prompt_fingerprint(b)
