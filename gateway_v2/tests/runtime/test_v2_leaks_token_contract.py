"""v2-enforcement-leaks Group F task 15.5 -- runtime token contract unit + property tests.

# Validates: Requirements 2.15

Covers the runtime additions of tasks 15.1-15.4:

* ``ResourceContract.estimate_request_tokens(chars, max_tokens, n)`` -- v1's
  ``LLMRouter.estimate_prompt_tokens`` (input ``max(1, chars // 4)`` when ``chars > 0`` else 0,
  plus ``max_tokens * n``, at least 1), fail-closed on a negative argument or ``n < 1``;
* ``_CHARS_PER_TOKEN_DIVISOR`` and the four v1 output-budget constants behind their accessors,
  plus the import-time ordering guard ``_check_output_budget_ordering``;
* ``max_request_tokens() == max(1, max_request_bytes() // 4) + 65536``;
* ``StoreKeys.budget_workers(org)`` -- the active-lease-worker ZSET under the ``{rv2}`` hash tag.

The property test uses the house idiom (a seeded ``random.Random``, no ``hypothesis``,
>= 10,000 iterations, the seed in every assertion message) and compares against the FROZEN v1
oracle ``tests/admit/_v1_token_estimate_oracle.estimate_prompt_tokens``.
"""

from __future__ import annotations

import random

import pytest

import gateway_v2.runtime.resources as resources
from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.kinds import HardwareSignals
from gateway_v2.runtime.resources import ResourceContract, from_signals
from gateway_v2.runtime.store_keys import DEFAULT_NAMESPACE, KEYS, StoreKeys
from tests.admit._v1_token_estimate_oracle import estimate_prompt_tokens

_GIB = 1024 * 1024 * 1024


def _contract(*, per_worker_rss: int = 2 * _GIB) -> ResourceContract:
    """A serviceable contract via ``from_signals`` (same shape as the other runtime tests)."""
    signals = HardwareSignals(
        cpu_quota=4.0,
        memory_limit=per_worker_rss * 8,
        fd_limit=4096,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )
    return from_signals(
        signals,
        target_p99_ms=20.0,
        utilization_cap=0.75,
        per_worker_rss=per_worker_rss,
    )


# ===========================================================================
# estimate_request_tokens -- examples
# ===========================================================================


@pytest.mark.parametrize(
    ("chars", "max_tokens", "n", "expected"),
    [
        (0, 0, 1, 1),  # empty prompt, no output budget -> the floor of 1
        (3, 0, 1, 1),  # under one divisor of chars still costs 1 input token
        (8, 0, 1, 2),
        (2000, 256, 2, 1012),  # 500 input + 256 * 2 output
        (0, 100, 3, 300),  # no input tokens at all when chars == 0
    ],
)
def test_estimate_request_tokens_examples(
    chars: int, max_tokens: int, n: int, expected: int
) -> None:
    assert ResourceContract.estimate_request_tokens(chars, max_tokens, n) == expected


@pytest.mark.parametrize(
    ("chars", "max_tokens", "n"),
    [
        (-1, 0, 1),
        (0, -1, 1),
        (0, 0, 0),
        (0, 0, -1),
        (10, 10, 0),
    ],
)
def test_estimate_request_tokens_rejects_invalid_arguments(
    chars: int, max_tokens: int, n: int
) -> None:
    with pytest.raises(ValueError):
        ResourceContract.estimate_request_tokens(chars, max_tokens, n)


# ===========================================================================
# Constants, accessors and the ordering guard
# ===========================================================================


def test_chars_per_token_divisor_is_v1_four() -> None:
    assert resources._CHARS_PER_TOKEN_DIVISOR == 4


def test_output_budget_accessor_values() -> None:
    assert ResourceContract.max_output_tokens() == 65536
    assert ResourceContract.default_response_token_ceiling() == 4096
    assert ResourceContract.max_output_tokens_boundary() == 1_000_000
    assert ResourceContract.max_completions() == 1


