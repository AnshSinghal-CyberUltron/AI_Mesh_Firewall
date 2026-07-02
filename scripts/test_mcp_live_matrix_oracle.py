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
from mcp_live_matrix_harness import find_leaked_values  # noqa: E402


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


if __name__ == "__main__":
    _fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for _fn in _fns:
        _fn()
        print("ok", _fn.__name__)
    print(f"ALL {len(_fns)} REDACTION-ORACLE TESTS PASSED")
