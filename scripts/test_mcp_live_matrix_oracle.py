"""Unit test for the item-20 redaction oracle find_leaked_values (backstop CHG-0012).

The live-matrix harness used to count a redact-but-forward as ``allowed``. This
proves the response-byte oracle detects a raw PII value in the egress (a real
leak) and honours masking/blocking. The harness itself needs the live stack; this
oracle is pure.

Run:  gateway/.venv/bin/python scripts/test_mcp_live_matrix_oracle.py
  or  gateway/.venv/bin/python -m pytest scripts/test_mcp_live_matrix_oracle.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from mcp_live_matrix_harness import (  # noqa: E402
    authz_denied,
    authz_violation,
    find_leaked_values,
)


def test_detects_raw_pii_leak():
    body = {"result": {"content": [{"type": "text", "text": "reach user1@example.com or 123-45-6789"}]}}
    leaked = find_leaked_values(body, ["user1@example.com", "123-45-6789"])
    assert set(leaked) == {"user1@example.com", "123-45-6789"}


def test_masked_response_no_leak():
    body = {"result": {"content": [{"type": "text", "text": "reach u***@e***.com or ***-**-6789"}]}}
    assert find_leaked_values(body, ["user1@example.com", "123-45-6789"]) == []


def test_blocked_response_no_leak():
    body = {"result": {"content": [{"text": "[BLOCKED] matched compliance tags"}], "isError": True}}
    assert find_leaked_values(body, ["user1@example.com"]) == []


def test_partial_leak_detected():
    # ssn masked but email forwarded raw -> the email is reported as leaked.
    body = {"result": {"content": [{"text": "user1@example.com ***-**-6789"}]}}
    assert find_leaked_values(body, ["user1@example.com", "123-45-6789"]) == ["user1@example.com"]


def test_no_sensitive_values():
    assert find_leaked_values({"result": {"content": [{"text": "hello"}]}}, []) == []
    assert find_leaked_values({"anything": 1}, None) == []


# ── item-20 authz-under-load oracle (CHG-0028) ───────────────────────────────

_OK_RESULT = {"result": {"content": [{"type": "text", "text": "did the thing"}]}}


def test_authz_denied_shapes():
    assert authz_denied(403, {}) is True                                   # HTTP 403
    assert authz_denied(200, {"error": {"message": "tool not allowed for key"}}) is True
    assert authz_denied(200, {"error": {"message": "org_scope_violation"}}) is True
    assert authz_denied(200, {"result": {"content": [{"text": "[BLOCKED] no"}], "isError": True}}) is True
    assert authz_denied(200, _OK_RESULT) is False                          # normal success
    assert authz_denied(200, {"error": {"message": "internal boom"}}) is False  # non-authz error


def test_authz_violation_only_on_successful_forbidden_execution():
    # A forbidden tool that RAN successfully under load -> violation.
    assert authz_violation(200, _OK_RESULT, expect_denied=True) is True
    # Correctly denied in each shape -> NOT a violation.
    assert authz_violation(403, {}, expect_denied=True) is False
    assert authz_violation(200, {"error": {"message": "forbidden"}}, expect_denied=True) is False
    assert authz_violation(200, {"result": {"content": [{"text": "[BLOCKED]"}], "isError": True}},
                           expect_denied=True) is False
    # Non-execution error (didn't run the tool) -> NOT a violation.
    assert authz_violation(400, {"error": {"message": "bad json"}}, expect_denied=True) is False
    assert authz_violation(200, {"error": {"message": "upstream timeout"}}, expect_denied=True) is False


def test_authz_violation_never_flags_allowed_scenarios():
    # For a normal (non-deny) scenario we never expect denial, so a success is fine.
    assert authz_violation(200, _OK_RESULT, expect_denied=False) is False
    assert authz_violation(403, {}, expect_denied=False) is False


if __name__ == "__main__":
    _fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for _fn in _fns:
        _fn()
        print("ok", _fn.__name__)
    print(f"ALL {len(_fns)} REDACTION-ORACLE TESTS PASSED")
