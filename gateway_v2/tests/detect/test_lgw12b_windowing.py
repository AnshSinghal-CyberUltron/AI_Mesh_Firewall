"""LGW12b windowing bounds (R2-06 / GW12b). Requirements 4.1, 4.2, 4.3."""

from __future__ import annotations

from gateway_v2.detect.windowing import (
    MAX_PATTERN_BYTES_CEIL,
    MAX_PATTERN_BYTES_FLOOR,
    max_pattern_length,
    window_slice,
)


def test_max_pattern_length_within_bounds() -> None:
    # Requirement 4.1: the Window is a constant in [1, 65536].
    window = max_pattern_length()
    assert MAX_PATTERN_BYTES_FLOOR <= window <= MAX_PATTERN_BYTES_CEIL
    assert window == 64  # longest bounded pattern = generic-secret keyword window


def test_max_pattern_length_is_constant() -> None:
    assert max_pattern_length() == max_pattern_length()


def test_window_slice_bounds_trailing_bytes() -> None:
    # Requirement 4.2: inspect at most `window` trailing bytes with correct offset.
    buf = "abcdefghij"  # 10 bytes
    sliced, offset = window_slice(buf, 4)
    assert sliced == "ghij"
    assert len(sliced) <= 4
    assert offset == 6
    assert buf[offset:] == sliced


def test_window_slice_buffer_shorter_than_window() -> None:
    # Requirement 4.3: fewer than `window` bytes -> all bytes, offset 0, no block.
    buf = "abc"
    sliced, offset = window_slice(buf, 64)
    assert sliced == "abc"
    assert offset == 0


def test_window_slice_empty_buffer() -> None:
    assert window_slice("", 64) == ("", 0)


def test_window_slice_window_equals_buffer_length() -> None:
    buf = "abcd"
    sliced, offset = window_slice(buf, 4)
    assert sliced == "abcd"
    assert offset == 0


def test_window_slice_window_larger_than_buffer() -> None:
    buf = "hello"
    sliced, offset = window_slice(buf, 1000)
    assert sliced == buf
    assert offset == 0


def test_window_slice_window_one() -> None:
    buf = "hello"
    sliced, offset = window_slice(buf, 1)
    assert sliced == "o"
    assert offset == 4
