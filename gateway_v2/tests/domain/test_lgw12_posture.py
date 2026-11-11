"""GW12 SSE egress stream posture codes: uniqueness + the single-spelling guarantee.

These are the value-codes `egress`/`dispatch` return and `edge` renders (R3.3, codes-vs-render
boundary). The whole point of the one shared vocabulary is that no two spellings collide, so the
join at the render boundary never silently finds nothing (the C37 failure). These tests pin that:
every GW12 code is a unique non-empty string, no two module-level codes share a spelling, and each
`STREAM_*` value is its own name in snake_case.
"""

from __future__ import annotations

from gateway_v2.domain import posture

# The nine GW12 stream codes added in task 2.1. `stream_killed`/`scan_failure`/`output_blocked`
# are REUSED from `egress/stream.py` (not redefined in this module) so they are not listed here.
_GW12_STREAM_CODE_NAMES = (
    "STREAM_INTER_CHUNK_TIMEOUT",
    "STREAM_IDLE_TIMEOUT",
    "STREAM_WRITE_TIMEOUT",
    "STREAM_MAX_DURATION",
    "STREAM_KEY_REVOKED",
    "STREAM_PLAN_CHANGED",
    "STREAM_SNAPSHOT_STALE",
    "STREAM_MALFORMED_UPSTREAM",
    "STREAM_BUFFER_UNAVAILABLE",
)


def _module_str_constants() -> dict[str, str]:
    """Every module-level `str` constant declared in `domain.posture` (UPPER_CASE names).

    Collected by introspection rather than a hand-maintained list so a future code addition is
    automatically covered by the single-spelling assertion below. `MIN_RETRY_AFTER_S` is a float
    and is excluded by the `isinstance(value, str)` filter.
    """
    return {
        name: value
        for name, value in vars(posture).items()
        if name.isupper() and isinstance(value, str)
    }


def test_every_gw12_stream_code_is_defined() -> None:
    """Task 2.1 added exactly these nine `STREAM_*` codes to `domain.posture`."""
    for name in _GW12_STREAM_CODE_NAMES:
        assert hasattr(posture, name), f"missing GW12 posture code: {name}"


def test_each_gw12_stream_code_is_a_non_empty_string() -> None:
    """R3.3: each code is a plain, non-empty `str` (no status/message/ErrorSpec attached)."""
    for name in _GW12_STREAM_CODE_NAMES:
        value = getattr(posture, name)
        assert isinstance(value, str), f"{name} is not a str: {type(value)!r}"
        assert value, f"{name} is an empty string"


def test_gw12_stream_codes_are_mutually_unique() -> None:
    """The nine new codes are distinct from one another."""
    values = [getattr(posture, name) for name in _GW12_STREAM_CODE_NAMES]
    assert len(set(values)) == len(values), f"duplicate GW12 code spelling: {values}"


def test_no_duplicate_spelling_across_the_module() -> None:
    """Single-spelling guarantee: no two module-level `str` codes share a value.

    This is the C37 defence -- two codes agreeing in meaning but differing (or colliding) in
    spelling break the render-boundary join. Introspecting ALL module-level `str` constants means
    this holds for the GW12 codes AND every pre-existing code (`shared_state_unavailable`, etc.)
    together, and keeps holding as codes are added.
    """
    constants = _module_str_constants()
    values = list(constants.values())
    assert len(set(values)) == len(values), (
        f"duplicate code spelling across domain.posture: {sorted(constants.items())}"
    )


def test_gw12_codes_participate_in_the_module_spelling_set() -> None:
    """Guards the introspection: every GW12 code is actually seen by the module-wide check."""
    collected = _module_str_constants()
    for name in _GW12_STREAM_CODE_NAMES:
        assert name in collected, f"{name} not collected by _module_str_constants()"


def test_gw12_code_values_match_their_names_in_snake_case() -> None:
    """Each `STREAM_*` value is its own name lower-cased (e.g. STREAM_IDLE_TIMEOUT == the string).

    Pins the spelling to the symbol so a code cannot drift from its name -- the thing that makes
    the shared vocabulary legible on both sides of the boundary.
    """
    for name in _GW12_STREAM_CODE_NAMES:
        value = getattr(posture, name)
        assert value == name.lower(), f"{name} value {value!r} != {name.lower()!r}"


def test_inter_chunk_timeout_spelling_is_pinned() -> None:
    """Explicit anchor for the example called out in the task (snake_case of the symbol name)."""
    assert posture.STREAM_INTER_CHUNK_TIMEOUT == "stream_inter_chunk_timeout"
