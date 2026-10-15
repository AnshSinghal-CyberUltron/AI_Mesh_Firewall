"""Tenant-scale stratum — tenant population + per-request tenant assignment (B6 / R2-21, Req 7).

The tenant-scale stratum spreads offered load across a large configured tenant population, so
capacity is measured under the tenant cardinality GW05c targets rather than a single tenant
(Req 7). This module is the pure generation half: a validated ``TenantPopulation`` built from one
primitive (``num_tenants``), a pure per-request draw ``assign(population, rng)`` keyed by an
injected ``random.Random``, and a small distinct-count helper so the driver/report can surface the
distinct tenants actually exercised over a run (Req 7.3).

Self-contained by design. Mirroring the ``strata/keys.py`` sibling, this module imports nothing
from the rest of the harness (not ``arrivals.py``, not ``keys.py``): ``assign`` takes only a
``TenantPopulation`` and an injected ``random.Random`` and returns a frozen ``TenantAssignment``
carrying the tenant id. A caller that needs to tie an assignment to a specific arrival keys its own
``rng`` per arrival index — the determinism spine (same seed ⇒ same draw sequence) lives at the
composer (``request_plan.py``), not here.

Determinism + injection (Req 13). Every draw is a pure function of ``(population, rng)``; the live
``random.Random`` is injected (never module-global ``random.*``), so a population + its assignments
are reproducible and verifiable offline without a running server.

The floor is stratum-POPULATION policy, NOT a host capacity position (Req 7.1; Req 14.2).
``_MIN_TENANTS`` (10,000) is the GW20b tenant-scale stratum population floor named in the
requirement — the realistic tenant cardinality the *offered traffic* must span. It is the same
justification class as ``keys._MIN_KEYS`` and ``latency_slo._LOST_SEGMENT_TAIL_MS``:
operational/statistical policy of the measurement instrument, not a value derived from the host's
CPU / RAM / FD / RPS budget, so it is not a capacity-position literal.
``lint/check_capacity_literals.py`` keys on capacity-position *names* (``max_connections`` /
``workers`` / ``pool_size`` / ``queue_depth`` / ...) assigned to int constants; ``_MIN_TENANTS``
carries no such name, so the gate does not flag it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from random import Random

__all__ = (
    "TenantAssignment",
    "TenantPopulation",
    "assign",
    "build_tenant_population",
    "distinct_counts",
    "distinct_tenants",
)


# --------------------------------------------------------------------------------------------------
# Stratum-population policy constant (NOT a capacity-position literal; see module docstring).
#
# This is the GW20b tenant-scale stratum population floor from Req 7.1 — the realistic tenant
# cardinality the OFFERED TRAFFIC must span, an operational/statistical policy of the instrument in
# the same justification class as `keys._MIN_KEYS` and `latency_slo._LOST_SEGMENT_TAIL_MS`. It is
# not derived from the host's CPU / RAM / FD / RPS budget, and carries no capacity-position name
# `lint/check_capacity_literals.py` scans for, so it is not a capacity-position literal.
# --------------------------------------------------------------------------------------------------

_MIN_TENANTS = 10_000
"""Minimum distinct tenants the tenant-scale population must span when the stratum is enabled.

STRATUM-POPULATION policy (Req 7.1). GW20b signs a fleet knee only under a tenant-scale stratum of
"≥ 10,000 tenants" (the cardinality GW05c targets); the builder refuses a smaller enabled
population. A realistic-cardinality floor of the offered traffic, not a host capacity position.
"""


@dataclass(frozen=True, slots=True)
class TenantAssignment:
    """The tenant drawn for one offered request (frozen + slots, Req 14.3).

    ``tenant_id`` is a stable index into the population's tenant space ``[0, num_tenants)``.
    Carrying it on the assignment lets the driver/report count the distinct tenants actually
    exercised over a run (Req 7.3, ``distinct_counts`` / ``distinct_tenants``).
    """

    tenant_id: int


@dataclass(frozen=True, slots=True)
class TenantPopulation:
    """A validated tenant-scale population of ``num_tenants`` tenants.

    ``num_tenants`` honors ``>= _MIN_TENANTS`` (≥ 10,000) when the stratum is enabled — see
    ``build_tenant_population``, the only sanctioned constructor. Tenants are a flat id space
    ``[0, num_tenants)``; a per-request draw picks a uniform tenant id, so the value type is a
    single int and the whole module is offline-reproducible.
    """

    num_tenants: int


def build_tenant_population(num_tenants: int) -> TenantPopulation:
    """Build a validated ``TenantPopulation`` of ``num_tenants`` tenants.

    Honors the GW20b tenant-scale stratum floor (Req 7.1): ``num_tenants`` must be
    ``>= _MIN_TENANTS`` (≥ 10,000). Unlike ``config.load_run_spec`` this is a hard builder (not
    warn-and-default): an
    out-of-range population is a programming error at the stratum boundary, so it raises
    ``ValueError`` rather than silently clamping — the config layer is where operator input is
    softened, the stratum builder enforces the invariant the rest of the module relies on (the same
    rationale as ``keys.build_key_population``).
    """
    if num_tenants < _MIN_TENANTS:
        raise ValueError(
            f"num_tenants={num_tenants} below the tenant-scale stratum population floor "
            f"{_MIN_TENANTS}"
        )
    return TenantPopulation(num_tenants=num_tenants)


def assign(population: TenantPopulation, rng: Random) -> TenantAssignment:
    """Draw one ``TenantAssignment`` from ``population`` using the injected ``rng`` (Req 7.2).

    The configured distribution is uniform over the tenant space (the design does not single out a
    skewed tenant distribution for this stratum): a tenant id is drawn uniformly from
    ``[0, num_tenants)``. Pure in ``(population, rng)`` and keyed only by the injected
    ``random.Random`` — never module-global ``random.*`` — so the same seeded ``rng`` reproduces the
    same draw sequence offline (Req 13).
    """
    tenant_id = rng.randrange(population.num_tenants)
    return TenantAssignment(tenant_id=tenant_id)


def distinct_tenants(assignments: Iterable[TenantAssignment]) -> int:
    """Report the count of distinct tenants exercised across ``assignments`` (Req 7.3).

    A pure fold over the tenant ids on the assignments — it reads nothing but what the draws
    produced, so the reported count never exceeds the population and never exceeds what the realized
    draws admitted.
    """
    tenants: set[int] = set()
    for assignment in assignments:
        tenants.add(assignment.tenant_id)
    return len(tenants)


def distinct_counts(assignments: Iterable[TenantAssignment]) -> int:
    """Alias of ``distinct_tenants`` mirroring ``keys.distinct_counts`` (Req 7.3).

    The multi-key sibling exposes a ``distinct_counts`` helper the driver/report calls to surface
    the distinct identities a run exercised; this single-dimension stratum offers the same name so
    the composer can treat the strata uniformly, returning the one distinct-tenant count.
    """
    return distinct_tenants(assignments)