def test_output_budget_ordering_holds_for_shipped_constants() -> None:
    resources._check_output_budget_ordering()  # must not raise


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("_DEFAULT_RESPONSE_TOKEN_CEILING", 0),  # ceiling below 1
        ("_DEFAULT_RESPONSE_TOKEN_CEILING", 65537),  # ceiling above the hard cap
        ("_OUTPUT_TOKENS_HARD_CAP", 4095),  # hard cap below the ceiling
        ("_OUTPUT_TOKENS_HARD_CAP", 1_000_001),  # hard cap above the boundary
        ("_MAX_OUTPUT_TOKENS_BOUNDARY", 65535),  # boundary below the hard cap
        ("_MAX_COMPLETIONS_PER_REQUEST", 0),  # uncostable requests
    ],
)
def test_output_budget_ordering_fails_closed(
    monkeypatch: pytest.MonkeyPatch, name: str, value: int
) -> None:
    """A mis-ordered constant makes the guard refuse to start; monkeypatch restores it."""
    monkeypatch.setattr(resources, name, value)
    with pytest.raises(CapacityUnavailable):
        resources._check_output_budget_ordering()


# ===========================================================================
# max_request_tokens
# ===========================================================================


def test_max_request_tokens_formula() -> None:
    contract = _contract()
    expected = max(1, contract.max_request_bytes() // 4) + 65536
    assert contract.max_request_tokens() == expected


def test_max_request_tokens_at_two_gib_rss() -> None:
    assert _contract(per_worker_rss=2 * _GIB).max_request_tokens() == 1_407_713


# ===========================================================================
# store_keys.budget_workers
# ===========================================================================


def test_budget_workers_key_shape() -> None:
    assert KEYS.budget_workers("acme") == "{rv2}:budget:acme:workers"
    assert StoreKeys(namespace="{ns}").budget_workers("acme") == "{ns}:budget:acme:workers"


def test_budget_workers_shares_hash_tag_with_lease_family() -> None:
    org = "acme"
    family = (
        KEYS.budget_workers(org),
        KEYS.budget_remaining(org),
        KEYS.budget_generation(org),
        KEYS.budget_lease(org, "w-1"),
    )
    for key in family:
        assert key.startswith(f"{DEFAULT_NAMESPACE}:budget:{org}:"), key
    assert len(set(family)) == len(family)


def test_budget_workers_rejects_empty_org() -> None:
    with pytest.raises(ValueError):
        KEYS.budget_workers("")


# ===========================================================================
# Property 12 -- v2 estimate == frozen v1 oracle (seeded, no hypothesis)
# ===========================================================================

_SEED = 0x15_05
_ITERATIONS = 10_000
_ALPHABET = "abcdefghijklmnopqrstuvwxyz ABCXYZ0123456789.,!?\n\té漢字🙂"


def _random_content(rng: random.Random) -> str:
    """A prompt biased toward the divisor boundaries (0-8 chars) with a long tail."""
    length = rng.randint(0, 8) if rng.random() < 0.3 else rng.randint(0, 4000)
    return "".join(rng.choice(_ALPHABET) for _ in range(length))


def test_property_estimate_matches_v1_oracle() -> None:
    """**Validates: Requirements 2.15** (Property 12: the v1 token estimate)."""
    rng = random.Random(_SEED)
    for i in range(_ITERATIONS):
        content = _random_content(rng)
        max_tokens = rng.choice((0, rng.randint(0, 64), rng.randint(0, 65536)))
        n = rng.randint(1, 8)
        messages: list[object] = [{"role": "user", "content": content}]
        want = estimate_prompt_tokens(messages, max_tokens, n)
        got = ResourceContract.estimate_request_tokens(len(content), max_tokens, n)
        assert got == want, (
            f"seed={_SEED:#x} iter={i}: chars={len(content)} max_tokens={max_tokens} n={n} "
            f"v2={got} v1={want}"
        )
