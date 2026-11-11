# Feature: gw29-killswitch-isolation, Property 13: name mutations of an engaged model never reach it
# Validates: Requirements 2.1, 2.2, 2.4 (one canonical identity; alias kill = family kill)
"""P13 over case / whitespace / alias mutations of killed and isolated models."""

from __future__ import annotations

import random

from gateway_v2.domain.incident import ModelProfile, RequestConstraints
from gateway_v2.domain.posture import KILL_SWITCH_ENGAGED, MODEL_KILLED
from tests.admit._lgw29_support import GateRig, iso_body, ks

SEED = 29013
ITERATIONS = 5_000
ALIASES = {"gpt-4o-2024-08-06": "gpt-4o", "gpt-4o-latest": "gpt-4o", "fast": "mini"}
TAGS = frozenset({"t"})
PROFILES = {n: ModelProfile(n, "p", TAGS, 2) for n in ("gpt-4o", "mini", "other")}
KNOWN = RequestConstraints(True, None, frozenset(), 0)
_FAMILY = ("gpt-4o", "gpt-4o-2024-08-06", "gpt-4o-latest")


def _mutate(name: str, rng: random.Random) -> str:
    cased = "".join(c.upper() if rng.random() < 0.5 else c for c in name)
    return " \t" * rng.randint(0, 2) + cased + " \n" * rng.randint(0, 2)


def test_property_13_killed_family_is_unreachable_under_any_spelling() -> None:
    rng = random.Random(SEED)
    for i in range(ITERATIONS):
        rig = GateRig(aliases=ALIASES)
        killed_as = rng.choice(_FAMILY)
        scope = rng.choice(("model", "om"))
        key = f"model:{_mutate(killed_as, rng).strip()}" if scope == "model" else (
            f"om:a:{_mutate(killed_as, rng).strip()}")
        rig.engage(ks(key, feed_seq=1))
        asked = _mutate(rng.choice(_FAMILY), rng)
        v = rig.decide(asked, "a")
        tag = f"seed={SEED} i={i} killed={key!r} asked={asked!r}"
        assert v.routed_model is None, f"{tag} reached a killed model"
        assert v.refuse_code == (MODEL_KILLED if scope == "model" else KILL_SWITCH_ENGAGED), tag
        other_org = rig.decide(asked, "b")
        assert (other_org.routed_model is None) == (scope == "model"), f"{tag} org scoping"


def test_property_13_an_isolated_family_is_rerouted_or_refused_never_sent_through() -> None:
    rng = random.Random(SEED + 1)
    for i in range(ITERATIONS):
        rig = GateRig(aliases=ALIASES, catalog=True, profiles=PROFILES, constraints=KNOWN,
                      fallbacks={})
        isolated_as = rng.choice(_FAMILY)
        fallback = rng.choice(("fast", "mini", "other", None))
        body = iso_body(expires_at=9e9, **({"fallback": fallback} if fallback else {}))
        rig.engage(ks(f"iso:{_mutate(isolated_as, rng).strip()}", body=body, feed_seq=1))
        asked = _mutate(rng.choice(_FAMILY), rng)
        v = rig.decide(asked, "a")
        tag = f"seed={SEED + 1} i={i} fallback={fallback} asked={asked!r}"
        assert v.routed_model != "gpt-4o", f"{tag} sent to an isolated model"
        if v.routed_model is not None:
            assert v.routed_model in ("mini", "other") and v.reroute is not None, tag


def test_property_13_unresolvable_and_unknown_names_are_refused_never_sent() -> None:
    rng = random.Random(SEED + 2)
    junk = ("", " ", "a b", "é", "m" * 129, "x\x00y", "no-such-model", "GPT-5", "../etc")
    for i in range(500):
        rig = GateRig(aliases=ALIASES, catalog=True, profiles=PROFILES)
        v = rig.decide(rng.choice(junk) + rng.choice(("", " ")), "a")
        assert v.routed_model is None and v.refuse_code == MODEL_KILLED, f"seed={SEED + 2} i={i}"
        assert v.cause in ("unresolvable_model", "unknown_model"), f"seed={SEED + 2} i={i}"


def test_property_13_the_routed_name_is_the_canonical_name_that_was_checked() -> None:
    rng = random.Random(SEED + 3)
    for i in range(ITERATIONS):
        rig = GateRig(aliases=ALIASES)
        raw = _mutate(rng.choice((*_FAMILY, "mini", "fast", "plain")), rng)
        v = rig.decide(raw, "a")
        canonical = ALIASES.get(raw.strip().casefold(), raw.strip().casefold())
        assert v.routed_model == canonical, f"seed={SEED + 3} i={i} raw={raw!r}"
        assert v.routed_model == v.routed_model.strip().casefold()  # type: ignore[union-attr]
