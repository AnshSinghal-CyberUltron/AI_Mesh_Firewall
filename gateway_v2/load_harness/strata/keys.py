"""Multi-key stratum — key/org population + per-request key assignment (B6 / R2-21, Req 3).

The multi-key stratum spreads offered load across a configured population of many distinct API
keys spanning many orgs, so capacity is measured under realistic key cardinality rather than a
single hot key (Req 3). This module is the pure generation half: a validated ``KeyPopulation``
built from two primitives (``num_keys``, ``num_orgs``), a pure per-request draw
``assign(population, rng)`` keyed by an injected ``random.Random``, and a small distinct-count
helper so the driver/report can surface the distinct keys + distinct orgs actually exercised over a
run (Req 3.4).

Self-contained by design. The ``arrivals.Arrival`` type is being built in parallel; to avoid a
cross-dependency on an in-flight sibling module this module imports nothing from the rest of the
harness. ``assign`` takes only a ``KeyPopulation`` and an injected ``random.Random`` and returns a
frozen ``KeyAssignment`` carrying both the key id and its org id; a caller that needs to tie an
assignment to a specific arrival keys its own ``rng`` per arrival index — the determinism spine
(same seed ⇒ same draw sequence) lives at the composer, not here.

Determinism + injection (Req 13). Every draw is a pure function of ``(population, rng)``; the live
``random.Random`` is injected (never module-global ``random.*``), so a population + its assignments
are reproducible and verifiable offline without a running server.

Floors and ceiling are stratum-POPULATION policy, NOT host capacity positions (Req 3.1, 3.2;
Req 14.2). ``_MIN_KEYS`` (2,000), ``_MIN_ORGS`` (200) and ``_MAX_KEYS`` (50,000) are the GW20b
multi-key stratum population floors/ceiling named in the requirement — the realistic key
cardinality the *offered traffic* must span. They are the same justification class as
``latency_slo._LOST_SEGMENT_TAIL_MS``: operational/statistical policy of the measurement
instrument, not a value derived from the host's CPU / RAM / FD / RPS budget, so they are not
capacity-position literals. ``lint/check_capacity_literals.py`` keys on capacity-position *names*
(``max_connections`` / ``workers`` / ``pool_size`` / ``queue_depth`` / ...) assigned to int
constants; none of these constants carries such a name, so the gate does not flag them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from random import Random

__all__ = (
    "KeyAssignment",
    "KeyPopulation",
    "assign",
    "build_key_population",
    "distinct_counts",
)


# --------------------------------------------------------------------------------------------------
# Stratum-population policy constants (NOT capacity-position literals; see module docstring).
#
# These are the GW20b multi-key stratum population floors/ceiling from Req 3.1 / 3.2 — the realistic
# key cardinality the OFFERED TRAFFIC must span, an operational/statistical policy of the instrument
# in the same justification class as `latency_slo._LOST_SEGMENT_TAIL_MS`. None is derived from the
# host's CPU / RAM / FD / RPS budget, and none carries a capacity-position name that
# `lint/check_capacity_literals.py` scans for, so none is a capacity-position literal.
# --------------------------------------------------------------------------------------------------

_MIN_KEYS = 2_000
"""Minimum distinct API keys the multi-key population must span when the stratum is enabled.

STRATUM-POPULATION policy (Req 3.1). GW20b signs a fleet knee only under a multi-key stratum of
"≥ 2,000 active keys"; the builder refuses a smaller enabled population. A realistic-cardinality
floor of the offered traffic, not a host capacity position.
"""

_MIN_ORGS = 200
"""Minimum distinct orgs the key population must span when the stratum is enabled.

STRATUM-POPULATION policy (Req 3.1). GW20b requires the ≥ 2,000 keys to span "≥ 200 orgs"; the
builder refuses fewer. A realistic-cardinality floor of the offered traffic, not a host capacity
position.
"""

_MAX_KEYS = 50_000
"""Maximum distinct API keys the multi-key population may be configured to (Req 3.2).

