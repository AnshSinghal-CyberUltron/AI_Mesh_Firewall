"""E14: adversarial proof that a sensitive value cannot be slipped PAST the
STREAMING output guard by splitting it across chunk boundaries.

RECON CLAIM UNDER ATTACK
------------------------
``secure_streaming.SecureStreamingResponse`` reassembles every buffered delta
into ``full_text`` (``_flush_buffer`` line ~222) and, on a *non-final* flush,
holds back a trailing ``STREAM_LOOKAHEAD_BYTES`` (512) window
(``_release_with_lookahead_tail``) so a PII/secret value SPLIT across a flush
boundary is re-scanned WITH its completion before any part of it is released.

This test drives the REAL ``SecureStreamingResponse.__aiter__`` end to end with
chunk-split sensitive values and asserts the COMPLETE value never reaches the
client unmasked. We exercise both the OutputGuard path (a stub guard that
delegates the actual PII verdict to the gateway's own ``InputScanner`` — i.e. a
realistic "redact PII / clean otherwise" guard) AND the no-OutputGuard fallback
scanner path (the real ``InputScanner.scan_output`` + ``redact_pii``).

Attack vectors covered
----------------------
(a) value SPLIT across a single chunk boundary
(b) value split across MORE than two chunks
(c) value LONGER than the 512-byte lookahead window (the lookahead's own
    invariant: it must exceed the longest token)
(d) split right at the FINAL flush / [DONE]
(e) tiny 1-character chunks
(f) the no-OutputGuard fallback-scanner path

Any client-visible chunk that contains the complete raw secret (or a long enough
verbatim substring of a > lookahead value to be a real leak) is a FAILURE.

litellm is stubbed before import (the scanner/secure_streaming modules do not
import litellm, but the package __init__ / siblings might), mirroring the E13
suite's stubbing discipline.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest


# --- stub litellm before importing gateway modules (matches E13 discipline) ---
if "litellm" not in sys.modules:
    fake_litellm = types.ModuleType("litellm")

    class _FakeRouter:  # pragma: no cover - trivial stub
        def __init__(self, *args, **kwargs):
            self.model_list = kwargs.get("model_list", [])

    async def _unused_async_completion(*args, **kwargs):  # pragma: no cover
        raise RuntimeError("litellm completion should be stubbed in tests")

    fake_litellm.Router = _FakeRouter
    fake_litellm.acompletion = _unused_async_completion
    fake_litellm.aembedding = _unused_async_completion
    fake_litellm.drop_params = True
    fake_litellm.request_timeout = 120
    fake_litellm.num_retries = 2
    fake_litellm.ssl_verify = True
    fake_exceptions = types.ModuleType("litellm.exceptions")
    for exc_name in (
        "APIConnectionError", "APIError", "AuthenticationError", "BadRequestError",
        "BudgetExceededError", "ContentPolicyViolationError",
        "ContextWindowExceededError", "NotFoundError", "RateLimitError",
        "ServiceUnavailableError", "Timeout",
    ):
        setattr(fake_exceptions, exc_name, type(exc_name, (Exception,), {}))
    sys.modules["litellm"] = fake_litellm
    sys.modules["litellm.exceptions"] = fake_exceptions

try:  # tolerate both import roots used across this suite
    from secure_streaming import SecureStreamingResponse, STREAM_LOOKAHEAD_BYTES
    from scanner import InputScanner
    from patterns import detect_pii, redact_all
except ImportError:  # pragma: no cover
    from ai_mesh_gateway.secure_streaming import (
        SecureStreamingResponse, STREAM_LOOKAHEAD_BYTES,
    )
    from ai_mesh_gateway.scanner import InputScanner
    from ai_mesh_gateway.patterns import detect_pii, redact_all


# --------------------------------------------------------------------------- #
# Sensitive payloads (each is detectable whole; the question is whether SPLIT
# delivery lets the COMPLETE verbatim value reach the client).
# --------------------------------------------------------------------------- #
SSN = "123-45-6789"
EMAIL = "alex.morgan@corp-secrets.example.com"
CREDIT_CARD = "4111-1111-1111-1111"
# An OpenAI-style key whose body is far longer than the 512-byte lookahead.
LONG_API_KEY = "sk-" + "Az9Kp" * 140  # ~703 chars, > STREAM_LOOKAHEAD_BYTES


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
def _sse(text: str) -> str:
    """Build one OpenAI-style streaming SSE frame carrying ``text`` as the delta."""
    payload = {
        "id": "chatcmpl-x",
        "object": "chat.completion.chunk",
        "model": "gpt-test",
        "choices": [{"index": 0, "delta": {"content": text}}],
    }
    return f"data: {json.dumps(payload)}\n\n"


async def _inner_from_pieces(pieces: list[str]):
    """Yield one SSE content frame per piece, then [DONE]."""
    for p in pieces:
        yield _sse(p)
    yield "data: [DONE]\n\n"


class _ScannerBackedGuard:
    """A realistic OutputGuard stand-in.

    The gateway's production output guard, when it finds PII/secret content,
    returns ``action='redact'`` (and the secure stream then calls
    ``scanner.redact_pii(full_text)``); otherwise it returns a clean ``allow``.
    We delegate the verdict to the SAME deterministic detector the gateway uses
    (``detect_pii``) so this double faithfully models "redact PII, pass clean".

    This is the path that SHOULD reassemble the split value before egress.
    """

    def __init__(self):
        self.calls: list[str] = []

    async def inspect(self, text, *, context_chunks=None, org_config=None, org_slug=""):
        self.calls.append(text)
        found = detect_pii(text)
        return _Verdict(found)


class _Verdict:
    def __init__(self, found: dict):
        self._found = found
        if found:
            self.action = "redact"
            self.threat_type = "pii"
            self.matched_patterns = list(found.keys())
        else:
            self.action = "allow"
            self.threat_type = "none"
            self.matched_patterns = []
        self.detail = ""
        self.compliance_tags = []
        self.matched_values = dict(found)
        self.scan_degraded = False


def _make_stream(pieces, *, use_guard, buffer_max_bytes=4096):
    scanner = InputScanner(config={})
    guard = _ScannerBackedGuard() if use_guard else None
    return SecureStreamingResponse(
        _inner_from_pieces(pieces),
        scanner,
        redaction_enabled=True,
        buffer_max_bytes=buffer_max_bytes,
        output_guard=guard,
    )


async def _drain(stream) -> str:
    """Collect the FULL client-visible text actually delivered downstream.

    We reconstruct exactly what a stock OpenAI SDK client would reassemble: the
    concatenation of every delivered ``delta.content`` across all yielded frames.
    A leak = the complete raw secret appearing in that reassembled output.
    """
    delivered_content: list[str] = []
    raw_frames: list[str] = []
    async for sse in stream.__aiter__():
        raw_frames.append(sse)
        s = sse.strip()
        if not s.startswith("data: "):
            continue
        payload = s[6:]
        if payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and data.get("error"):
            continue
        for ch in data.get("choices") or []:
            delta = (ch or {}).get("delta") or {}
            c = delta.get("content")
            if isinstance(c, str):
                delivered_content.append(c)
    return "".join(delivered_content)


def _run(pieces, *, use_guard, buffer_max_bytes=4096) -> str:
    return asyncio.run(_drain(_make_stream(
        pieces, use_guard=use_guard, buffer_max_bytes=buffer_max_bytes,
    )))


def _assert_not_leaked(delivered: str, secret: str, ctx: str):
    assert secret not in delivered, (
        f"[{ctx}] COMPLETE raw secret slipped past the streaming output guard.\n"
        f"  secret = {secret!r}\n"
        f"  delivered (client-visible) = {delivered!r}"
    )


# --------------------------------------------------------------------------- #
# (a) value SPLIT across a single chunk boundary — both paths
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("use_guard", [True, False], ids=["guard", "fallback"])
@pytest.mark.parametrize(
    "secret",
    [SSN, EMAIL, CREDIT_CARD],
    ids=["ssn", "email", "cc"],
)
def test_split_two_chunks_no_leak(secret, use_guard):
    half = len(secret) // 2
    # A trailing sentence boundary on the SECOND piece forces a non-final flush
    # AFTER the value is complete, so the lookahead path is exercised.
    pieces = [
        f"Here is the value {secret[:half]}",
        f"{secret[half:]}. Done.",
    ]
    delivered = _run(pieces, use_guard=use_guard)
    _assert_not_leaked(delivered, secret, f"split-2 use_guard={use_guard}")


# --------------------------------------------------------------------------- #
# (b) value split across MORE than two chunks
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("use_guard", [True, False], ids=["guard", "fallback"])
def test_split_many_chunks_no_leak(use_guard):
    secret = CREDIT_CARD
    # Break into 5 fragments. A boundary char ('.') only at the very end.
    fragments = ["4111", "-1111", "-1111", "-1111"]
    pieces = ["card: "] + fragments + [" thanks."]
    delivered = _run(pieces, use_guard=use_guard)
    _assert_not_leaked(delivered, secret, f"split-many use_guard={use_guard}")


# --------------------------------------------------------------------------- #
# (e) tiny 1-character chunks (token-by-token streaming)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("use_guard", [True, False], ids=["guard", "fallback"])
def test_one_char_chunks_no_leak(use_guard):
    secret = SSN
    pieces = list(f"value={secret}.")  # one char per chunk, boundary at the end
    delivered = _run(pieces, use_guard=use_guard)
    _assert_not_leaked(delivered, secret, f"1-char use_guard={use_guard}")


# --------------------------------------------------------------------------- #
# (d) split right at the FINAL flush / [DONE]
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("use_guard", [True, False], ids=["guard", "fallback"])
def test_split_at_final_done_no_leak(use_guard):
    secret = EMAIL
    half = len(secret) // 2
    # NO sentence boundary anywhere -> nothing flushes until [DONE]. The whole
    # value is reassembled only at the DONE flush.
    pieces = [f"contact {secret[:half]}", secret[half:]]
    delivered = _run(pieces, use_guard=use_guard)
    _assert_not_leaked(delivered, secret, f"final-done use_guard={use_guard}")


def test_split_straddling_boundary_flush_then_done():
    """The dangerous case: a boundary char triggers a NON-final flush mid-value,
    so the value's prefix is released-with-lookahead, then the suffix and the
    final [DONE] arrive later. The retained tail + new content must re-detect."""
    secret = CREDIT_CARD  # 19 chars
    pieces = [
        # boundary '.' fires a non-final flush while the card is INCOMPLETE
        "Your reference number is 42. The card is 4111-1111",
        "-1111-1111 on file.",
    ]
    for use_guard in (True, False):
        delivered = _run(pieces, use_guard=use_guard)
        _assert_not_leaked(delivered, secret, f"straddle use_guard={use_guard}")


# --------------------------------------------------------------------------- #
# (c) value LONGER than the 512-byte lookahead window.
#
# This is the lookahead's OWN stated invariant: STREAM_LOOKAHEAD_BYTES "must
# exceed the longest single PII/secret token". An OpenAI key body is unbounded
# (regex sk-[A-Za-z0-9]{32,}), so a > lookahead key split so that its PREFIX is
# released on an early boundary flush and only its SUFFIX is retained will, on
# the next scan, present only the suffix to the detector. If the prefix already
# left the buffer, the COMPLETE key cannot be re-detected as one span — and the
# released prefix is a verbatim-key leak.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("use_guard", [True, False], ids=["guard", "fallback"])
def test_value_longer_than_lookahead_no_leak(use_guard):
    secret = LONG_API_KEY
    assert len(secret.encode("utf-8")) > STREAM_LOOKAHEAD_BYTES, (
        "test setup: key must exceed the lookahead window to exercise (c)"
    )
    # Stream the key in fragments, with sentence boundaries SPRINKLED IN so the
    # non-final lookahead flush fires repeatedly while the key is mid-assembly.
    # Pattern: prose with a '.' boundary, then key fragments with no boundary.
    frag = 64
    body = secret
    pieces = ["The api key follows."]  # boundary -> flush prose first
    i = 0
    while i < len(body):
        pieces.append(body[i:i + frag])
        i += frag
    pieces.append(" end of key.")  # trailing boundary
    delivered = _run(pieces, use_guard=use_guard)

    # A real leak: the complete verbatim key, OR a long verbatim run of it. We
    # check both. The >lookahead verbatim-prefix run is the actual exposure even
    # if the *exact* full string is broken by a redaction marker.
    _assert_not_leaked(delivered, secret, f"longkey use_guard={use_guard}")
    # Any verbatim contiguous run of the key body >= the lookahead window is a
    # leak of key material that the lookahead invariant promised to prevent.
    body_only = secret[3:]  # drop the 'sk-' prefix; check the random body
    longest = _longest_common_run(delivered, body_only)
    assert longest < STREAM_LOOKAHEAD_BYTES, (
        f"[longkey use_guard={use_guard}] leaked a verbatim key run of "
        f"{longest} bytes (>= lookahead {STREAM_LOOKAHEAD_BYTES}); the "
        f"value-longer-than-lookahead invariant is broken.\n"
        f"  delivered = {delivered!r}"
    )


# --------------------------------------------------------------------------- #
# (c) — CONFIRMED GAP: value LONGER than the lookahead, split so an EARLY flush
# redacts the secret's PREFIX while a LATER flush carries only the trailing body.
#
# An OpenAI-style key body is unbounded (sk-[A-Za-z0-9]{32,}). If the model
# emits a sentence-boundary char (e.g. a newline) AFTER the first ~600 chars of a
# 703-char key but BEFORE the key finishes, the first flush sees a complete-enough
# prefix ("sk-<597 chars>"), detects it as a secret, REDACTS the whole buffer, and
# clears it (the redact path does NOT use the lookahead retention). The NEXT flush
# then carries ONLY the trailing ~103 key chars — which, stripped of their "sk-"
# prefix, match NO secret/PII pattern — so they are released VERBATIM to the
# client. Net: ~103 contiguous bytes of live key material egress unmasked.
#
# This is exactly the recon-claimed vector (c): STREAM_LOOKAHEAD_BYTES is a fixed
# 512-byte window but secret tokens are unbounded, so the "lookahead must exceed
# the longest token" invariant is violated for long keys. Confirmed on BOTH the
# OutputGuard path and the no-guard fallback path.
#
# E14 FIX LANDED: the secret-in-progress anchor (secure_streaming._secret_anchor
# + _consume_secret_continuation) now masks the un-anchored continuation of a
# >lookahead secret across the redact boundary. This was a strict-xfail FAILING
# repro of the open gap; it has been PROMOTED to a hard regression guard now that
# the fix closes the leak (see fix_hint: carry a 'secret-in-progress' anchor
# across flushes / mask trailing alnum runs that abut a just-redacted secret).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("use_guard", [True, False], ids=["guard", "fallback"])
def test_long_key_prefix_redacted_trailing_fragment_leaks(use_guard):
    secret = "sk-" + "Az9Kp" * 140  # 703 chars, > STREAM_LOOKAHEAD_BYTES
    assert len(secret.encode("utf-8")) > STREAM_LOOKAHEAD_BYTES
    # Newline boundary forced in AFTER 600 chars but BEFORE the key completes.
    pieces = [secret[:600], "\n", secret[600:], " done.\n"]
    delivered = _run(pieces, use_guard=use_guard)
    trailing = secret[600:]  # the un-anchored remainder
    assert trailing not in delivered, (
        f"[longkey-prefix-redact use_guard={use_guard}] {len(trailing)} bytes of "
        f"verbatim key material leaked past the streaming guard.\n"
        f"  leaked fragment = {trailing!r}\n"
        f"  delivered       = {delivered!r}"
    )


def _longest_common_run(haystack: str, needle: str) -> int:
    """Length of the longest substring of ``needle`` that appears verbatim in
    ``haystack``. Cheap because ``needle`` is a single highly-repetitive key."""
    best = 0
    n = len(needle)
    # Slide windows of decreasing size; short-circuit once we find one.
    for size in range(n, 0, -1):
        if size <= best:
            break
        for start in range(0, n - size + 1):
            if needle[start:start + size] in haystack:
                best = max(best, size)
                break
        if best >= size:
            break
    return best


# --------------------------------------------------------------------------- #
# (f) the no-OutputGuard fallback-scanner path, isolated & explicit.
#
# When output_guard is None the stream relies entirely on
# InputScanner.scan_output -> detect_pii/detect_secrets. Prove a split PII value
# is redacted (not leaked) on this path with a mid-value boundary flush.
# --------------------------------------------------------------------------- #
def test_fallback_path_split_pii_redacted():
    secret = SSN
    pieces = [
        "Account note. ssn is 123-45",  # boundary '.' before value completes
        "-6789 noted.",
    ]
    delivered = _run(pieces, use_guard=False)
    _assert_not_leaked(delivered, secret, "fallback-split")
    # And the masked form should be what reached the client.
    assert "6789" in delivered or "REDACT" in delivered.upper() or "***" in delivered, (
        f"fallback path neither redacted nor masked the value: {delivered!r}"
    )


# --------------------------------------------------------------------------- #
# Sanity: a fully clean stream is delivered verbatim (no false positive / no
# truncation) on BOTH paths, so the assertions above can't pass trivially by
# the stream simply dropping all content.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("use_guard", [True, False], ids=["guard", "fallback"])
def test_clean_stream_delivered_intact(use_guard):
    pieces = ["Hello there. ", "This is a perfectly clean ", "response with no secrets."]
    expected = "".join(pieces)
    delivered = _run(pieces, use_guard=use_guard)
    assert delivered == expected, (
        f"clean stream was altered/truncated (use_guard={use_guard}): "
        f"{delivered!r} != {expected!r}"
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
