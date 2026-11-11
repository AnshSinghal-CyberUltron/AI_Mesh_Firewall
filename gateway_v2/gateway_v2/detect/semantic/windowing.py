"""Tokenizer-aware input windowing for semantic detection (GW10) — pure.

This module is the LOCAL half of the GW10 windowing algebra (Requirements 1, 4).
Long inputs are split into tokenizer-aware, OVERLAPPING windows sized by the
*measured* characters-per-token for the input (3.04 markdown, 6.28 prose) rather
than the 4.0 rule-of-thumb, so no part of a long input is silently cut and a match
straddling a window boundary appears whole in at least one window.

It is DISTINCT from the shipped byte-windowing ``gateway_v2.detect.windowing`` —
that module bounds the trailing-byte rescan of the GW12b OUTPUT holdback scanner;
this module windows the WHOLE semantic INPUT. They are not interchangeable and this
module does not import it.

Design decisions grounded in the GW10 design doc (§"Tokenizer-aware windowing"):

* **Chars-per-token model (measured, not 4.0).** The band is sized in characters as
  ``band_chars = token_band * chars_per_token`` using the measured in-corpus values,
  carried here as MODULE CONSTANTS (never dataclass-field literals, so
  ``lint/check_capacity_literals.py`` stays green). The token band, overlap, and the
  maximum window count come from configuration / the ``ResourceContract`` / module
  constants — never a dataclass field (R1.6, R4.4, R8.4).
* **Overlap.** Adjacent windows share a declared ``overlap`` of tokens
  (``overlap_chars = overlap * chars_per_token``) so any contiguous match up to one
  band long is wholly contained in at least one window (R1.2).
* **No 512 truncation.** The shipped tokenizer truncates/pads at 512 tokens; GW10
  DISABLES that and windows the whole input — nothing is silently cut (R1.3).
* **Span offset map.** Every window carries the ``(start, end)`` CHAR offsets of its
  slice in the original text, mapping a window-level result back to a
  ``domain.finding.Span`` in the original input.
* **Pinned tokenizer hash.** A ``Tokenizer_Hash`` identifies the tokenizer version
  (the window count varies with it) and is recorded on every ``WindowPlan`` (R1.4).
* **Tokenizer seam.** Where the production tokenizer bytes are not loaded, the
  algebra consumes a :class:`TokenizerSeam` (the chars-per-token model handle); the
  windowing algebra is identical either way (R1.5, R11.4).
* **Over-length.** ``W = ceil`` over the band with overlap. If ``W > max_window_count``
  the function raises the local :class:`OverLengthError` carrying ``required_windows``
  and ``max_windows`` — an actionable length reject, NEVER a head-and-tail middle-drop,
  and NEVER an HTTP type (R4.2, R4.3). Within the max, EVERY window is windowed (R4.1).

Everything here is a PURE function of ``(text, token_band, overlap, chars_per_token)``:
no clock, no I/O, no mutation. The returned :class:`WindowPlan` is frozen+slotted.

Layering: imports only the standard library and ``gateway_v2.domain`` (the ``Span``
value type). It never imports ``edge`` / ``admit`` / ``plan`` or the byte-windowing
sibling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

from gateway_v2.domain import Span

__all__ = (
    "CHARS_PER_TOKEN_MARKDOWN",
    "CHARS_PER_TOKEN_PROSE",
    "DEFAULT_MAX_WINDOW_COUNT",
    "DEFAULT_OVERLAP_TOKENS",
    "DEFAULT_TOKEN_BAND",
    "OverLengthError",
    "TokenizerSeam",
    "WindowPlan",
    "window_text",
)

# --------------------------------------------------------------------------- #
# Measured chars-per-token model — MODULE CONSTANTS (never dataclass fields).
#
# These are the in-corpus measured characters-per-token ratios the card records
# (§0.2 / PG2), used to size a token band in CHARACTERS. They are NOT the 4.0
# rule-of-thumb. A 1,024-token band is ~3,158 chars of markdown (1024 * 3.04),
# matching the card's worked figure. Carried as module constants so the band
# derivation holds no dataclass-field literal.
# --------------------------------------------------------------------------- #

#: Measured characters-per-token for markdown traffic.
CHARS_PER_TOKEN_MARKDOWN: Final = 3.04
#: Measured characters-per-token for prose traffic.
CHARS_PER_TOKEN_PROSE: Final = 6.28

# --------------------------------------------------------------------------- #
# Band / overlap / max-window defaults — MODULE CONSTANTS.
#
# The token band, the declared token overlap, and the maximum window count are
# read from configuration or the ResourceContract by the caller; these module
# constants are the fallback values used when the caller passes nothing. None of
# them is a dataclass-field literal, so `check_capacity_literals.py` stays green.
# --------------------------------------------------------------------------- #

#: Default token band — the shipped tokenizer's 512-token window is the natural
#: unit, but the 512-token truncation/padding is DISABLED (the whole input is
#: windowed); the band here is the per-window token width.
DEFAULT_TOKEN_BAND: Final = 512
#: Default declared token overlap between adjacent windows (R1.2).
DEFAULT_OVERLAP_TOKENS: Final = 64
#: Default maximum window count before an over-length reject (R4.2). The caller
#: reads the authoritative bound from the ResourceContract / configuration.
DEFAULT_MAX_WINDOW_COUNT: Final = 64


class OverLengthError(Exception):
    """An input requires more windows than the declared maximum (R4.2, R4.3).

    An ACTIONABLE length reject — never a head-and-tail truncation that drops the
    middle of an input, and never an HTTP type. It carries the number of windows the
    input would require and the declared maximum so the caller can surface an
    actionable error. It is a plain :class:`Exception` subclass local to the detect
    layer; it never escapes as an HTTP response (the resolver maps it).
    """

    def __init__(self, required_windows: int, max_windows: int) -> None:
        self.required_windows = required_windows
        self.max_windows = max_windows
        super().__init__(
            f"input requires {required_windows} windows, "
            f"exceeds the declared maximum of {max_windows}",
        )


@dataclass(frozen=True, slots=True)
class TokenizerSeam:
    """The declared tokenizer boundary (R1.5, R11.4).

    The production tokenizer BYTES are a deferred seam; the local half carries only
    the pinned ``tokenizer_hash`` identifying the tokenizer version and the
    ``chars_per_token`` model handle used to size a token band in characters. The
    windowing algebra is identical whether a real tokenizer or this chars-per-token
    model is used, so substituting the seam yields an identical window count for the
    same ``(text, token_band, overlap)``.
    """

    tokenizer_hash: str
    chars_per_token: float


@dataclass(frozen=True, slots=True)
class WindowPlan:
    """The pure output of :func:`window_text`.

    ``windows`` — the window text slices, in order, covering the WHOLE input.
    ``spans`` — the ``(start, end)`` CHAR offsets of each window in the original text,
        parallel to ``windows``; a window-level result maps back via ``spans[i]``.
    ``window_count`` — ``W``, recorded on every request (R2.4).
    ``tokenizer_hash`` — the pinned tokenizer hash, recorded on every request (R1.4).
    """

    windows: tuple[str, ...]
    spans: tuple[Span, ...]
    window_count: int
    tokenizer_hash: str


def _band_chars(token_band: int, chars_per_token: float) -> int:
    """Size the per-window band in characters: ``ceil(token_band * chars_per_token)``.

    At least one character, so a degenerate band can never produce a zero-width
    window or divide-by-zero in the stride.
    """

    return max(1, math.ceil(token_band * chars_per_token))


def _overlap_chars(overlap: int, chars_per_token: float, band_chars: int) -> int:
    """Convert the declared token overlap to characters, bounded below the band.

    The overlap is ``floor(overlap * chars_per_token)`` clamped into
    ``[0, band_chars - 1]`` so the stride (``band_chars - overlap_chars``) is always
    at least one character and the windowing always makes forward progress.
    """

    raw = math.floor(max(0, overlap) * chars_per_token)
    return max(0, min(raw, band_chars - 1))


def _required_windows(text_len: int, band_chars: int, stride: int) -> int:
    """Number of windows needed to cover ``text_len`` chars with the given stride.

    Empty or within-one-band input needs exactly one window; otherwise it is
    ``1 + ceil((text_len - band_chars) / stride)`` — one anchoring window plus a
    window per stride for the remaining characters.
    """

    if text_len <= band_chars:
        return 1
    return 1 + math.ceil((text_len - band_chars) / stride)


def window_text(
    text: str,
    *,
    token_band: int = DEFAULT_TOKEN_BAND,
    overlap: int = DEFAULT_OVERLAP_TOKENS,
    chars_per_token: float = CHARS_PER_TOKEN_MARKDOWN,
    tokenizer_hash: str,
    max_window_count: int = DEFAULT_MAX_WINDOW_COUNT,
) -> WindowPlan:
    """Window ``text`` into tokenizer-aware overlapping windows. Pure.

    A pure function of ``(text, token_band, overlap, chars_per_token)`` plus the
    pinned ``tokenizer_hash`` and the declared ``max_window_count``. The band is sized
    in characters as ``token_band * chars_per_token`` (the measured model, not 4.0);
    adjacent windows overlap by the declared token ``overlap`` converted to characters
    so a match straddling a boundary appears whole in at least one window; the WHOLE
    input is windowed with the 512-token truncation/padding DISABLED.

    Every window carries its ``(start, end)`` char offsets so a window-level result
    maps back to a :class:`~gateway_v2.domain.finding.Span` in the original text; the
    union of the window spans covers every character with no gap.

    If the required window count exceeds ``max_window_count`` the function raises
    :class:`OverLengthError` — an actionable length reject, never a middle-drop.

    The token band, overlap, chars-per-token, and max window count are passed in by
    the caller (from configuration / the ``ResourceContract`` / the module-constant
    defaults); none is a dataclass-field literal.
    """

    band_chars = _band_chars(token_band, chars_per_token)
    overlap_chars = _overlap_chars(overlap, chars_per_token, band_chars)
    stride = band_chars - overlap_chars

    text_len = len(text)
    required = _required_windows(text_len, band_chars, stride)
    if required > max_window_count:
        raise OverLengthError(required, max_window_count)

    windows: list[str] = []
    spans: list[Span] = []
    start = 0
    while True:
        end = min(start + band_chars, text_len)
        windows.append(text[start:end])
        spans.append(Span(start, end))
        if end >= text_len:
            break
        start += stride

    return WindowPlan(
        windows=tuple(windows),
        spans=tuple(spans),
        window_count=len(windows),
        tokenizer_hash=tokenizer_hash,
    )
