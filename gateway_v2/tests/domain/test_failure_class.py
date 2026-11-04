"""GW35: one classification of a failure code (operational error vs policy block)."""

from __future__ import annotations

import ast
import pathlib

from gateway_v2.domain import posture
from gateway_v2.domain.failure_class import (
    INPUT_SIDE,
    NO_ACTION,
    OPERATIONAL_CODES,
    PREDISPATCH_OPERATIONAL,
    is_input_side,
    is_no_action,
    is_operational_failure,
    terminal_output_action,
)

_UPSTREAM = (
    "upstream_auth_error",
    "upstream_auth_unsupported",
    "upstream_error",
    "upstream_invalid_request",
    "upstream_not_found",
    "upstream_rate_limited",
    "upstream_request_unbuildable",
    "upstream_timeout",
    "upstream_unavailable",
)
_POLICY = (
    "output_blocked",
    "scan_failure",
    posture.POLICY_REGEX_TIMEOUT,
    posture.STREAM_KEY_REVOKED,
    posture.STREAM_PLAN_CHANGED,
    posture.STREAM_SNAPSHOT_STALE,
    "forced_release_tradeoff",
    "holdback_overflow",
    "undefined_tradeoff",
    posture.STREAM_STRICT_BUFFER_OVERFLOW,
)
_OPERATIONAL_STREAM = (
    posture.STREAM_MALFORMED_UPSTREAM,
    posture.STREAM_INTER_CHUNK_TIMEOUT,
    posture.STREAM_IDLE_TIMEOUT,
    posture.STREAM_WRITE_TIMEOUT,
    posture.STREAM_MAX_DURATION,
    posture.STREAM_BUFFER_UNAVAILABLE,
)


def _class_of(code: str) -> str:
    hits = [
        name
        for name, table in (
            ("operational", OPERATIONAL_CODES),
            ("no_action", NO_ACTION),
            ("input_side", INPUT_SIDE),
        )
        if code in table
    ]
    assert len(hits) <= 1, (code, hits)
    return hits[0] if hits else "policy"


def test_every_stream_code_is_classified_once() -> None:
    stream_codes = [
        getattr(posture, name) for name in posture.__all__ if name.startswith("STREAM_")
    ]
    for code in stream_codes:
        _class_of(code)  # raises when a code is in two tables
    assert all(is_operational_failure(code) for code in _UPSTREAM)
    assert all(is_operational_failure(code) for code in _OPERATIONAL_STREAM)
    assert not any(is_operational_failure(code) for code in _POLICY)


def test_kill_switch_spellings_publish_nothing() -> None:
    assert is_no_action("killed")
    assert is_no_action(posture.STREAM_KILLED)
    assert not is_operational_failure("killed")
    assert not is_operational_failure(posture.STREAM_KILLED)


def test_input_side_and_predispatch_tables() -> None:
    assert is_input_side("input_blocked") and is_input_side("input_unmaskable")
    assert not is_operational_failure("input_blocked")
    for code in PREDISPATCH_OPERATIONAL:
        assert is_operational_failure(code)
    assert posture.PLAN_UNAVAILABLE in PREDISPATCH_OPERATIONAL


def test_unknown_code_defaults_to_policy() -> None:
    assert not is_operational_failure("a_code_nobody_declared")
    assert terminal_output_action("a_code_nobody_declared") == "block"


def test_terminal_output_action() -> None:
    assert terminal_output_action("upstream_error") == "error"
    assert terminal_output_action(posture.STREAM_INTER_CHUNK_TIMEOUT) == "error"
    assert terminal_output_action(posture.STREAM_KEY_REVOKED) == "block"


def test_every_code_routes_kills_is_classified() -> None:
    """Every posture constant `edge/routes.py` passes to ``kill_latch.kill`` is in a table."""
    source = pathlib.Path(__file__).parents[2] / "gateway_v2" / "edge" / "routes.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {
        node.args[0].id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "kill"
        and node.args
        and isinstance(node.args[0], ast.Name)
    }
    assert names, "expected at least one kill_latch.kill(<CONST>) call"
    for name in names:
        value = getattr(posture, name, None)
        assert value is not None, name
        assert is_operational_failure(value) or is_no_action(value) or value in _POLICY, name
