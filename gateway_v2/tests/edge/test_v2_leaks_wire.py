# Feature: v2-enforcement-leaks, task 24.3 — edge/wire unit tests (Groups A, F)
# Validates: Requirements 2.15, 2.20
"""Unit tests for the Group A / Group F edge/wire changes (tasks 24.1, 24.2).

These pin the wire-layer grammar and pure transforms that tasks 24.1/24.2 landed in
``edge/wire/openai.py`` and ``edge/wire/openai_misc.py``:

* :meth:`OpenAIRequestParser.parse` output-budget grammar — the absent/``null``
  defaults, the off-contract ``MALFORMED_WRONG_SHAPE`` shapes, and the parse boundary
  (``max_output_tokens_boundary()``) / over-boundary ``INVALID_MAX_*`` refusals.
* :func:`clamp_output_budget` — v1's ``min(mt, org ceiling, hard cap)`` + ``n`` clamp,
  the ``request_clamps`` record strings and order, idempotence, and the ``org_ceiling``
  guard.
* :func:`prompt_chars` — the per-message single-separator join.
* :meth:`MiscRequestParser.parse_embeddings` — the ``input_is_string`` shape flag.

Example-based unit tests (not the 10,000-iteration property idiom): each case pins a
concrete grammar / arithmetic outcome. The ``ResourceContract`` is built the same way
the shipped GW15 wire tests build it (``from_signals`` over ``HardwareSignals``); the
parse boundary (1_000_000) and the clamp hard cap (65536) are read off the contract AND
pinned to those concrete numbers so the "1_000_000 accepted / 1_000_001 refused"
semantics are legible.
"""

from __future__ import annotations

import json

import pytest

from gateway_v2.domain.plan import Surface
from gateway_v2.edge.wire.openai import (
    INVALID_MAX_COMPLETION_TOKENS,
    INVALID_MAX_TOKENS,
    MALFORMED_WRONG_SHAPE,
    CanonicalRequest,
    MalformedRequest,
    OpenAIRequestParser,
    clamp_output_budget,
    prompt_chars,
)
from gateway_v2.edge.wire.openai_misc import MiscRequestParser
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals

_PARSER = OpenAIRequestParser()
_MISC_PARSER = MiscRequestParser()


def _contract() -> ResourceContract:
    """A serviceable contract (the shared GW15 edge-test fixture shape).

    ``parse`` reads ``max_output_tokens_boundary()`` off it and ``clamp_output_budget``
    reads ``max_output_tokens()`` / ``max_completions()``; those accessors return the
    v1-parity contract constants independent of the hardware signals, so any
    serviceable ``from_signals`` contract yields the same bounds.
    """
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=2 * 1024 * 1024 * 1024,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=20.0,
        utilization_cap=0.75,
        per_worker_rss=400 * 1024 * 1024,
    )


_CONTRACT = _contract()

#: The concrete parse boundary + clamp hard cap the contract yields (v1 parity). Pinned
#: so the over-boundary refusal + clamp arithmetic cases are legible as numbers, while
#: the parse/clamp cases still derive their expectations from the contract accessors.
_BOUNDARY = 1_000_000
_HARD_CAP = 65536


def _parse(body: dict[str, object]) -> CanonicalRequest:
    """Parse a well-formed JSON body through the chat parser on the standard contract."""
    return _PARSER.parse(
        json.dumps(body).encode("utf-8"), surface=Surface.CHAT, contract=_CONTRACT
    )


def _base_body(**extra: object) -> dict[str, object]:
    """A minimal valid chat body (``model`` required) plus any extra output-budget keys."""
    body: dict[str, object] = {"model": "gpt-4"}
    body.update(extra)
    return body


# --------------------------------------------------------------------------- #
# The contract pins the v1-parity bounds (legibility anchor).
# --------------------------------------------------------------------------- #


def test_contract_boundary_and_hard_cap_are_the_v1_values() -> None:
    """The standard test contract yields the v1-parity boundary (1e6) + hard cap (65536)."""
    assert _CONTRACT.max_output_tokens_boundary() == _BOUNDARY
    assert _CONTRACT.max_output_tokens() == _HARD_CAP
    assert _CONTRACT.max_completions() == 1


# --------------------------------------------------------------------------- #
# Parser grammar — absent / explicit null defaults.
# --------------------------------------------------------------------------- #


def test_parse_absent_budget_fields_take_defaults() -> None:
    """Absent ``max_tokens`` / ``n`` / ``max_completion_tokens`` give 0 / 1 / None."""
    req = _parse(_base_body())
    assert req.max_tokens == 0
    assert req.n == 1
    assert req.max_completion_tokens is None