STRATUM-POPULATION policy. GW20b supports a configured population "up to 50,000 keys"; the builder
refuses a larger population. A realistic-cardinality ceiling of the offered traffic, not a host
capacity position.
"""


@dataclass(frozen=True, slots=True)
class KeyAssignment:
    """The key + its org drawn for one offered request (frozen + slots, Req 14.3).

    ``key_id`` is a stable index into the population's key space ``[0, num_keys)`` and ``org_id`` is
    the index into the org space ``[0, num_orgs)`` that owns that key. Carrying both ids on the
    assignment (rather than only the key) lets the driver/report count the distinct keys AND the
    distinct orgs actually exercised over a run without re-deriving ownership (Req 3.4,
    ``distinct_counts``).
    """

    key_id: int
    org_id: int


@dataclass(frozen=True, slots=True)
class KeyPopulation:
    """A validated multi-key population: ``num_keys`` keys spread over ``num_orgs`` orgs.

    ``num_keys`` honors ``[_MIN_KEYS, _MAX_KEYS]`` and ``num_orgs`` honors ``>= _MIN_ORGS`` with
    ``num_orgs <= num_keys`` (every org owns at least one key) when the stratum is enabled — see
    ``build_key_population``, the only sanctioned constructor. Keys are owned by orgs by the stable
    rule ``org_id = key_id % num_orgs`` (round-robin), so every org owns at least one key and the
    ownership is a pure function of the ids — no per-key table is stored, keeping the value type a
    pair of ints and the whole module offline-reproducible. A per-request draw picks a uniform key
    id in ``[0, num_keys)`` and reports its owning org.
    """

    num_keys: int
    num_orgs: int

    def org_of(self, key_id: int) -> int:
        """The org id owning ``key_id`` under the stable round-robin ownership rule."""
        return key_id % self.num_orgs


def build_key_population(num_keys: int, num_orgs: int) -> KeyPopulation:
    """Build a validated ``KeyPopulation`` of ``num_keys`` keys over ``num_orgs`` orgs.

    Honors the GW20b multi-key stratum floors/ceiling (Req 3.1, 3.2): ``num_keys`` must lie in
    ``[_MIN_KEYS, _MAX_KEYS]`` (≥ 2,000 and ≤ 50,000) and ``num_orgs`` must be ``>= _MIN_ORGS``
    (≥ 200) with ``num_orgs <= num_keys`` so every org owns at least one key under the round-robin
    ownership rule. Unlike ``config.load_run_spec`` this is a hard builder (not warn-and-default):
    an out-of-range population is a programming error at the stratum boundary, so it raises
    ``ValueError`` rather than silently clamping — the config layer is where operator input is
    softened, the stratum builder enforces the invariant the rest of the module relies on.
    """
    if num_keys < _MIN_KEYS or num_keys > _MAX_KEYS:
        raise ValueError(
            f"num_keys={num_keys} outside the multi-key stratum population range "
            f"[{_MIN_KEYS}, {_MAX_KEYS}]"
        )
    if num_orgs < _MIN_ORGS:
        raise ValueError(
            f"num_orgs={num_orgs} below the multi-key stratum org floor {_MIN_ORGS}"
        )
    if num_orgs > num_keys:
        raise ValueError(
            f"num_orgs={num_orgs} exceeds num_keys={num_keys}; every org must own at least one key"
        )
    return KeyPopulation(num_keys=num_keys, num_orgs=num_orgs)


def assign(population: KeyPopulation, rng: Random) -> KeyAssignment:
    """Draw one ``KeyAssignment`` from ``population`` using the injected ``rng`` (Req 3.3).

    The configured distribution is uniform over the key space (the design does not single out a
    skewed key distribution for this stratum): a key id is drawn uniformly from ``[0, num_keys)``
    and paired with its owning org under the population's stable ownership rule. Pure in
    ``(population, rng)`` and keyed only by the injected ``random.Random`` — never module-global
    ``random.*`` — so the same seeded ``rng`` reproduces the same draw sequence offline (Req 13).
    """
    key_id = rng.randrange(population.num_keys)
    return KeyAssignment(key_id=key_id, org_id=population.org_of(key_id))


def distinct_counts(assignments: Iterable[KeyAssignment]) -> tuple[int, int]:
    """Report ``(distinct_keys, distinct_orgs)`` exercised across ``assignments`` (Req 3.4).

    The driver/report accumulates the per-request ``KeyAssignment`` values a run offered and passes
    them here to surface the distinct-key and distinct-org counts GW20b records per run (Req 3.4).
    A pure fold over the ids on the assignments — it reads nothing but what the draws produced, so
    the reported counts never exceed the population and never exceed what the realized draws
    admitted.
    """
    keys: set[int] = set()
    orgs: set[int] = set()
    for assignment in assignments:
        keys.add(assignment.key_id)
        orgs.add(assignment.org_id)
    return len(keys), len(orgs)
