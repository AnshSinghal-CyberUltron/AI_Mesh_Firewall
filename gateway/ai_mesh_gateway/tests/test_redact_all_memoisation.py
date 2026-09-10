"""Memoising `redact_all` must not change one character — task 8b.

MEASURED: `redact_all` is 78% of the gateway's on-CPU time (py-spy, 121,169 samples), and
the allow path redacts the SAME string twice per request — `redacted_prompt` is None
whenever no redaction fired (main.py:7952), so `forwarded_prompt=_trace_text(redacted_prompt
or prompt)` re-redacts exactly what `prompt=_trace_text(prompt)` just did.

THE HAZARD (R4): a process-wide cache keyed on text is shared across tenants. That is
sound for `redact_all` only because its output depends on NOTHING but the text. It is NOT
sound for `redact_all_scoped`, whose output depends on `allowed_classes` — caching that on
text alone would hand one scope another scope's answer.
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


def _uncached(text: str) -> str:
    """The pre-8b body, verbatim."""
    return P._redact_obfuscated(text, P._redact_all_raw(text))


def _corpus() -> list[str]:
    out = []
    d = ROOT / "tests" / "detection_corpus"
    for name in ("benign.jsonl", "malicious.jsonl"):
        for line in (d / name).read_text().splitlines():
            if line.strip():
                out.append(json.loads(line)["text"])
    return out


SENSITIVE = [
    "email alex.morgan@corp-secrets.example.com now",
    "card 4111-1111-1111-1111 and ssn 123-45-6789",
    "sk-" + "Az9Kp" * 20,
    "Bearer " + "x" * 40,
    "aws_secret_access_key = " + "A" * 40,
    "​sk​-live​-key",          # zero-width obfuscation
    "".join(chr(0xE0000 + ord(c)) for c in "sk-live-ABCDEF"),  # tag-block
    "",
]


def test_corpus_is_byte_identical():
    bad = [t for t in _corpus() if P.redact_all(t) != _uncached(t)]
    assert not bad, f"{len(bad)} corpus items redact differently, e.g. {bad[:2]!r}"


@pytest.mark.parametrize("text", SENSITIVE)
def test_sensitive_inputs_are_byte_identical(text):
    assert P.redact_all(text) == _uncached(text)


def test_repeated_calls_return_the_same_result():
    t = "email alex.morgan@corp-secrets.example.com and card 4111-1111-1111-1111"
    first = P.redact_all(t)
    for _ in range(5):
        assert P.redact_all(t) == first


def test_scoped_redaction_is_not_cached_on_text_alone():
    """R4's trap: `redact_all_scoped` output depends on `allowed_classes`.

    If it shared a text-keyed cache, asking for 'pii' after asking for 'credential'
    would return the credential-scoped answer for the same text — masking a class the
    operator explicitly chose not to mutate.
    """
    t = "email alex.morgan@corp.example.com and key sk-" + "Az9Kp" * 20
    pii_only = P.redact_all_scoped(t, {"pii"})
    cred_only = P.redact_all_scoped(t, {"credential"})
    assert pii_only != cred_only, (
        "the two scopes returned identical output — if a text-keyed cache is being "
        "shared between them, one scope is answering for the other")
    # And asking again in the reverse order must not swap the answers.
    assert P.redact_all_scoped(t, {"pii"}) == pii_only
    assert P.redact_all_scoped(t, {"credential"}) == cred_only


def test_cache_is_bounded():
    fn = getattr(P.redact_all, "__wrapped__", None) or P.redact_all
    info = getattr(P, "_redact_all_cached", None)
    assert info is not None and hasattr(info, "cache_info"), \
        "expected a bounded lru_cache on the redact_all body"
    assert info.cache_info().maxsize is not None, "cache must be bounded (R3)"