def test_parse_explicit_null_budget_fields_take_defaults() -> None:
    """Explicit JSON ``null`` for each budget field is treated as absent (v1 parity)."""
    req = _parse(
        _base_body(max_tokens=None, n=None, max_completion_tokens=None)
    )
    assert req.max_tokens == 0
    assert req.n == 1
    assert req.max_completion_tokens is None


# --------------------------------------------------------------------------- #
# Parser grammar — off-contract shapes raise MALFORMED_WRONG_SHAPE.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("field", ["max_tokens", "n", "max_completion_tokens"])
@pytest.mark.parametrize(
    "bad_value",
    [True, 4096.0, "100", -1],
    ids=["bool", "float", "str", "negative-int"],
)
def test_parse_off_contract_budget_value_is_wrong_shape(
    field: str, bad_value: object
) -> None:
    """A bool, float, string or negative int in any budget field is WRONG_SHAPE."""
    with pytest.raises(MalformedRequest) as excinfo:
        _parse(_base_body(**{field: bad_value}))
    assert excinfo.value.code == MALFORMED_WRONG_SHAPE


def test_parse_n_zero_is_wrong_shape() -> None:
    """``n == 0`` is below ``n``'s minimum of 1 → WRONG_SHAPE (not a boundary refusal)."""
    with pytest.raises(MalformedRequest) as excinfo:
        _parse(_base_body(n=0))
    assert excinfo.value.code == MALFORMED_WRONG_SHAPE


# --------------------------------------------------------------------------- #
# Parser grammar — the parse boundary (accepted) and the over-boundary refusals.
# --------------------------------------------------------------------------- #


def test_parse_max_tokens_at_boundary_is_accepted() -> None:
    """``max_tokens == boundary`` (1_000_000) parses through unchanged (the clamp is later)."""
    boundary = _CONTRACT.max_output_tokens_boundary()
    assert boundary == _BOUNDARY
    req = _parse(_base_body(max_tokens=boundary))
    assert req.max_tokens == boundary == 1_000_000


def test_parse_max_tokens_over_boundary_raises_invalid_max_tokens() -> None:
    """``max_tokens == boundary + 1`` → ``INVALID_MAX_TOKENS`` with ``param='max_tokens'``."""
    over = _CONTRACT.max_output_tokens_boundary() + 1
    assert over == 1_000_001
    with pytest.raises(MalformedRequest) as excinfo:
        _parse(_base_body(max_tokens=over))
    assert excinfo.value.code == INVALID_MAX_TOKENS
    assert excinfo.value.param == "max_tokens"


def test_parse_max_completion_tokens_over_boundary_raises_invalid_mct() -> None:
    """``max_completion_tokens == boundary + 1`` → ``INVALID_MAX_COMPLETION_TOKENS``."""
    over = _CONTRACT.max_output_tokens_boundary() + 1
    assert over == 1_000_001
    with pytest.raises(MalformedRequest) as excinfo:
        _parse(_base_body(max_completion_tokens=over))
    assert excinfo.value.code == INVALID_MAX_COMPLETION_TOKENS
    assert excinfo.value.param == "max_completion_tokens"


# --------------------------------------------------------------------------- #
# clamp_output_budget — org_ceiling == 4096 (the default response ceiling).
# --------------------------------------------------------------------------- #


def _req(*, max_tokens: int = 0, n: int = 1) -> CanonicalRequest:
    """A minimal CanonicalRequest carrying only the output-budget fields under test."""
    return CanonicalRequest(
        model="gpt-4",
        streaming=False,
        input_channels=(),
        text_deltas=(),
        surface=Surface.CHAT,
        max_tokens=max_tokens,
        n=n,
    )


@pytest.mark.parametrize("max_tokens", [0, 100, 4096])
def test_clamp_max_tokens_at_or_below_ceiling_is_unchanged(max_tokens: int) -> None:
    """``max_tokens`` at/below the 4096 ceiling is kept and records no clamp."""
    out = clamp_output_budget(_req(max_tokens=max_tokens), org_ceiling=4096, contract=_CONTRACT)
    assert out.max_tokens == max_tokens
    assert out.request_clamps == ()


def test_clamp_max_tokens_above_ceiling_clamps_to_ceiling() -> None:
    """``max_tokens = 1_000_000`` with ``org_ceiling = 4096`` → 4096, recorded."""
    out = clamp_output_budget(_req(max_tokens=1_000_000), org_ceiling=4096, contract=_CONTRACT)
    assert out.max_tokens == 4096
    assert "max_tokens=1000000->4096" in out.request_clamps


