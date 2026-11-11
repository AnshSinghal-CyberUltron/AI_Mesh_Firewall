"""GW28 routing posture codes: unique, non-empty, single spelling (code == lowercased name)."""

from __future__ import annotations

from gateway_v2.domain import posture

_NAMES = (
    "ROUTING_NO_CANDIDATES",
    "ROUTING_UNKNOWN_MODEL",
    "ROUTING_ALL_UNAVAILABLE",
    "ROUTING_ATTEMPTS_EXHAUSTED",
    "ROUTING_TEARDOWN_UNCONFIRMED",
    "ROUTING_CONTEXT_MISSING",
    "ROUTING_SNAPSHOT_UNAVAILABLE",
    "ROUTING_INTERNAL_ERROR",
)


def test_routing_codes_single_spelling_and_exported() -> None:
    values = [getattr(posture, n) for n in _NAMES]
    assert values == [n.lower() for n in _NAMES]
    assert len(set(values)) == len(values)
    assert all(n in posture.__all__ for n in _NAMES)


def test_routing_codes_do_not_collide_with_other_codes() -> None:
    others = {
        v
        for k, v in vars(posture).items()
        if k.isupper() and isinstance(v, str) and k not in _NAMES
    }
    assert not others & {getattr(posture, n) for n in _NAMES}
