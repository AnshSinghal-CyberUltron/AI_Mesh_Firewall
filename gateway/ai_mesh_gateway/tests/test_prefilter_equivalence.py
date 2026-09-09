"""The multi-pattern prefilter may only ever REMOVE work — task 7.

`_redact_all_raw` runs 63 full-text `.sub()` scans per call. The prefilter reports which
patterns could match so the rest can be skipped, measured at 7.4x on the matching path.

THE FAILURE MODE THIS GUARDS: an under-report is not a slow path — it is redaction silently
not happening, on one input, while every output diff on benign text still looks perfect.
`scripts/detection/hyperscan_prefilter_gate.py` hunts that directly by comparing matching
pattern SETS; these tests pin the properties that make the design safe regardless.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "gateway"))
sys.path.insert(0, str(ROOT / "gateway" / "ai_mesh_gateway"))

import patterns as P  # noqa: E402

SENSITIVE = [
    "email alex.morgan@corp-secrets.example.com now",
    "card 4111-1111-1111-1111 and ssn 123-45-6789",
    "sk-" + "Az9Kp" * 20,
    "Bearer " + "x" * 40,
    "aws_secret_access_key = " + "A" * 40,
    "password: hunter2",
    "MRN 1234567 and NPI 1234567890",
    "call +1 (555) 123-4567",
    "mongodb://user:pw@host/db",
    "-----BEGIN PRIVATE KEY-----",
    "​sk​-live​-key",
    "".join(chr(0xE0000 + ord(c)) for c in "sk-live-ABCDEF"),
    "password: forgotten — this must NOT be flagged as a credential",
    "see https://example.com/user@host/path for details",
    "",
]


def _corpus():
    out = []
    d = ROOT / "tests" / "detection_corpus"
    for name in ("benign.jsonl", "malicious.jsonl"):
        for line in (d / name).read_text().splitlines():
            if line.strip():
                out.append(json.loads(line)["text"])
    return out


def _without_prefilter(text: str) -> str:
    """Same function with the prefilter forced off — i.e. the pre-task-7 code path."""
    saved_ready, saved_pf = P._PREFILTER_READY, P._PREFILTER
    P._PREFILTER_READY, P._PREFILTER = True, None
    try:
        return P._redact_obfuscated(text, P._redact_all_raw(text))
    finally:
        P._PREFILTER_READY, P._PREFILTER = saved_ready, saved_pf


@pytest.mark.parametrize("text", SENSITIVE)
def test_sensitive_inputs_are_byte_identical_with_and_without_prefilter(text):
    P._redact_all_cached.cache_clear()
    with_pf = P.redact_all(text)
    P._redact_all_cached.cache_clear()
    assert with_pf == _without_prefilter(text), (
        f"prefilter changed the redaction of {text[:50]!r} — it may only remove work, "
        f"never alter output")


def test_corpus_is_byte_identical_with_and_without_prefilter():
    bad = []
    for t in _corpus():
        P._redact_all_cached.cache_clear()
        a = P.redact_all(t)
        P._redact_all_cached.cache_clear()
        if a != _without_prefilter(t):
            bad.append(t)
    assert not bad, f"{len(bad)} corpus items redact differently with the prefilter on"


def test_fallback_is_the_same_code_not_a_second_implementation():
    """`_candidate_keys` returning None must disable every guard.

    This is why the no-hyperscan path needs no separate testing: it IS this code with the
    guards inert.
    """
    saved_ready, saved_pf = P._PREFILTER_READY, P._PREFILTER
    P._PREFILTER_READY, P._PREFILTER = True, None
    try:
        assert P._candidate_keys("anything at all") is None
    finally:
        P._PREFILTER_READY, P._PREFILTER = saved_ready, saved_pf


def test_the_seven_lookaround_patterns_are_never_skipped():
    """They are FP-suppressors Hyperscan cannot compile; skipping one flags benign text."""
    assert P._RE_ONLY_KEYS == {
        "email", "government_id", "password_assignment", "secret_assignment",
        "token_assignment", "api_key_assignment", "exposed_password",
    }
    for k in P._RE_ONLY_KEYS:
        assert k not in P._PREFILTER_KEYS, (
            f"{k!r} is in the prefilter set; its lookaround cannot be expressed there, so "
            f"a miss would silently drop a false-positive guard")


def test_a_broken_prefilter_falls_back_rather_than_reducing_redaction(monkeypatch):
    """Any prefilter failure must mean 'scan everything', never 'scan nothing'."""
    class _Exploding:
        def scan(self, *a, **k):
            raise RuntimeError("prefilter blew up")

    saved_ready, saved_pf = P._PREFILTER_READY, P._PREFILTER
    P._PREFILTER_READY, P._PREFILTER = True, (_Exploding(), object())
    try:
        assert P._candidate_keys("email alex@corp.example.com") is None
        P._redact_all_cached.cache_clear()
        out = P.redact_all("email alex.morgan@corp.example.com")
        assert "alex.morgan@corp.example.com" not in out, \
            "a broken prefilter must not stop redaction"
    finally:
        P._PREFILTER_READY, P._PREFILTER = saved_ready, saved_pf
