# Feature: gw29-killswitch-isolation, Property 13: canonical_model is idempotent and mutation-stable
# Validates: Requirements 2.1, 2.4 (one canonical model identity on every side)
"""P13a: `canonical_model(canonical_model(x)) == canonical_model(x)` for random inputs."""

from __future__ import annotations

import random
import string
from types import MappingProxyType

from gateway_v2.domain.incident import canonical_model

SEED = 29013
ITERATIONS = 10_000
_ALPHABET = string.ascii_letters + string.digits + "._:/- \t\x00é日ｇ"
_ALIASES = MappingProxyType({"alias-a": "family-a", "alias-b": "family-b"})


def _random_name(rng: random.Random) -> str:
    return "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 140)))


def test_property_13a_canonicalisation_is_idempotent() -> None:
    rng = random.Random(SEED)
    for i in range(ITERATIONS):
        raw = _random_name(rng)
        for aliases in (None, _ALIASES):
            once = canonical_model(raw, aliases)
            if once is None:
                continue
            assert canonical_model(once, aliases) == once, f"seed={SEED} i={i} raw={raw!r}"


def test_property_13a_case_and_padding_mutations_collapse_to_one_name() -> None:
    rng = random.Random(SEED + 1)
    for i in range(ITERATIONS):
        base = "".join(rng.choice(string.ascii_lowercase + string.digits + "-.") for _ in range(
            rng.randint(1, 40)))
        mutated = "".join(c.upper() if rng.random() < 0.5 else c for c in base)
        padded = " \t" * rng.randint(0, 2) + mutated + " \n" * rng.randint(0, 2)
        assert canonical_model(padded) == base, f"seed={SEED + 1} i={i} base={base!r}"
        assert canonical_model(padded, _ALIASES) == _ALIASES.get(base, base), f"i={i}"
