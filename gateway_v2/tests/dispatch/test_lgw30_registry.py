"""LGW30 no-silent-fallback property test (GW30, task 8.2).

# Feature: vector-store-adapter-gw30, Property 1: No silent fallback
# Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 15.2

Property 1 (design §Correctness Properties → Property 1): *for all* registry
contents and *for all* requested ``store_id`` values,
``VectorRegistry.select(store_id)``
(``gateway_v2/dispatch/vector_registry.py``) resolves to the connector registered
under that **exact** id when present, and otherwise to a frozen
:class:`~gateway_v2.dispatch.vector_adapter.AdapterResult` carrying
``posture.STORE_NOT_CONFIGURED`` — **never** a different connector, **never** "any
available", **never** ``None``, and **never** a raised exception. The v1
``_resolve_vector_client`` anti-pattern (fall back to any reachable store on a
miss) is barred by construction.

What the property pins (R7.1–R7.5, R15.2):

* **Hit returns the EXACT object (identity).** A request against a registered
  ``store_id`` returns *the very same* connector object registered under that id
  — asserted with ``is``, not equality — so the registry can never substitute a
  look-alike (R7.1).
* **Miss is a declared code, fail-closed.** An unregistered id returns an
  :class:`AdapterResult` with ``code == posture.STORE_NOT_CONFIGURED`` and
  ``ok is False`` — a frozen declared-code value, never ``None`` and never a
  raised exception, so the caller tells a miss from a hit by ``code`` alone
  (R7.2).
* **At most one connector per select.** Selection is a single exact-key lookup,
  so for any one id the registry hands back *at most one* of the registered
  connectors (R7.3, R7.5): the returned connector is either absent (the miss
  result) or is identically one specific registered connector and no other.
* **No fallback on a miss.** Selecting an unregistered id never returns *any* of
  the registered connectors — not the first, not a load/round-robin pick, not a
  near-name match (R7.4). This is the direct negation of the
  ``_resolve_vector_client`` "any available" behaviour (R15.2).

House idiom: a single seeded ``random.Random`` drives ``_ITERATIONS`` trials;
each trial randomises the registered id set (names + count), which
:class:`~gateway_v2.dispatch.vector_connectors.Stub_Vector_Connector` object maps
to which id, and the requested id (sometimes a registered id, sometimes an
unregistered one). The registered connectors are socket-free
``Stub_Vector_Connector`` instances built with an injected ``random.Random``, a
short script, and a :class:`~gateway_v2.dispatch.vector_adapter.Capabilities`
value. No ``hypothesis``; ``select`` is synchronous so no ``asyncio`` is needed.

_Design: Testing Strategy (property tests); Components §VectorRegistry;
Correctness Properties → Property 1._
_Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 15.2._
"""

from __future__ import annotations

import random
import string

from gateway_v2.dispatch.vector_adapter import (
    AdapterResult,
    Capabilities,
    Vector_Adapter,
)
from gateway_v2.dispatch.vector_connectors import Stub_Vector_Connector
from gateway_v2.dispatch.vector_registry import VectorRegistry
from gateway_v2.domain import posture

_ITERATIONS = 10_000

# A pool of candidate id spellings the generator draws REGISTERED ids from. The
# requested id is sometimes drawn from the registered set (a hit) and sometimes
# synthesised OUTSIDE it (a guaranteed miss), so both branches of ``select`` are
# exercised across the run.
_ID_ALPHABET = string.ascii_lowercase + string.digits + "_-"


def _random_id(rng: random.Random) -> str:
    """A short random ``store_id`` spelling drawn from ``_ID_ALPHABET``."""
    length = rng.randint(1, 8)
    return "".join(rng.choice(_ID_ALPHABET) for _ in range(length))


def _random_capabilities(rng: random.Random) -> Capabilities:
    """A :class:`Capabilities` value with all three flags defined from ``rng``."""
    return Capabilities(
        filters=rng.random() < 0.5,
        namespaces=rng.random() < 0.5,
        hybrid_search=rng.random() < 0.5,
    )


def _random_connector(rng: random.Random) -> Stub_Vector_Connector:
    """A socket-free :class:`Stub_Vector_Connector` with an injected ``rng``.

    The ``rng`` is owned by the test (never created inside the stub), the script
    is short, and the capabilities are fully defined — exactly the constructor
    contract the stub declares. The connector is used here only as an *object
    identity* the registry must return verbatim; its script is never replayed.
    """
    script = (AdapterResult(code=posture.VECTOR_OK, records=()),)
    return Stub_Vector_Connector(
        script=script,
        capabilities=_random_capabilities(rng),
        rng=random.Random(rng.randint(0, 2**63 - 1)),
    )


def _build_registry(
    rng: random.Random,
) -> tuple[VectorRegistry, dict[str, Stub_Vector_Connector]]:
    """Build a registry over a random id set, returning it plus the truth map.

    The id set size, the id spellings, and which connector object maps to which
    id are all driven by ``rng``. The returned ``dict`` is the ground truth the
    property checks ``select`` against: every mapping is a distinct connector
    object, so an identity check can tell *which* connector came back.
    """
    count = rng.randint(0, 6)
    mapping: dict[str, Stub_Vector_Connector] = {}
    while len(mapping) < count:
        store_id = _random_id(rng)
        if store_id in mapping:
            continue  # keep ids distinct so identity maps one-to-one
        mapping[store_id] = _random_connector(rng)
    return VectorRegistry(mapping), mapping


