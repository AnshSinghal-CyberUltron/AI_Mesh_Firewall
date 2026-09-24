"""Sanitize-at-capture and ledger load."""

from __future__ import annotations

from gateway_v2.contracts.parity.c2 import generate_c2
from gateway_v2.contracts.parity.clock import FrozenClock
from gateway_v2.contracts.parity.ledger import load_ledger
from gateway_v2.contracts.parity.sanitize import sanitize_headers, sanitize_text


def test_sanitize_strips_pii_and_secrets() -> None:
    text = "mail jane.doe@example.com ssn 123-45-6789 key AKIAIOSFODNN7EXAMPLE"
    out = sanitize_text(text)
    assert "jane.doe" not in out
    assert "123-45-6789" not in out
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "[EMAIL]" in out
    assert "[SSN]" in out
    assert "[AWS_KEY]" in out


def test_c2_sanitizes_at_capture() -> None:
    rec = generate_c2(8, FrozenClock(epoch=1))[4]
    assert "jane.doe" not in rec.prompt
    assert "AKIAIOSFODNN7EXAMPLE" not in rec.prompt
    assert rec.headers[1][1] == "[REDACTED]"
    names = sanitize_headers((("Authorization", "Bearer abcdefghijklmnop"),))
    assert names[0][1] == "[REDACTED]"


def test_shipped_ledger_has_no_authorizing_entries() -> None:
    assert load_ledger() == ()
