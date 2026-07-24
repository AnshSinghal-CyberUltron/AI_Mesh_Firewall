"""
M-09 — tests for safe env parsing in config.py (_env_int / _env_float),
load_config() clamping of representative knobs, routing-weight
clamp+normalization, and the swept import-time casts in scanner.py /
bedrock_scanner.py.

Design note: config.py does NOT cache values at module import — load_config()
and the helpers read os.environ at call time — so monkeypatch.setenv plus a
plain call is sufficient (no importlib.reload needed). The one exception is
scanner.DEFAULT_THREAD_POOL_SIZE, a module-level constant, which is tested
with a guarded importlib.reload that always restores the module with a clean
environment.
"""
from __future__ import annotations

import importlib
import logging
from unittest.mock import MagicMock

import pytest

from ai_mesh_gateway.config import _env_float, _env_int, load_config

# Every env var touched by these tests; cleared before each test so a value
# leaking from the developer's shell can't change expected defaults.
_ENV_VARS = [
    "TEST_ENV_INT",
    "TEST_ENV_FLOAT",
    "GATEWAY_PORT",
    "GATEWAY_STATS_INTERVAL_SEC",
    "GATEWAY_STREAM_MAX_BUFFER_BYTES",
    "GATEWAY_STREAM_MAX_BUFFER_CHUNKS",
    "GATEWAY_TIER2_STREAM_HOLD_TIMEOUT_MS",
    "GATEWAY_STREAM_FINALIZE_TIMEOUT_MS",
    "LITELLM_REQUEST_TIMEOUT",
    "LITELLM_NUM_RETRIES",
    "GATEWAY_TELEMETRY_FLUSH_INTERVAL",
    "GATEWAY_TELEMETRY_BUFFER_SIZE",
    "GATEWAY_ROUTING_RISK_WEIGHT",
    "GATEWAY_ROUTING_COST_WEIGHT",
    "GATEWAY_ROUTING_LATENCY_WEIGHT",
    "GATEWAY_ROUTING_PRIORITY_WEIGHT",
    "GATEWAY_SCANNER_THREAD_POOL_SIZE",
    "GATEWAY_BEDROCK_THREAD_POOL_SIZE",
    "GATEWAY_TIER2_CACHE_TTL_SECONDS",
    "GATEWAY_TIER2_CACHE_MAX",
    "GATEWAY_TIER2_SAMPLE_RATE",
    "ENABLE_TIER2",
    "BEDROCK_MAX_TOKENS",
    "GATEWAY_MCP_BLOCK_ON_CREDENTIAL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _warnings(caplog):
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


# ---------- _env_int ------------------------------------------------------

def test_env_int_unset_returns_default():
    assert _env_int("TEST_ENV_INT", 42) == 42


def test_env_int_empty_string_returns_default(monkeypatch):
    monkeypatch.setenv("TEST_ENV_INT", "")
    assert _env_int("TEST_ENV_INT", 42) == 42


def test_env_int_whitespace_only_returns_default(monkeypatch):
    monkeypatch.setenv("TEST_ENV_INT", "   ")
    assert _env_int("TEST_ENV_INT", 42) == 42


def test_env_int_valid_value(monkeypatch):
    monkeypatch.setenv("TEST_ENV_INT", "17")
    assert _env_int("TEST_ENV_INT", 42) == 17


def test_env_int_valid_value_with_surrounding_whitespace(monkeypatch):
    monkeypatch.setenv("TEST_ENV_INT", "  17 ")
    assert _env_int("TEST_ENV_INT", 42) == 17


def test_env_int_negative_value_parses(monkeypatch):
    monkeypatch.setenv("TEST_ENV_INT", "-3")
    assert _env_int("TEST_ENV_INT", 42) == -3


@pytest.mark.parametrize("bad", ["8300x", "abc", "12.5", "1_0_x", "0x10"])
def test_env_int_bad_value_falls_back_with_warning(monkeypatch, caplog, bad):
    monkeypatch.setenv("TEST_ENV_INT", bad)
    with caplog.at_level(logging.WARNING):
        assert _env_int("TEST_ENV_INT", 42) == 42
    msgs = [r.getMessage() for r in _warnings(caplog)]
    assert any("Invalid int" in m and "TEST_ENV_INT" in m for m in msgs)


def test_env_int_clamps_below_min_with_warning(monkeypatch, caplog):
    monkeypatch.setenv("TEST_ENV_INT", "0")
    with caplog.at_level(logging.WARNING):
        assert _env_int("TEST_ENV_INT", 8, min_value=1, max_value=256) == 1
    assert any("below min" in r.getMessage() for r in _warnings(caplog))


def test_env_int_clamps_above_max_with_warning(monkeypatch, caplog):
    monkeypatch.setenv("TEST_ENV_INT", "99999")
    with caplog.at_level(logging.WARNING):
        assert _env_int("TEST_ENV_INT", 8, min_value=1, max_value=256) == 256
    assert any("above max" in r.getMessage() for r in _warnings(caplog))


@pytest.mark.parametrize("boundary", [1, 256])
def test_env_int_boundary_values_pass_unclamped_no_warning(
        monkeypatch, caplog, boundary):
    monkeypatch.setenv("TEST_ENV_INT", str(boundary))
    with caplog.at_level(logging.WARNING):
        assert _env_int("TEST_ENV_INT", 8, min_value=1, max_value=256) == boundary
    assert _warnings(caplog) == []


def test_env_int_default_itself_is_clamped():
    # A default outside the bounds is still clamped (defensive).
    assert _env_int("TEST_ENV_INT", 0, min_value=1) == 1


# ---------- _env_float ----------------------------------------------------

def test_env_float_unset_returns_default():
    assert _env_float("TEST_ENV_FLOAT", 2.0) == 2.0


@pytest.mark.parametrize("raw,expected", [
    ("0.5", 0.5),
    ("3", 3.0),       # int strings parse as float
    ("1e-1", 0.1),    # scientific notation
    (" 2.5 ", 2.5),   # surrounding whitespace
])
def test_env_float_valid_values(monkeypatch, raw, expected):
    monkeypatch.setenv("TEST_ENV_FLOAT", raw)
    assert _env_float("TEST_ENV_FLOAT", 9.9) == pytest.approx(expected)


@pytest.mark.parametrize("bad", ["fast", "2.0s", "1,5", ""])
def test_env_float_bad_or_empty_value_falls_back(monkeypatch, caplog, bad):
    monkeypatch.setenv("TEST_ENV_FLOAT", bad)
    with caplog.at_level(logging.WARNING):
        assert _env_float("TEST_ENV_FLOAT", 2.0) == 2.0
    if bad.strip():  # empty string falls back silently (treated as unset)
        msgs = [r.getMessage() for r in _warnings(caplog)]
        assert any("Invalid float" in m and "TEST_ENV_FLOAT" in m for m in msgs)


def test_env_float_clamps_below_min(monkeypatch, caplog):
    monkeypatch.setenv("TEST_ENV_FLOAT", "-0.5")
    with caplog.at_level(logging.WARNING):
        assert _env_float("TEST_ENV_FLOAT", 0.5, min_value=0.0, max_value=1.0) == 0.0
    assert any("below min" in r.getMessage() for r in _warnings(caplog))


def test_env_float_clamps_above_max(monkeypatch, caplog):
    monkeypatch.setenv("TEST_ENV_FLOAT", "100.0")
    with caplog.at_level(logging.WARNING):
        assert _env_float("TEST_ENV_FLOAT", 0.5, min_value=0.0, max_value=1.0) == 1.0
    assert any("above max" in r.getMessage() for r in _warnings(caplog))


@pytest.mark.parametrize("boundary", ["0.0", "1.0"])
def test_env_float_boundary_values_pass_unclamped_no_warning(
        monkeypatch, caplog, boundary):
    monkeypatch.setenv("TEST_ENV_FLOAT", boundary)
    with caplog.at_level(logging.WARNING):
        value = _env_float("TEST_ENV_FLOAT", 0.5, min_value=0.0, max_value=1.0)
    assert value == float(boundary)
    assert _warnings(caplog) == []


# ---------- load_config(): representative knobs ----------------------------

@pytest.mark.parametrize("raw,expected", [
    ("8443", 8443),       # valid passes through
    ("8300x", 8300),      # typo -> default
    ("70000", 65535),     # above max -> clamp
    ("0", 1),             # below min -> clamp
    ("65535", 65535),     # boundary unchanged
])
def test_load_config_port(monkeypatch, raw, expected):
    monkeypatch.setenv("GATEWAY_PORT", raw)
    assert load_config()["port"] == expected


@pytest.mark.parametrize("raw,expected", [
    ("65536", 65536),
    ("not-a-size", 4096),         # bad -> default
    ("1", 64),                    # below min -> clamp
    ("999999999999", 10_485_760), # multi-GB typo -> clamp
])
def test_load_config_stream_max_buffer_bytes(monkeypatch, raw, expected):
    monkeypatch.setenv("GATEWAY_STREAM_MAX_BUFFER_BYTES", raw)
    assert load_config()["stream_max_buffer_bytes"] == expected


@pytest.mark.parametrize("raw,expected", [
    ("0", 1),
    ("100000", 10000),
    ("128", 128),
])
def test_load_config_stream_max_buffer_chunks(monkeypatch, raw, expected):
    monkeypatch.setenv("GATEWAY_STREAM_MAX_BUFFER_CHUNKS", raw)
    assert load_config()["stream_max_buffer_chunks"] == expected


@pytest.mark.parametrize("raw,expected", [
    ("30", 30),
    ("2 minutes", 120),  # bad -> default
    ("0", 1),            # below min -> clamp
    ("999999", 3600),    # above max -> clamp
])
def test_load_config_litellm_request_timeout(monkeypatch, raw, expected):
    monkeypatch.setenv("LITELLM_REQUEST_TIMEOUT", raw)
    assert load_config()["litellm_request_timeout"] == expected


@pytest.mark.parametrize("raw,expected", [
    ("-5", 0),         # below min -> clamp
    ("120000", 60000), # above max -> clamp
    ("nope", 1200),    # bad -> default
])
def test_load_config_tier2_stream_hold_timeout_ms(monkeypatch, raw, expected):
    monkeypatch.setenv("GATEWAY_TIER2_STREAM_HOLD_TIMEOUT_MS", raw)
    assert load_config()["tier2_stream_hold_timeout_ms"] == expected


@pytest.mark.parametrize("raw,expected", [
    ("5.5", 5.5),
    ("often", 2.0),     # bad -> default
    ("0.01", 0.1),      # below min -> clamp
    ("100000", 3600.0), # above max -> clamp
])
def test_load_config_telemetry_flush_interval(monkeypatch, raw, expected):
    monkeypatch.setenv("GATEWAY_TELEMETRY_FLUSH_INTERVAL", raw)
    assert load_config()["telemetry_flush_interval"] == pytest.approx(expected)


def test_load_config_telemetry_buffer_size_bad_and_clamped(monkeypatch):
    monkeypatch.setenv("GATEWAY_TELEMETRY_BUFFER_SIZE", "lots")
    assert load_config()["telemetry_buffer_size"] == 100
    monkeypatch.setenv("GATEWAY_TELEMETRY_BUFFER_SIZE", "0")
    assert load_config()["telemetry_buffer_size"] == 1


def test_load_config_defaults_with_clean_env():
    cfg = load_config()
    assert cfg["port"] == 8300
    assert cfg["stream_max_buffer_bytes"] == 4096
    assert cfg["stream_max_buffer_chunks"] == 64
    assert cfg["litellm_request_timeout"] == 120
    assert cfg["telemetry_flush_interval"] == 2.0
    assert cfg["telemetry_buffer_size"] == 100


# ---------- load_config(): routing weights ---------------------------------

_W_KEYS = ("routing_risk_weight", "routing_cost_weight",
           "routing_latency_weight", "routing_priority_weight")
_W_ENV = ("GATEWAY_ROUTING_RISK_WEIGHT", "GATEWAY_ROUTING_COST_WEIGHT",
          "GATEWAY_ROUTING_LATENCY_WEIGHT", "GATEWAY_ROUTING_PRIORITY_WEIGHT")
_W_DEFAULTS = (0.30, 0.20, 0.20, 0.30)


def _weights(cfg):
    return tuple(cfg[k] for k in _W_KEYS)


def test_routing_weights_defaults_sum_to_one():
    weights = _weights(load_config())
    assert weights == pytest.approx(_W_DEFAULTS)
    assert sum(weights) == pytest.approx(1.0)


def test_routing_weights_equal_overrides_normalized(monkeypatch):
    for env in _W_ENV:
        monkeypatch.setenv(env, "1.0")
    weights = _weights(load_config())
    assert weights == pytest.approx((0.25, 0.25, 0.25, 0.25))
    assert sum(weights) == pytest.approx(1.0)


def test_routing_weight_above_one_clamped_then_normalized(monkeypatch):
    # risk=5.0 clamps to 1.0; others keep defaults -> sum 1.7 -> normalized.
    monkeypatch.setenv("GATEWAY_ROUTING_RISK_WEIGHT", "5.0")
    weights = _weights(load_config())
    assert weights[0] == pytest.approx(1.0 / 1.7)
    assert weights[1] == pytest.approx(0.20 / 1.7)
    assert sum(weights) == pytest.approx(1.0)


def test_routing_weight_negative_clamped_to_zero_then_normalized(monkeypatch):
    # risk=-1 clamps to 0.0; remaining 0.2+0.2+0.3=0.7 -> normalized.
    monkeypatch.setenv("GATEWAY_ROUTING_RISK_WEIGHT", "-1")
    weights = _weights(load_config())
    assert weights[0] == 0.0
    assert weights[3] == pytest.approx(0.30 / 0.7)
    assert sum(weights) == pytest.approx(1.0)


def test_routing_weights_all_zero_restores_defaults(monkeypatch, caplog):
    for env in _W_ENV:
        monkeypatch.setenv(env, "0")
    with caplog.at_level(logging.WARNING):
        weights = _weights(load_config())
    assert weights == pytest.approx(_W_DEFAULTS)
    assert any("restoring defaults" in r.getMessage() for r in _warnings(caplog))


def test_routing_weights_within_tolerance_not_normalized(monkeypatch):
    # Sum 0.995 is within the 0.01 tolerance: values kept as given.
    for env, raw in zip(_W_ENV, ("0.295", "0.20", "0.20", "0.30")):
        monkeypatch.setenv(env, raw)
    weights = _weights(load_config())
    assert weights == pytest.approx((0.295, 0.20, 0.20, 0.30))


def test_routing_weight_bad_value_falls_back_to_its_default(monkeypatch, caplog):
    monkeypatch.setenv("GATEWAY_ROUTING_RISK_WEIGHT", "very high")
    with caplog.at_level(logging.WARNING):
        weights = _weights(load_config())
    assert weights == pytest.approx(_W_DEFAULTS)  # sum 1.0 -> no normalization
    assert any("Invalid float" in r.getMessage() for r in _warnings(caplog))


# ---------- swept casts: scanner.py ----------------------------------------

def test_scanner_module_pool_size_survives_bad_env(monkeypatch):
    """Import-time constant must fall back / clamp instead of crashing."""
    from ai_mesh_gateway import scanner as scanner_module
    try:
        monkeypatch.setenv("GATEWAY_SCANNER_THREAD_POOL_SIZE", "eight")
        importlib.reload(scanner_module)  # would raise ValueError pre-M-09
        assert scanner_module.DEFAULT_THREAD_POOL_SIZE == 8

        monkeypatch.setenv("GATEWAY_SCANNER_THREAD_POOL_SIZE", "0")
        importlib.reload(scanner_module)
        assert scanner_module.DEFAULT_THREAD_POOL_SIZE == 1  # clamped

        monkeypatch.setenv("GATEWAY_SCANNER_THREAD_POOL_SIZE", "4")
        importlib.reload(scanner_module)
        assert scanner_module.DEFAULT_THREAD_POOL_SIZE == 4
    finally:
        # Always leave the module re-imported under a clean environment.
        monkeypatch.delenv("GATEWAY_SCANNER_THREAD_POOL_SIZE", raising=False)
        importlib.reload(scanner_module)
    assert scanner_module.DEFAULT_THREAD_POOL_SIZE == 8


def test_input_scanner_init_survives_bad_tier2_env(monkeypatch):
    from ai_mesh_gateway.scanner import InputScanner

    monkeypatch.setenv("ENABLE_TIER2", "false")  # skip BedrockScanner setup
    monkeypatch.setenv("GATEWAY_BEDROCK_THREAD_POOL_SIZE", "lots")
    monkeypatch.setenv("GATEWAY_TIER2_CACHE_TTL_SECONDS", "soon")
    monkeypatch.setenv("GATEWAY_TIER2_CACHE_MAX", "many")
    monkeypatch.setenv("GATEWAY_TIER2_SAMPLE_RATE", "half")
    scanner = InputScanner(thread_pool_size=1)  # crashed at startup pre-M-09
    try:
        assert scanner._tier2_cache_ttl == 300.0
        assert scanner._tier2_cache_max == 10000
        assert scanner._tier2_sample_rate == 1.0
    finally:
        scanner._executor.shutdown(wait=False)
        scanner._bedrock_executor.shutdown(wait=False)


@pytest.mark.parametrize("raw,expected", [
    ("2.5", 1.0),   # clamp high
    ("-0.5", 0.0),  # clamp low
    ("0.25", 0.25),
])
def test_input_scanner_sample_rate_clamped(monkeypatch, raw, expected):
    from ai_mesh_gateway.scanner import InputScanner

    monkeypatch.setenv("ENABLE_TIER2", "false")
    monkeypatch.setenv("GATEWAY_TIER2_SAMPLE_RATE", raw)
    scanner = InputScanner(thread_pool_size=1)
    try:
        assert scanner._tier2_sample_rate == expected
    finally:
        scanner._executor.shutdown(wait=False)
        scanner._bedrock_executor.shutdown(wait=False)


# ---------- swept casts: bedrock_scanner.py ---------------------------------

def _scan_payload_with_max_tokens_env(monkeypatch, raw):
    from ai_mesh_gateway.bedrock_scanner import BedrockScanner

    if raw is not None:
        monkeypatch.setenv("BEDROCK_MAX_TOKENS", raw)
    client = MagicMock()
    client.region = "us-test-1"
    client.scan_prompt.side_effect = RuntimeError("halt after payload build")
    scanner = BedrockScanner(client=client, model="openai.gpt-oss-test")
    result = scanner.scan("hello world")
    # Bad env must not raise; the client error path degrades gracefully.
    assert result["llm_guard"]["degraded"] is True
    return client.scan_prompt.call_args.kwargs["prompt_payload"]


def test_bedrock_scan_bad_max_tokens_falls_back(monkeypatch):
    payload = _scan_payload_with_max_tokens_env(monkeypatch, "lots")
    assert payload["max_tokens"] == 256


def test_bedrock_scan_valid_max_tokens_passes_through(monkeypatch):
    payload = _scan_payload_with_max_tokens_env(monkeypatch, "512")
    assert payload["max_tokens"] == 512


def test_bedrock_scan_max_tokens_clamped(monkeypatch):
    assert _scan_payload_with_max_tokens_env(monkeypatch, "0")["max_tokens"] == 1
    assert _scan_payload_with_max_tokens_env(
        monkeypatch, "9999999")["max_tokens"] == 65536


# ---------- mcp_block_on_credential: DEFAULT OFF (strict operator control) ---
#
# Regression for the config-vs-helper drift behind the live "issue_write blocked
# with 'matched compliance tags: SECRET' but no operator policy" symptom. Commit
# 005a6ffa (2026-07-22) made the credential force-block OPT-IN so a "redact"
# posture masks-and-forwards instead of escalating to a hard BLOCK ("redact means
# redact"). It updated _mcp_block_on_credential_enabled()'s docstring + env
# fallback but left THIS config.py default at "true" — and that helper reads the
# CONFIG value FIRST, so the escalation stayed live in every real deployment. The
# existing scan tests never caught it because they patch the helper / setenv
# directly and never build CONFIG from config.py. This pins the CONFIG default.

def test_mcp_block_on_credential_defaults_off():
    # Unset env (the autouse _clean_env fixture removed it) -> load_config MUST
    # produce False, so a "redact" posture masks the credential and forwards
    # rather than the floor escalating redact -> block.
    assert load_config()["mcp_block_on_credential"] is False


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("1", True), ("yes", True),   # opt-in still available
    ("false", False), ("0", False), ("", False),  # explicit / bad -> off
])
def test_mcp_block_on_credential_opt_in(monkeypatch, raw, expected):
    monkeypatch.setenv("GATEWAY_MCP_BLOCK_ON_CREDENTIAL", raw)
    assert load_config()["mcp_block_on_credential"] is expected
