"""GW30 vector-store adapter posture codes: distinctness + the single-spelling guarantee.

These are the value-codes `dispatch` returns on a frozen `AdapterResult` / `CredentialResolution`
and `edge` renders (GW31, the codes-vs-render boundary). The one shared vocabulary only works if
no two spellings collide, so the join at the render boundary never silently finds nothing (the
C37 failure). These tests pin that: every GW30 code is a unique non-empty string, the full set is
pairwise distinct, no two module-level codes share a spelling, and -- the R5.1 anchor --
`UNSUPPORTED_OPERATION` is its own spelling, distinct from `VECTOR_OK` and from every other GW30
code so a caller can tell an unsupported operation from a clean zero-record success by `code`
alone.
"""

from __future__ import annotations

from gateway_v2.domain import posture

# The GW30 codes (ten from task 1.1, plus COLLECTION_EXISTS for R2.9). Introspected from the
# module below for the module-wide single-spelling check, but named explicitly here so these
# exact codes are the ones asserted.
_GW30_VECTOR_CODE_NAMES = (
    "VECTOR_OK",
    "UNSUPPORTED_OPERATION",
    "PROVIDER_UNREACHABLE",
    "CREDENTIAL_UNAVAILABLE",
    "STORE_NOT_CONFIGURED",
    "CREDENTIAL_SCOPE_MISMATCH",
    "ORG_IDENTITY_INVALID",
    "VECTOR_CAPACITY_EXCEEDED",
    "VECTOR_CAPACITY_UNSET",
    "CREDENTIAL_LEAK_WITHHELD",
    "COLLECTION_EXISTS",
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


def test_every_gw30_vector_code_is_defined() -> None:
    """These GW30 codes are defined to `domain.posture`."""
    for name in _GW30_VECTOR_CODE_NAMES:
        assert hasattr(posture, name), f"missing GW30 posture code: {name}"


def test_each_gw30_code_is_a_non_empty_string() -> None:
    """R6.1: each code is a plain, non-empty `str` (no status/message/ErrorSpec attached)."""
    for name in _GW30_VECTOR_CODE_NAMES:
        value = getattr(posture, name)
        assert isinstance(value, str), f"{name} is not a str: {type(value)!r}"
        assert value, f"{name} is an empty string"


def test_gw30_codes_have_no_duplicate_spelling() -> None:
    """Each GW30 code is a unique spelling -- no two share a value."""
    values = [getattr(posture, name) for name in _GW30_VECTOR_CODE_NAMES]
    assert len(set(values)) == len(values), f"duplicate GW30 code spelling: {values}"


def test_gw30_code_set_is_pairwise_distinct() -> None:
    """The full set of GW30 codes is pairwise distinct (every pair differs)."""
    values = [getattr(posture, name) for name in _GW30_VECTOR_CODE_NAMES]
    for i, left in enumerate(values):
        for right in values[i + 1 :]:
            assert left != right, f"GW30 codes collide: {left!r} == {right!r}"


def test_unsupported_operation_differs_from_vector_ok() -> None:
    """R5.1: `UNSUPPORTED_OPERATION` is NOT `VECTOR_OK`.

    A caller distinguishes an unsupported operation from a clean zero-record success by `code`
    alone; if the two shared a spelling that distinction would be impossible.
    """
    assert posture.UNSUPPORTED_OPERATION != posture.VECTOR_OK


def test_unsupported_operation_differs_from_every_other_gw30_code() -> None:
    """R5.1: `UNSUPPORTED_OPERATION` is its own spelling, distinct from every other GW30 code."""
    unsupported = posture.UNSUPPORTED_OPERATION
    for name in _GW30_VECTOR_CODE_NAMES:
        if name == "UNSUPPORTED_OPERATION":
            continue
        other = getattr(posture, name)
        assert unsupported != other, (
            f"UNSUPPORTED_OPERATION collides with {name}: {unsupported!r} == {other!r}"
        )


def test_no_duplicate_spelling_across_the_module() -> None:
    """Single-spelling guarantee: no two module-level `str` codes share a value.

    The C37 defence -- two codes agreeing in meaning but differing (or colliding) in spelling
    break the render-boundary join. Introspecting ALL module-level `str` constants means this
    holds for the GW30 codes AND every pre-existing code together, and keeps holding as codes are
    added.
    """
    constants = _module_str_constants()
    values = list(constants.values())
    assert len(set(values)) == len(values), (
        f"duplicate code spelling across domain.posture: {sorted(constants.items())}"
    )


def test_gw30_codes_participate_in_the_module_spelling_set() -> None:
    """Guards the introspection: every GW30 code is actually seen by the module-wide check."""
    collected = _module_str_constants()
    for name in _GW30_VECTOR_CODE_NAMES:
        assert name in collected, f"{name} not collected by _module_str_constants()"


def test_gw30_code_values_match_their_names_in_snake_case() -> None:
    """Each GW30 code value is its own name lower-cased, pinning spelling to the symbol.

    This is what makes the shared vocabulary legible on both sides of the codes-vs-render
    boundary -- a code cannot drift from the name callers reference.
    """
    for name in _GW30_VECTOR_CODE_NAMES:
        value = getattr(posture, name)
        assert value == name.lower(), f"{name} value {value!r} != {name.lower()!r}"


def test_gw30_codes_are_exported_in_module_all() -> None:
    """Each GW30 code is part of the module `__all__` (task 1.1 export obligation)."""
    for name in _GW30_VECTOR_CODE_NAMES:
        assert name in posture.__all__, f"{name} missing from domain.posture.__all__"
