"""_error_body must prefer the FULL HTTP body over the nested OpenAI error object."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _load_client():
    path = ROOT / "zeroshield_client.py"
    if "openai" not in sys.modules:
        stub = ModuleType("openai")

        class _OpenAI:  # noqa: N801
            pass

        class _APIStatusError(Exception):
            def __init__(self, message="blocked", body=None, response=None, status_code=400):
                super().__init__(message)
                self.body = body
                self.response = response
                self.status_code = status_code

        stub.OpenAI = _OpenAI
        stub.APIStatusError = _APIStatusError
        sys.modules["openai"] = stub
    spec = importlib.util.spec_from_file_location("zeroshield_client_err_body", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


mod = _load_client()


def test_error_body_prefers_full_response_over_nested_sdk_body():
    """SDK sets e.body to nested error{}; gateway puts pipeline_trace at top level."""
    full = {
        "error": {
            "message": "Request blocked due to security policy",
            "type": "invalid_request_error",
            "param": None,
            "code": "content_filter",
        },
        "message": "Request blocked due to security policy",
        "code": "input_blocked",
        "category": "jailbreak",
        "blocked_by": "input_scan",
        "request_id": "zs-abc",
        "pipeline_trace": {
            "final_action": "block",
            "total_latency_ms": 12.5,
            "stages": [
                {"name": "input_scan", "action": "block", "threat_type": "jailbreak",
                 "guard_reason": "Injection detected"},
            ],
        },
    }
    nested_only = full["error"]
    exc = mod.APIStatusError(
        "Error",
        body=nested_only,
        response=SimpleNamespace(text=json.dumps(full)),
        status_code=400,
    ) if hasattr(mod, "APIStatusError") else None
    # Use the stub class from openai module
    from openai import APIStatusError
    exc = APIStatusError(
        "Error",
        body=nested_only,
        response=SimpleNamespace(text=json.dumps(full)),
        status_code=400,
    )
    body = mod._error_body(exc)
    assert body.get("pipeline_trace"), "must keep top-level pipeline_trace"
    assert body.get("category") == "jailbreak"
    assert body.get("request_id") == "zs-abc"
    assert body.get("blocked_by") == "input_scan"


def test_blocked_result_surfaces_message_and_stages():
    from openai import APIStatusError
    full = {
        "error": {"message": "Request blocked due to security policy", "code": "content_filter",
                  "type": "invalid_request_error", "param": None},
        "message": "Request blocked due to security policy",
        "code": "input_blocked",
        "category": "jailbreak",
        "blocked_by": "input_scan",
        "request_id": "zs-abc",
        "pipeline_trace": {
            "final_action": "block",
            "stages": [
                {"name": "input_scan", "action": "block", "threat_type": "jailbreak",
                 "guard_reason": "Injection detected"},
            ],
        },
    }
    exc = APIStatusError(
        "Error",
        body=full["error"],
        response=SimpleNamespace(text=json.dumps(full)),
        status_code=400,
    )
    out = mod._blocked_result(exc)
    assert out["blocked"] is True
    assert out["message"] == "Request blocked due to security policy"
    assert out["trace"]["action"] == "block"
    assert out["trace"]["threat_type"] == "jailbreak"
    assert out["trace"]["guard_reason"]
    assert len(out["trace"]["stages"]) == 1
    assert out["trace"]["stages"][0]["name"] == "input_scan"
