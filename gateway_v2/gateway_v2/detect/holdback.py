"""Pure pattern-aware minimal holdback scanner (R2-06 / GW12b).

This module is the pure core of the bounded-holdback feature. Given the pending
unreleased buffer of a text stream it computes the earliest byte index whose
suffix could still grow into an output-pattern match (``hold_start``), classifies
what is being held (``classify_hold``), and -- bounded by the owner-signed hold
cap -- decides how much of that suffix must still be held versus force-released
(``scan``). Everything here is a pure function of its inputs: no clock, no I/O,
no transport, no provider. That is what makes the correctness properties
directly property-testable in isolation.

Layering: this module imports only from ``gateway_v2.domain`` and the sibling
``gateway_v2.detect.windowing``. It never imports ``egress`` or ``runtime``.

Reference. The ``hold_start`` family is adapted-in-spirit from the evidence
snapshot ``rvproto/detect/holdback.py``. Its ``PEM_HEADERS`` are copied here
(self-contained) rather than imported; the material additions over the
reference are ``TokenIndex`` (the upstream-token counting model), the per-pattern
trade-off table, ``ScanResult``, ``classify_hold``, and the token-aware
force-release / relaxed-ceiling logic in ``scan`` that turn the reference's
"hold the minimal suffix forever" into Requirement 1's bounded hold.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from gateway_v2.detect.windowing import window_slice
from gateway_v2.domain import RELAXED_HOLDBACK_CLASSES

__all__ = (
    "HOLD_CLASS_RELAXED",
    "HOLD_CLASS_WORD",
    "PEM_HEADERS",
    "TRADE_OFF",
    "ScanResult",
    "TokenIndex",
    "TradeOffOutcome",
    "classify_hold",
    "hold_start",
    "scan",
)

# --------------------------------------------------------------------------- #
# Character classes (match the reference word/numeric sets exactly).
# --------------------------------------------------------------------------- #

_WORD: Final = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._%+@/=-"
)
_NUM: Final = frozenset("0123456789 ().+-")
_NUM_START: Final = frozenset("0123456789(+")

# A payment card is at most 19 digits with up to 18 single-byte separators.
_CARD_MAX_CHARS: Final = 19 + 18  # 37

# --------------------------------------------------------------------------- #
# PEM headers -- copied self-contained (do NOT import rvproto). Both the plain
# and the " BLOCK" variants exist, matching the reference pattern catalogue.
# --------------------------------------------------------------------------- #

PEM_HEADERS: Final[tuple[str, ...]] = tuple(
    f"-----BEGIN {kind}PRIVATE KEY{blk}-----"
    for kind in ("", "RSA ", "EC ", "DSA ", "OPENSSH ", "ENCRYPTED ", "PGP ")
    for blk in ("", " BLOCK")
)
_PEM_MAX: Final = max(len(h) for h in PEM_HEADERS)

# Generic-secret keyword tail: a keyword that could still precede a value run,
# bounded to a 64-char trailing window.
_GENERIC_TAIL: Final = re.compile(
    r"(?i)(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret"
    r"|password|passwd)[\"']?[ \t]{0,4}(?:[:=][ \t]{0,4}[\"']?[A-Za-z0-9_/+=.-]*)?$"
)
_GENERIC_TAIL_WINDOW: Final = 64

# --------------------------------------------------------------------------- #
# Hold-class category constants (R2-06 design "per-pattern trade-off table").
# --------------------------------------------------------------------------- #

#: A sensitive class NOT in ``RELAXED_HOLDBACK_CLASSES`` -- subject to the cap.
HOLD_CLASS_WORD: Final = "word"
#: A class in ``RELAXED_HOLDBACK_CLASSES`` -- may exceed the cap to the ceiling.
HOLD_CLASS_RELAXED: Final = "relaxed"


class TradeOffOutcome(StrEnum):
    """The single declared outcome for an over-cap sensitive pattern (R6.1)."""

    REDACT_REMAINDER = "redact_remainder"
    TERMINATE = "terminate"


# The frozen per-pattern trade-off table (design "Per-pattern trade-off table",
# R6.1). Exactly one outcome per class; this mapping is the single source
# consulted by both the scanner (classification) and the resolver (outcome). It
# is an immutable MappingProxyType so no module-level mutable state exists.
TRADE_OFF: Final[Mapping[str, TradeOffOutcome]] = MappingProxyType(
    {
        # Word-class, cap-bounded.
        "aws": TradeOffOutcome.REDACT_REMAINDER,
        "api_token": TradeOffOutcome.REDACT_REMAINDER,
        "email": TradeOffOutcome.REDACT_REMAINDER,
        "jwt": TradeOffOutcome.TERMINATE,
        # Numeric-class, cap-bounded.
        "card": TradeOffOutcome.TERMINATE,
        # Relaxed, may exceed cap to the ceiling.
        "uuid": TradeOffOutcome.REDACT_REMAINDER,
        "sha256": TradeOffOutcome.REDACT_REMAINDER,
        "url": TradeOffOutcome.REDACT_REMAINDER,
        "base64": TradeOffOutcome.REDACT_REMAINDER,
    }
)


# --------------------------------------------------------------------------- #
# Upstream-token counting model (design "Token counting model").
# --------------------------------------------------------------------------- #


class TokenIndex:
    """Counts Upstream_Tokens in a buffer slice. Pure, deterministic, O(len)."""

    @staticmethod
    def tokens_in(s: str) -> int:
        """Number of maximal ``_WORD`` runs in ``s`` (a UUID is 1 run; "a b c" is 3)."""
        count = 0
        in_run = False
        for ch in s:
            if ch in _WORD:
                if not in_run:
                    count += 1
                    in_run = True
            else:
                in_run = False
        return count

    @staticmethod
    def token_boundaries(s: str) -> list[int]:
        """Start index of each maximal ``_WORD`` run, oldest first.

        The force-release drops the OLDEST held tokens by advancing the hold
        index to a later boundary, so the returned list is in increasing order.
        """
        starts: list[int] = []
        in_run = False
        for i, ch in enumerate(s):
            if ch in _WORD:
                if not in_run:
                    starts.append(i)
                    in_run = True
            else:
                in_run = False
        return starts


# --------------------------------------------------------------------------- #
# hold_start -- earliest index whose suffix could still grow into a match.
# --------------------------------------------------------------------------- #


def _word_run(buf: str, n: int) -> int:
    i = n
    while i > 0 and buf[i - 1] in _WORD:
        i -= 1
    if i == n:
        return n
    run = buf[i:n]
    ats = [k for k, ch in enumerate(run) if ch == "@"]
    if len(ats) >= 2:  # only the part after the second-to-last '@' can still match
        i += ats[-2] + 1
    return i


def _numeric_run(buf: str, n: int) -> int:
    j = n
    lo = max(0, n - _CARD_MAX_CHARS)
    while j > lo and buf[j - 1] in _NUM:
        j -= 1
    for k in range(j, n):
        if buf[k] in _NUM_START:
            return k
    return n


def _pem_prefix(buf: str, n: int) -> int:
    lo = max(0, n - _PEM_MAX)
    k = buf.find("-", lo)
    while k != -1 and k < n:
        tail = buf[k:n]
        for h in PEM_HEADERS:
            if len(tail) < len(h) and h.startswith(tail):
                return k
        k = buf.find("-", k + 1)
    return n


def hold_start(buf: str) -> int:
    """Earliest index ``i`` such that ``buf[i:]`` could still grow into a match.

    Everything before ``i`` cannot be part of a still-incomplete match and may
    be released once scanned. Returns ``len(buf)`` when nothing could still
    match. Pure and deterministic.
    """
    n = len(buf)
    if n == 0:
        return 0
    w = _word_run(buf, n)
    h = min(w, _numeric_run(buf, n))
    if "-" in buf[max(0, n - _PEM_MAX) :]:
        h = min(h, _pem_prefix(buf, n))
    # A keyword can only precede the trailing value run by a few separator chars.
    lo = max(0, w - _GENERIC_TAIL_WINDOW)
    m = _GENERIC_TAIL.search(buf, lo)
    if m is not None:
        h = min(h, m.start())
    return h


# --------------------------------------------------------------------------- #
# classify_hold -- which pattern class the held suffix belongs to.
# --------------------------------------------------------------------------- #

# Deterministic recognisers for the held suffix. Each returns the class name
# when the suffix is unambiguously that class; order matters (most specific
# first). These classify what is being HELD so the trade-off/relaxed-ceiling
# logic can be applied -- they are intentionally conservative, not a full
# detector (that is GW07/GW08).
_UUID_RE: Final = re.compile(
    r"\A[0-9a-fA-F]{0,8}(?:-[0-9a-fA-F]{0,4}){0,3}(?:-[0-9a-fA-F]{0,12})?\Z"
)
_SHA256_RE: Final = re.compile(r"\A[0-9a-fA-F]{1,64}\Z")
_URL_RE: Final = re.compile(r"\A(?:h|ht|htt|http|https|https?://)")
_BASE64_RE: Final = re.compile(r"\A[A-Za-z0-9+/]{8,}={0,2}\Z")
_EMAIL_RE: Final = re.compile(r"@")
_AWS_RE: Final = re.compile(r"\A(?:AKIA|ASIA)")
_JWT_RE: Final = re.compile(r"\Aey")
_PEM_LEAD_RE: Final = re.compile(r"\A-+BEGIN")


def _classify_word_suffix(suffix: str) -> str | None:
    """Best-effort class name for a word-class held suffix, or ``None``."""
    if not suffix:
        return None
    if _AWS_RE.match(suffix):
        return "aws"
    # A JWT begins with the base64 of '{"', i.e. "eyJ"; a 1-2 char lead "ey"/"eyJ"
    # is still a possible JWT head.
    if _JWT_RE.match(suffix) and "eyJ".startswith(suffix[:3]):
        return "jwt"
    if _EMAIL_RE.search(suffix):
        return "email"
    # A UUID-shaped (hex-with-dashes) suffix is a relaxed class.
    if "-" in suffix and _UUID_RE.match(suffix):
        return "uuid"
    _url_heads = {"h", "ht", "htt", "http", "https"}
    if _URL_RE.match(suffix) and ("http" in suffix or suffix in _url_heads):
        return "url"
    # A long hex run is a sha256; a shorter one is ambiguous but hex -> sha256.
    if _SHA256_RE.match(suffix):
        return "sha256"
    if _BASE64_RE.match(suffix):
        return "base64"
    return None


def classify_hold(buf: str, hold_index: int) -> tuple[str | None, bool]:
    """Classify the held suffix ``buf[hold_index:]``.

    Returns ``(held_class, is_relaxed)`` where ``held_class`` is one of the
    pattern class names in :data:`TRADE_OFF` (e.g. ``"uuid"``, ``"aws"``,
    ``"email"``, ``"jwt"``, ``"card"``, ``"url"``, ``"sha256"``, ``"base64"``,
    ``"api_token"``) or ``None`` when nothing is held, and ``is_relaxed`` is
    ``held_class in RELAXED_HOLDBACK_CLASSES``. Deterministic and pure.
    """
    if hold_index >= len(buf):
        return None, False
    suffix = buf[hold_index:]

    # PEM header prefix (a key material header) -> treat as an api_token class.
    if _PEM_LEAD_RE.match(suffix):
        return "api_token", False

    # Generic-secret keyword tail -> api_token.
    if _GENERIC_TAIL.search(buf, max(0, hold_index - _GENERIC_TAIL_WINDOW)):
        return "api_token", False

    # Numeric run (card) -> all chars are numeric-class and at least one digit.
    if suffix and all(ch in _NUM for ch in suffix) and any(ch.isdigit() for ch in suffix):
        return "card", False

    held_class = _classify_word_suffix(suffix)
    if held_class is None:
        # Something is held but we cannot name it precisely; it is a word-class
        # run (the only non-numeric/non-PEM thing hold_start returns).
        return "api_token", False
    is_relaxed = held_class in RELAXED_HOLDBACK_CLASSES
    return held_class, is_relaxed


# --------------------------------------------------------------------------- #
# ScanResult + scan -- the bounded-hold decision.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ScanResult:
    """The pure result of a holdback scan over the pending buffer.

    ``hold_index`` -- earliest held byte: ``buf[:hold_index]`` is releasable.
    ``held_class`` -- pattern class forcing the hold, or ``None`` if nothing held.
    ``is_relaxed`` -- ``held_class in RELAXED_HOLDBACK_CLASSES``.
    ``forced_release`` -- the token cap advanced ``hold_index`` past the minimal
        suffix (the oldest held tokens were force-released).
    ``forced_release_index`` -- the minimal (unbounded) hold index, kept for the
        trade-off accounting in the caller.
    ``overflow`` -- a relaxed-class hold exceeded ``2 * hold_cap_tokens``. The
        caller (egress) handles this as holdback overflow (R6.5). ``scan`` stays
        pure by signalling via this flag rather than raising.
    """

    hold_index: int
    held_class: str | None
    is_relaxed: bool
    forced_release: bool
    forced_release_index: int
    overflow: bool = False


def scan(
    buf: str,
    *,
    final: bool,
    hold_cap_tokens: int,
    token_index: TokenIndex,
    window: int,
) -> ScanResult:
    """Compute the bounded-hold decision for ``buf``. Pure function of its inputs.

    When ``final`` is set the whole buffer is releasable (nothing can still grow
    into a match once the stream is complete), so ``hold_index == len(buf)``.

    Otherwise: the minimal hold index is ``hold_start`` clamped to the trailing
    ``window`` bytes. The held suffix is classified; for a word-class hold whose
    held-token count exceeds ``hold_cap_tokens`` the oldest tokens are
    force-released (``hold_index`` advanced to the boundary that leaves exactly
    ``hold_cap_tokens`` held). A relaxed-class hold is permitted up to
    ``2 * hold_cap_tokens`` tokens; beyond that ``overflow`` is signalled.

    Requirements 1.3, 1.4, 1.5, 3.1, 4.2, 4.3, 6.1.
    """
    n = len(buf)
    if final:
        return ScanResult(
            hold_index=n,
            held_class=None,
            is_relaxed=False,
            forced_release=False,
            forced_release_index=n,
        )

    # Bound the inspected span to the trailing `window` bytes (R4.2/R4.3). The
    # scanner only inspects the slice; a local index is translated back to an
    # absolute buffer index via the offset.
    sliced, offset = window_slice(buf, window)
    local_minimal = hold_start(sliced)
    minimal = offset + local_minimal

    if minimal >= n:
        # Nothing could still match: release everything.
        return ScanResult(
            hold_index=n,
            held_class=None,
            is_relaxed=False,
            forced_release=False,
            forced_release_index=n,
        )

    held = buf[minimal:]
    held_tokens = token_index.tokens_in(held)
    held_class, is_relaxed = classify_hold(buf, minimal)

    if is_relaxed:
        ceiling = 2 * hold_cap_tokens
        if held_tokens > ceiling:
            # Relaxed-class ceiling exceeded (R6.5): signal overflow. Hold index
            # stays at the minimal suffix; the caller releases no in-progress
            # bytes and emits a holdback_overflow terminal frame.
            return ScanResult(
                hold_index=minimal,
                held_class=held_class,
                is_relaxed=True,
                forced_release=False,
                forced_release_index=minimal,
                overflow=True,
            )
        return ScanResult(
            hold_index=minimal,
            held_class=held_class,
            is_relaxed=True,
            forced_release=False,
            forced_release_index=minimal,
        )

    # Word-class (or numeric/api_token) hold: enforce the hard cap.
    if held_tokens > hold_cap_tokens:
        boundaries = token_index.token_boundaries(held)
        # Keep exactly `hold_cap_tokens` youngest tokens: advance to the boundary
        # of the (len - cap)-th token so the oldest tokens are force-released.
        drop_to = boundaries[len(boundaries) - hold_cap_tokens]
        new_hold_index = minimal + drop_to
        return ScanResult(
            hold_index=new_hold_index,
            held_class=held_class,
            is_relaxed=False,
            forced_release=True,
            forced_release_index=minimal,
        )

    return ScanResult(
        hold_index=minimal,
        held_class=held_class,
        is_relaxed=False,
        forced_release=False,
        forced_release_index=minimal,
    )
