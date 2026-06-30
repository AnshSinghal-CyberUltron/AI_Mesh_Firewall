"""
Tests for the bedrock_logger JSON formatter (BEDROCK_LOG_JSON env path).

bedrock_logger reads BEDROCK_LOG_JSON / BEDROCK_LOG_DIR at import time and
configures a module-level singleton logger, so each test reloads the module
with patched env vars (clearing the named logger's handlers first — otherwise
_setup_bedrock_logger early-returns and keeps the stale formatter).

Covers:
  * BEDROCK_LOG_JSON=true selects the _JSONFormatter on the file handler
  * log_bedrock_request emits parseable JSON lines with the expected fields,
    including the new call_site / api_method / backend keys
  * log_bedrock_response ditto (plus http_status, tokens, success)
  * call_site is omitted when not provided; api_method/backend default
  * BEDROCK_LOG_JSON=false falls back to the human-readable (non-JSON) format
"""
from __future__ import annotations

import importlib
import json
import logging
from logging.handlers import RotatingFileHandler

import pytest

import bedrock_logger as bedrock_logger_module


def _clear_bedrock_handlers() -> None:
    logger = logging.getLogger("bedrock")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def _read_json_lines(path):
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def _entries_with_event(path, event: str):
    return [e for e in _read_json_lines(path) if e.get("data", {}).get("event") == event]


@pytest.fixture()
def reload_logger(monkeypatch, tmp_path):
    """Factory: reload bedrock_logger with a given BEDROCK_LOG_JSON value.

    Returns (module, log_file_path). Teardown restores the original env and
    re-initializes the module so other gateway tests see default behavior.
    """

    def _reload(json_flag: str = "true"):
        monkeypatch.setenv("BEDROCK_LOG_JSON", json_flag)
        monkeypatch.setenv("BEDROCK_LOG_DIR", str(tmp_path))
        # Pin the preview-emission toggles this suite asserts on. They are
        # module-level globals read from env at import; another test in the full
        # suite can leave BEDROCK_LOG_PROMPT_PREVIEW / _OUTPUT_PREVIEW falsy in the
        # ambient env, so the reload below would silently disable the prompt/output
        # companion records and this test would fail only in-suite (not in isolation).
        monkeypatch.setenv("BEDROCK_LOG_PROMPT_PREVIEW", "true")
        monkeypatch.setenv("BEDROCK_LOG_OUTPUT_PREVIEW", "true")
        # Pin DEBUG too: the ``bedrock_output`` companion is logged at DEBUG, so an
        # ambient BEDROCK_LOG_LEVEL=INFO (left by another suite) would filter it and
        # this test would fail only in-suite. _clear_bedrock_handlers() above forces
        # _setup_bedrock_logger to re-run setLevel on reload, so this takes effect.
        monkeypatch.setenv("BEDROCK_LOG_LEVEL", "DEBUG")
        _clear_bedrock_handlers()
        mod = importlib.reload(bedrock_logger_module)
        return mod, tmp_path / "bedrock.log"

    yield _reload

    monkeypatch.undo()
    _clear_bedrock_handlers()
    importlib.reload(bedrock_logger_module)


def test_log_json_env_flag_selects_json_formatter(reload_logger):
    mod, _ = reload_logger("true")
    assert mod.LOG_JSON is True
    file_handlers = [
        h for h in mod.bedrock_log.handlers if isinstance(h, RotatingFileHandler)
    ]
    assert file_handlers, "expected a rotating file handler in BEDROCK_LOG_DIR"
    for handler in file_handlers:
        assert isinstance(handler.formatter, mod._JSONFormatter)


