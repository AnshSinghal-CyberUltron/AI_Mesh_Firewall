"""Unit tests for the mcp_pipeline_matrix_live oracle (backstop CHG-0029).

The previous oracle checked only ONE hardcoded SSN in ONLY result.content[0].text,
so a redact-but-forward in a later content item / structuredContent / nested field
/ with any non-SSN value passed as ``redacted`` (a silent leak). These pin the
full-serialized-bytes oracle over the case's ACTUAL sensitive values. The module
is now import-safe so this runs without GATEWAY_KEY / argv.

Run:  gateway/.venv/bin/python -m pytest scripts/test_mcp_pipeline_oracle.py -q
  or  gateway/.venv/bin/python scripts/test_mcp_pipeline_oracle.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from mcp_pipeline_matrix_live import (  # noqa: E402
    case_sensitive_values,
    classify,
    find_pii_in_body,
    has_redaction_marker,
    matches,
)


def test_find_pii_in_body_scans_whole_response_not_just_first_content():
    # PII hidden in the SECOND content item + structuredContent + a nested field —
    # all invisible to the old content[0]-only oracle.
    body = {
        "result": {
            "content": [
                {"type": "text", "text": "clean summary"},
                {"type": "text", "text": "reach a@b.com"},
            ],
            "structuredContent": {"record": {"ssn": "123-45-6789"}},
        }
    }
    leaked = find_pii_in_body(body, ["a@b.com", "123-45-6789", "unused"])
    assert set(leaked) == {"a@b.com", "123-45-6789"}


def test_find_pii_in_body_masked_no_leak():
    body = {"result": {"content": [{"text": "reach a***@b***.com or ***-**-6789"}]}}
    assert find_pii_in_body(body, ["a@b.com", "123-45-6789"]) == []


def test_case_sensitive_values_explicit_and_fallback():
    assert case_sensitive_values({"args": {}, "pii": ["x@y.com", "sekret"]}) == ["x@y.com", "sekret"]
    # fallback: canonical SSN present in args -> detected
    assert case_sensitive_values({"args": {"message": "my ssn 123-45-6789"}}) == ["123-45-6789"]
    # no explicit pii, no SSN in args -> nothing to check
    assert case_sensitive_values({"args": {"message": "hello"}}) == []


def test_has_redaction_marker():
    assert has_redaction_marker({"result": {"content": [{"text": "[REDACTED]"}]}}) is True
    assert has_redaction_marker({"result": {"content": [{"text": "a***@b.com"}]}}) is True
    assert has_redaction_marker({"result": {"content": [{"text": "plain"}]}}) is False


def test_classify_block_error_allow():
    assert classify({"result": {"content": [{"text": "[BLOCKED] no"}], "isError": True}})[0] == "block"
    assert classify({"result": {"content": [{"text": "boom"}], "isError": True}})[0] == "error"
    assert classify({"error": {"message": "bad"}})[0] == "error"
    assert classify({"result": {"content": [{"text": "ok"}]}})[0] == "allow"


def test_matches_pass_pii_requires_raw_value_present():
    body = {"result": {"content": [{"text": "your ssn is 123-45-6789"}]}}
    assert matches("allow", body, "pass_pii", ["123-45-6789"]) is True
    masked = {"result": {"content": [{"text": "your ssn is ***-**-6789"}]}}
    assert matches("allow", masked, "pass_pii", ["123-45-6789"]) is False


def test_matches_redacted_is_byte_truth():
    # allowed + no raw value anywhere + a redaction marker -> genuinely redacted.
    ok = {"result": {"content": [{"text": "your ssn is [REDACTED]"}]}}
    assert matches("allow", ok, "redacted", ["123-45-6789"]) is True
    # a redact-but-FORWARD (raw value still present) -> NOT redacted (a leak).
    leak = {"result": {"content": [{"text": "clean"}, {"text": "123-45-6789"}]}}
    assert matches("allow", leak, "redacted", ["123-45-6789"]) is False
    # allowed + absent but NO marker -> not counted as a successful redaction.
    noecho = {"result": {"content": [{"text": "done"}]}}
    assert matches("allow", noecho, "redacted", ["123-45-6789"]) is False


def test_matches_direct_actions():
    body = {"result": {"content": [{"text": "[BLOCKED]"}], "isError": True}}
    assert matches("block", body, "block", []) is True
    assert matches("allow", {"result": {"content": [{"text": "ok"}]}}, "allow", []) is True
    assert matches("error", {"error": {"message": "x"}}, "error", []) is True
    assert matches("allow", body, "block", []) is False


if __name__ == "__main__":
    _fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for _fn in _fns:
        _fn()
        print("ok", _fn.__name__)
    print(f"ALL {len(_fns)} PIPELINE-ORACLE TESTS PASSED")