def test_clamp_max_tokens_bounded_by_hard_cap_when_ceiling_is_higher() -> None:
    """``org_ceiling = 100_000`` can only clamp to the 65536 hard cap, not above it."""
    assert _CONTRACT.max_output_tokens() == _HARD_CAP == 65536
    out = clamp_output_budget(
        _req(max_tokens=1_000_000), org_ceiling=100_000, contract=_CONTRACT
    )
    assert out.max_tokens == 65536
    assert "max_tokens=1000000->65536" in out.request_clamps


def test_clamp_n_one_is_unchanged() -> None:
    """``n = 1`` is already at ``max_completions()`` → kept, no record."""
    out = clamp_output_budget(_req(n=1), org_ceiling=4096, contract=_CONTRACT)
    assert out.n == 1
    assert out.request_clamps == ()


def test_clamp_n_above_max_completions_clamps_to_one() -> None:
    """``n = 8`` → 1 (v1 single-choice output guard), recorded ``"n=8->1"``."""
    out = clamp_output_budget(_req(n=8), org_ceiling=4096, contract=_CONTRACT)
    assert out.n == 1
    assert "n=8->1" in out.request_clamps


def test_clamp_is_idempotent() -> None:
    """Applying the clamp twice equals applying it once; the second pass records nothing."""
    once = clamp_output_budget(
        _req(max_tokens=1_000_000, n=8), org_ceiling=4096, contract=_CONTRACT
    )
    twice = clamp_output_budget(once, org_ceiling=4096, contract=_CONTRACT)
    assert twice.n == once.n == 1
    assert twice.max_tokens == once.max_tokens == 4096
    # The already-clamped values change nothing on the second pass → no new records.
    assert twice.request_clamps == ()


def test_clamp_records_n_before_max_tokens_when_both_change() -> None:
    """When both change, ``request_clamps`` is ``("n=8->1", "max_tokens=1000000->4096")``."""
    out = clamp_output_budget(
        _req(max_tokens=1_000_000, n=8), org_ceiling=4096, contract=_CONTRACT
    )
    assert out.request_clamps == ("n=8->1", "max_tokens=1000000->4096")


def test_clamp_rejects_org_ceiling_below_one() -> None:
    """``org_ceiling = 0`` (< 1) raises ``ValueError``."""
    with pytest.raises(ValueError):
        clamp_output_budget(_req(max_tokens=100), org_ceiling=0, contract=_CONTRACT)


# --------------------------------------------------------------------------- #
# prompt_chars — one separator per additional same-message content part.
# --------------------------------------------------------------------------- #


def test_prompt_chars_joins_same_message_parts_with_one_separator() -> None:
    """Multiple content parts of ONE message join with one separator each."""
    parts = ["alpha", "bravo", "charlie"]
    body = _base_body(
        messages=[{"role": "user", "content": [{"text": p} for p in parts]}]
    )
    req = _parse(body)
    expected = sum(len(p) for p in parts) + (len(parts) - 1)
    assert prompt_chars(req) == expected


def test_prompt_chars_adds_no_separator_across_different_messages() -> None:
    """Two single-part messages add no inter-message separator (only same-message runs do)."""
    body = _base_body(
        messages=[
            {"role": "user", "content": "alpha"},
            {"role": "assistant", "content": "bravo"},
        ]
    )
    req = _parse(body)
    assert prompt_chars(req) == len("alpha") + len("bravo")


# --------------------------------------------------------------------------- #
# parse_embeddings — the input_is_string shape flag.
# --------------------------------------------------------------------------- #


def test_parse_embeddings_string_input_sets_input_is_string() -> None:
    """A bare-string ``input`` → ``input_is_string is True`` and exactly one item."""
    body = json.dumps({"model": "text-embedding-3-small", "input": "only"}).encode("utf-8")
    req = _MISC_PARSER.parse_embeddings(body)
    assert req.input_is_string is True
    assert len(req.input_items) == 1
    assert req.input_items == ("only",)


def test_parse_embeddings_single_element_list_is_not_a_string() -> None:
    """A one-element LIST ``input`` → ``input_is_string is False`` and one item."""
    body = json.dumps({"model": "text-embedding-3-small", "input": ["only"]}).encode("utf-8")
    req = _MISC_PARSER.parse_embeddings(body)
    assert req.input_is_string is False
    assert len(req.input_items) == 1
    assert req.input_items == ("only",)