def test_log_bedrock_request_emits_json_with_call_site_fields(reload_logger):
    mod, log_file = reload_logger("true")
    mod.log_bedrock_request(
        request_id="req123abc456",
        model="anthropic.claude-3-haiku",
        region="ap-south-1",
        payload_bytes=2048,
        prompt_len=900,
        truncated_len=512,
        has_context=True,
        max_tokens=256,
        deployment_path="bedrock-direct",
        prompt_preview="hello\nworld",
        call_site="bedrock_scanner.scan",
        api_method="converse",
        backend="bedrock",
    )

    requests = _entries_with_event(log_file, "bedrock_request")
    assert len(requests) == 1
    entry = requests[0]

    # Envelope fields from _JSONFormatter
    assert entry["level"] == "INFO"
    assert entry["logger"] == "bedrock"
    assert "ts" in entry
    assert entry["msg"].startswith("BEDROCK REQUEST")

    data = entry["data"]
    assert data["request_id"] == "req123abc456"
    assert data["model"] == "zeroshield-guard"
    assert "region" not in data
    assert data["payload_bytes"] == 2048
    assert data["prompt_len"] == 900
    assert data["truncated_len"] == 512
    assert data["has_context"] is True
    assert data["max_tokens"] == 256
    assert data["deployment_path"] == "bedrock-direct"
    # New structured attribution fields
    assert data["call_site"] == "bedrock_scanner.scan"
    assert data["api_method"] == "converse"
    assert "backend" not in data
    # Preview is normalized (newlines collapsed) and present on the request entry
    assert data["prompt_preview"] == "hello world"

    # The companion prompt-preview record must also be valid JSON
    prompts = _entries_with_event(log_file, "bedrock_prompt")
    assert len(prompts) == 1
    assert prompts[0]["data"]["preview"] == "hello world"


def test_log_bedrock_response_emits_json_with_call_site_fields(reload_logger):
    mod, log_file = reload_logger("true")
    mod.log_bedrock_response(
        request_id="resp987zyx",
        model="anthropic.claude-3-haiku",
        region="ap-south-1",
        elapsed_s=1.23456,
        tokens_in=120,
        tokens_out=48,
        success=True,
        response_keys=["output", "usage"],
        http_status=200,
        output_preview="all clear",
        call_site="llm_router.completion",
        api_method="invoke_model",
        backend="sagemaker",
    )

    responses = _entries_with_event(log_file, "bedrock_response")
    assert len(responses) == 1
    entry = responses[0]
    assert entry["level"] == "INFO"
    assert entry["msg"].startswith("BEDROCK RESPONSE")

    data = entry["data"]
    assert data["request_id"] == "resp987zyx"
    assert data["elapsed_s"] == 1.235  # rounded to 3 decimals
    assert data["tokens_in"] == 120
    assert data["tokens_out"] == 48
    assert data["success"] is True
    assert data["response_keys"] == ["output", "usage"]
    assert data["http_status"] == 200
    # The output preview is no longer inlined on the response record — it is
    # emitted in the companion ``bedrock_output`` event (asserted below), keeping
    # the response record free of model output text.
    assert "output_preview" not in data
    # New structured attribution fields
    assert data["call_site"] == "llm_router.completion"
    assert data["api_method"] == "invoke_model"
    assert "backend" not in data

    outputs = _entries_with_event(log_file, "bedrock_output")
    assert len(outputs) == 1
    assert outputs[0]["data"]["preview"] == "all clear"


def test_call_site_omitted_and_defaults_applied(reload_logger):
    mod, log_file = reload_logger("true")
    mod.log_bedrock_request(
        request_id="reqdefaults1",
        model="m",
        region="r",
        payload_bytes=1,
        prompt_len=1,
        truncated_len=1,
    )
    mod.log_bedrock_response(
        request_id="respdefaults1",
        model="m",
        region="r",
        elapsed_s=0.5,
        tokens_in=1,
        tokens_out=1,
        success=False,
    )

    req = _entries_with_event(log_file, "bedrock_request")[0]["data"]
    assert "call_site" not in req
    assert req["api_method"] == "invoke_model"
    assert "backend" not in req

    resp_entry = _entries_with_event(log_file, "bedrock_response")[0]
    assert resp_entry["level"] == "ERROR"  # success=False logs at ERROR
    resp = resp_entry["data"]
    assert "call_site" not in resp
    assert resp["api_method"] == "invoke_model"
    assert "backend" not in resp


def test_log_json_disabled_uses_readable_formatter(reload_logger):
    mod, log_file = reload_logger("false")
    assert mod.LOG_JSON is False
    mod.log_bedrock_request(
        request_id="readable1",
        model="m",
        region="r",
        payload_bytes=1,
        prompt_len=1,
        truncated_len=1,
    )
    lines = [ln for ln in log_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines, "expected readable log output in BEDROCK_LOG_DIR"
    for line in lines:
        with pytest.raises(json.JSONDecodeError):
            json.loads(line)
        assert "BEDROCK REQUEST" in line
