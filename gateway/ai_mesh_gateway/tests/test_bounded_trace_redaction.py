"""The trace redaction window must not change what an operator sees.

`_truncate` redacted the WHOLE prompt to keep 1200 characters — measured at 39.6% of
firewall CPU and unbounded in prompt length. It now redacts a bounded window. These tests
pin the SOUNDNESS ARGUMENT, not just the happy path: the window ends at whitespace because
every unbounded redaction pattern matches a whitespace-free run, and a run cannot straddle
a whitespace cut.
"""
from __future__ import annotations

import pytest

from ai_mesh_gateway import pipeline_trace as PT

LIMIT = 1200
F = "The deployment runbook lists the rollback steps for the platform team. "


def _old_truncate(text: str, limit: int = LIMIT) -> str:
    """The pre-change implementation: redact everything, then cut."""
    raw = (text or "").strip()
    if raw and PT._redact_all is not None:
        try:
            raw = PT._redact_all(raw)
        except Exception:
            pass
    return raw if len(raw) <= limit else raw[:limit] + "…"


def _filler(n: int) -> str:
    return (F * (n // len(F) + 1))[:n]


# ── the window rule itself ───────────────────────────────────────────────────

def test_window_cut_falls_on_whitespace():
    """The whole soundness argument rests on this: a whitespace-free token cannot straddle
    a cut placed at whitespace. The window is text[:j] where text[j] IS the whitespace, so
    the property is about the cut POSITION, not the window's last character."""
    text = _filler(600) + "A" * 1500 + " tail " + _filler(3000)
    w = PT._redaction_window(text, LIMIT)
    assert len(w) < len(text), "this fixture should be windowed"
    assert text[len(w)].isspace(), (
        f"cut landed mid-token: ...{text[len(w) - 8:len(w) + 8]!r}"
    )
    # and the A-run — the token that spans the naive cut — is wholly inside the window
    assert "A" * 1500 in w


def test_window_is_the_whole_text_when_short():
    for n in (0, 1, 50, LIMIT, LIMIT + PT._REDACT_WINDOW_OVERLAP):
        t = _filler(n)
        assert PT._redaction_window(t, LIMIT) == t


def test_window_does_not_grow_without_bound():
    """A prompt 30x longer must not produce a 30x longer window — that is the point."""
    small = PT._redaction_window(_filler(4096), LIMIT)
    huge = PT._redaction_window(_filler(120_000), LIMIT)
    assert len(huge) == len(small)
    assert len(huge) < LIMIT + PT._REDACT_WINDOW_OVERLAP + 200


def test_run_longer_than_hard_cap_stops_at_the_cap():
    """The documented residual risk, pinned so it cannot silently get worse."""
    text = _filler(600) + "B" * 20_000
    w = PT._redaction_window(text, LIMIT)
    assert len(w) <= LIMIT + PT._REDACT_WINDOW_OVERLAP + PT._REDACT_WINDOW_HARD_CAP


# ── equivalence: secrets planted exactly where a windowing bug would show ────

SECRETS = [
    "xoxb-1234567890-abcdefghijklmnopqrstuvwx",
    "glpat-ABCDEFGHIJKLMNOPQRSTU",
    "sk-ant-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123",
    "Bearer ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
    "password: hunter2hunter2hunter2",
    "mongodb://user:pass@host.example.com/db",
    "ravi@example.com",
    "4111111111111111",
    "123-45-6789",
    "-----BEGIN RSA PRIVATE KEY-----",
]


@pytest.mark.parametrize("secret", SECRETS)
def test_secret_straddling_the_cut_is_handled_identically(secret):
    """Slide each secret across the window boundary one character at a time. This is the
    exact position where redacting a prefix could differ from redacting everything."""
    cut = LIMIT + PT._REDACT_WINDOW_OVERLAP
    base = _filler(6000)
    for off in range(cut - len(secret) - 3, cut + 4):
        if off < 0:
            continue
        t = base[:off] + secret + base[off:6000]
        assert PT._truncate(t, LIMIT) == _old_truncate(t, LIMIT), f"offset {off}"


@pytest.mark.parametrize("text", [
    "",
    "   ",
    "A" * 10_000,
    "A" * 5_000 + " xoxb-1234567890-abcdefghij",
    "café ☕ ünïcødé " * 500,
    "\n" * 4000 + "ravi@example.com",
    F * 300,
])
def test_edge_shapes_are_identical(text):
    assert PT._truncate(text, LIMIT) == _old_truncate(text, LIMIT)


def test_pii_before_the_limit_is_still_masked():
    """The thing the trace exists to guarantee: no raw PII reaches the operator."""
    t = "ravi@example.com and card 4111111111111111 " + _filler(30_000)
    out = PT._truncate(t, LIMIT)
    assert "ravi@example.com" not in out
    assert "4111111111111111" not in out