def _unregistered_id(rng: random.Random, registered: dict[str, object]) -> str:
    """A ``store_id`` guaranteed NOT in ``registered`` (a forced miss)."""
    while True:
        candidate = _random_id(rng) + rng.choice(("#", "!", ".", "/", " "))
        if candidate not in registered:
            return candidate


def test_select_is_exact_match_with_no_silent_fallback() -> None:
    """Property 1: ``select`` returns the exact connector on a hit, a declared miss otherwise.

    For each trial the test builds a registry over a random id set, then issues a
    ``select`` that is — at random — either a registered id (hit) or an id forced
    outside the set (miss), plus an always-forced miss to exercise that branch on
    every trial. It asserts the four edges of Property 1:

    * a hit returns *the exact same object* (``is``) registered under that id;
    * a miss returns an :class:`AdapterResult` with
      ``code == posture.STORE_NOT_CONFIGURED`` and ``ok is False`` — never
      ``None``, never a raised exception;
    * at most one connector is ever returned (the result is either the miss value
      or identically one registered connector and no other);
    * selecting an unregistered id never returns *any* registered connector.

    _Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 15.2._
    """
    driver = random.Random(0x5A1EC7)

    for _ in range(_ITERATIONS):
        trial_seed = driver.randint(0, 2**63 - 1)
        gen = random.Random(trial_seed)

        registry, truth = _build_registry(gen)
        registered_ids = tuple(truth.keys())
        registered_objects = set(map(id, truth.values()))

        # Decide this trial's primary request: a registered id (hit) when the
        # set is non-empty and the coin says so, otherwise a forced miss.
        want_hit = bool(registered_ids) and gen.random() < 0.5
        if want_hit:
            requested = gen.choice(registered_ids)
        else:
            requested = _unregistered_id(gen, truth)

        # ``select`` never raises: a miss is a declared-code value, not an
        # exception. If this call raises, the trial fails here with the seed.
        result = registry.select(requested)

        if requested in truth:
            # HIT: the exact registered object, by identity (R7.1).
            expected = truth[requested]
            assert result is expected, (
                f"hit at seed={trial_seed} returned a different object: "
                f"{result!r} is not {expected!r}"
            )
            # A returned connector satisfies the adapter protocol (sanity).
            assert isinstance(result, Vector_Adapter)
            # At most one connector returned, and it is THIS id's connector —
            # never a different registered connector (R7.3, R7.5).
            assert id(result) == id(expected)
        else:
            # MISS: a frozen declared-code AdapterResult, fail-closed (R7.2).
            assert isinstance(result, AdapterResult), (
                f"miss at seed={trial_seed} returned {type(result)!r}, not AdapterResult"
            )
            assert result is not None  # never None on a miss
            assert result.code == posture.STORE_NOT_CONFIGURED
            assert result.ok is False
            # No silent fallback: the miss value is NOT any registered connector
            # (R7.4, R15.2) — the AdapterResult is not a Stub_Vector_Connector,
            # and its identity is none of the registered connectors'.
            assert not isinstance(result, Stub_Vector_Connector)
            assert id(result) not in registered_objects

        # On EVERY trial, an id forced outside the set returns the declared miss
        # and never a registered connector — the fallback negation holds even
        # when the primary request above happened to be a hit (R7.4, R7.5).
        forced_miss_id = _unregistered_id(gen, truth)
        miss = registry.select(forced_miss_id)
        assert isinstance(miss, AdapterResult)
        assert miss.code == posture.STORE_NOT_CONFIGURED
        assert miss.ok is False
        assert id(miss) not in registered_objects


def test_registered_id_is_isolated_from_every_other_connector() -> None:
    """Each registered id resolves ONLY to its own connector, never a neighbour (R7.1, R7.5).

    Builds a registry with at least two distinct connectors and asserts that
    selecting each id returns that id's connector and no other registered
    connector — the "at most one connector, and the RIGHT one" edge, checked
    exhaustively across the whole registered set each trial.

    _Validates: Requirements 7.1, 7.3, 7.5._
    """
    driver = random.Random(0xC0FFEE)

    for _ in range(_ITERATIONS):
        trial_seed = driver.randint(0, 2**63 - 1)
        gen = random.Random(trial_seed)

        # Force at least two entries so "no neighbour returned" is meaningful.
        registry, truth = _build_registry(gen)
        while len(truth) < 2:
            store_id = _random_id(gen)
            if store_id not in truth:
                truth[store_id] = _random_connector(gen)
        registry = VectorRegistry(truth)

        for store_id, connector in truth.items():
            selected = registry.select(store_id)
            # The exact object for this id.
            assert selected is connector, (
                f"id {store_id!r} at seed={trial_seed} resolved to the wrong object"
            )
            # And never any OTHER registered connector.
            for other_id, other in truth.items():
                if other_id == store_id:
                    continue
                assert selected is not other
