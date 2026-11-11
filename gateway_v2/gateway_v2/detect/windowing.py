"""Window-bounding helpers for the bounded-holdback scanner (R2-06 / GW12b).

Pure module: it imports nothing outside the standard library and
``gateway_v2.domain`` (and in fact needs neither), so the ``detect`` layer's
import contract (``detect`` imports only ``domain``) holds trivially.

This module is the single definition of ``Window`` (Requirement 4): the
longest bounded output-pattern length. ``window_slice`` bounds how many
trailing bytes the holdback scanner inspects per chunk, so per-chunk rescan
work is proportional to the window and independent of the total stream length.
"""

from __future__ import annotations

__all__ = (
    "MAX_PATTERN_BYTES_FLOOR",
    "MAX_PATTERN_BYTES_CEIL",
    "max_pattern_length",
    "window_slice",
)

# Requirement 4.1: every output pattern length is bounded to [1, 65536] bytes.
MAX_PATTERN_BYTES_FLOOR = 1
MAX_PATTERN_BYTES_CEIL = 65536

# Derivation of the longest bounded output-pattern length, from the reference
# scanner's bounded maxima (rvproto/detect/holdback.py):
#
#   * Payment card number: at most 19 digits with up to 18 single-byte group
#     separators between them -> 19 + 18 = 37 bytes.
#   * Longest PEM header, "-----BEGIN ENCRYPTED PRIVATE KEY-----": 37 bytes.
#   * Generic-secret keyword window: the scanner rescans a 64-byte tail looking
#     for an "api_key"/"secret"-style keyword that could still precede a value,
#     so the bounded span is 64 bytes.
#
# The Window is the maximum of these bounded maxima, i.e. 64 bytes. It is a
# compile-time constant, not a tunable.
_LONGEST_BOUNDED_PATTERN_BYTES = max(
    37,  # card: 19 digits + 18 separators
    37,  # "-----BEGIN ENCRYPTED PRIVATE KEY-----"
    64,  # generic-secret keyword window
)


def max_pattern_length() -> int:
    """Return the Window: the longest bounded output-pattern length (bytes).

    A constant clamped into ``[MAX_PATTERN_BYTES_FLOOR, MAX_PATTERN_BYTES_CEIL]``
    (Requirement 4.1). With the current pattern set this is 64.
    """
    window = max(
        MAX_PATTERN_BYTES_FLOOR,
        min(_LONGEST_BOUNDED_PATTERN_BYTES, MAX_PATTERN_BYTES_CEIL),
    )
    assert MAX_PATTERN_BYTES_FLOOR <= window <= MAX_PATTERN_BYTES_CEIL
    return window


def window_slice(buf: str, window: int) -> tuple[str, int]:
    """Return at most ``window`` trailing bytes of ``buf`` and their offset.

    The result is ``(buf[max(0, len(buf) - window):], max(0, len(buf) - window))``
    so the scanner inspects at most ``window`` trailing bytes (Requirement 4.2).
    When fewer than ``window`` bytes exist, all available bytes are returned at
    offset 0 and the caller never blocks waiting for more (Requirement 4.3). An
    empty buffer yields ``("", 0)``. The offset lets the scanner translate a
    local hold index back to a buffer-absolute index.
    """
    offset = max(0, len(buf) - window)
    return buf[offset:], offset
