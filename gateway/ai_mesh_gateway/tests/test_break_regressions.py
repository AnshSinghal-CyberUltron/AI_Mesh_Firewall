"""Regression tests for the bugs surfaced by the heavy parallel break-testing
swarm (vector-firewall + boundary-validation hardening).

These lock in the fixes so they cannot silently regress:
  * byok_embedder: OverflowError-on-convert is degraded (not a raw crash); the
    litellm count-mismatch guard fires (no IndexError leak).
  * typed_placeholder_redactor: None input fails loud (TypeError), not a silent
    None that crashes a downstream ``.split()``.
  * patterns: the email regex rejects SSH/git remotes (``git@github.com:org/repo``)
    while still matching real emails — and stays linear-time.
  * policy_sync: a malformed pub/sub notification (string/null version,
    non-numeric compiled_at) never crashes ``_is_newer_notification``.
"""
import time

import pytest

from byok_embedder import _is_degraded_vector, embed_texts, EmbeddingDegradedError
from typed_placeholder_redactor import detect_and_redact_typed
from patterns import PII_PATTERNS, compile_pattern


# ── byok_embedder ────────────────────────────────────────────────────────────
def test_is_degraded_vector_overflow_on_convert():
    # 10**309 is a Python int too large for IEEE-754; float() raises
    # OverflowError. It must be classified degraded, not propagate the error.
    assert _is_degraded_vector([0.5, 10 ** 309]) is True
    assert _is_degraded_vector([0.1, 0.2, 0.3]) is False


def test_embed_texts_overflow_raises_degraded_not_overflowerror():
    class _Resp:
        def __init__(self, vecs):
            self.data = [type("D", (), {"values": v})() for v in vecs]

    class _PC:
        class inference:
            @staticmethod
            def embed(model, inputs, parameters):
                return _Resp([[0.5, 10 ** 309]])

    with pytest.raises(EmbeddingDegradedError):
        embed_texts(["a"], embedding_model="m", pinecone_client=_PC())


def test_embed_texts_litellm_count_mismatch_raises_degraded(monkeypatch):
    # Provider returns FEWER vectors than inputs -> must raise
    # EmbeddingDegradedError (the count-mismatch guard), not a raw IndexError.
    import byok_embedder

    class _FakeLitellm:
        @staticmethod
        def embedding(**kwargs):
            n = len(kwargs["input"])
            return type("R", (), {"data": [{"embedding": [0.1, 0.2]} for _ in range(n - 1)]})()

    monkeypatch.setitem(__import__("sys").modules, "litellm", _FakeLitellm)
    with pytest.raises(EmbeddingDegradedError):
        embed_texts(["a", "b", "c"], embedding_model="text-embedding-3-small")


# ── typed_placeholder_redactor ───────────────────────────────────────────────
def test_redactor_none_raises_typeerror():
    with pytest.raises(TypeError):
        detect_and_redact_typed(None)


def test_redactor_empty_string_ok():
    r = detect_and_redact_typed("")
    assert r.text == ""
    assert not r.redacted


# ── patterns: email SSH false-positive + correctness + ReDoS ──────────────────
@pytest.mark.parametrize(
    "text,should_match",
    [
        ("john.doe+tag@sub.example.co.uk", True),
        ("a@b.com", True),
        ("contact john@x.com: hello", True),   # email then colon-space -> keep
        ("user@example.com:8080", True),       # port-ish, no path slash -> keep
        ("git@github.com:org/repo.git", False),
        ("git clone git@github.com:user/repo.git", False),
    ],
)
def test_email_regex_ssh_disambiguation(text, should_match):
    rx = compile_pattern(PII_PATTERNS["email"])
    assert bool(rx.search(text)) is should_match


def test_email_regex_linear_on_adversarial_input():
    rx = compile_pattern(PII_PATTERNS["email"])
    adv = ("a" * 200000) + "   " + ("a." * 100000)
    t = time.time()
    list(rx.finditer(adv))
    assert time.time() - t < 1.0  # linear, no catastrophic backtracking


# ── policy_sync: malformed notification never crashes ─────────────────────────
def test_policy_sync_is_newer_notification_malformed_inputs():
    from policy_sync import PolicySync

    ps = PolicySync.__new__(PolicySync)  # avoid __init__ (no redis needed)
    ps._org_versions = {"o": 5}
    ps._org_caches = {"o": {"compiled_at": 100.0}}

    # string / null version must not raise; must return a bool.
    assert ps._is_newer_notification("o", "6", None) in (True, False)
    assert ps._is_newer_notification("o", None, None) in (True, False)
    # non-numeric incoming compiled_at on the version-reset path must not raise.
    assert ps._is_newer_notification("o", 1, "2024-01-01T00:00:00Z") in (True, False)
    # genuinely newer compiled_at despite version reset -> accept.
    assert ps._is_newer_notification("o", 1, 200.0) is True
    # non-numeric CACHED compiled_at must not permanently wedge (no crash).
    ps._org_caches["o"] = {"compiled_at": "bogus-iso-string"}
    assert ps._is_newer_notification("o", 1, 50.0) in (True, False)
