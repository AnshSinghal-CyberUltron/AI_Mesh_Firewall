"""Request-plan composer for the load harness (B6 / R2-21).

Layers all **enabled** GW20b strata onto one open-loop :class:`~load_harness.arrivals.Arrival` to
produce a :class:`PlannedRequest` — the complete, immutable description of one request the driver
will dispatch at its scheduled time. This is the join point of the five pure strata generators
(``strata/keys.py``, ``strata/tenants.py``, ``strata/prompts.py``, ``strata/retry.py``,
``strata/secret_output.py``), the arrival schedule (``arrivals.py``), and the run spec
(``config.py``). It owns no new value semantics of its own beyond the composition — each layered
field is the strata module's own frozen value type.

Enabled vs disabled strata (Req 13). A :class:`PlannedRequest` carries, for each of the five
strata, EITHER the stratum's assignment/pick/plan value (when that stratum is enabled in the
``RunSpec``'s :class:`~load_harness.config.StrataConfig`) OR ``None`` — a documented neutral default
meaning "this dimension does not shape this request". The retry stratum layers a drawn
``client_is_retrying: bool | None`` decision (``None`` when disabled); the shared
:class:`~load_harness.strata.retry.RetryPolicy` itself is a per-run constant, not a per-request
field.

CRITICAL — the determinism spine + the fixed draw order (Req 13.1, 13.2). Layering strata onto
arrivals must be deterministic: the same seed must reproduce byte-identical ``PlannedRequest``
assignments. Because every stratum draws from the SAME injected ``random.Random``, the ORDER in
which strata consume draws is part of the contract — reordering two enabled strata would reshuffle
every subsequent draw. :func:`plan_one` therefore consumes strata draws in this FIXED, documented
order for every arrival, and a disabled stratum consumes ZERO draws (so toggling an unrelated
stratum never perturbs another's sequence — enabling multi-key does not move the prompt draw,
because multi-key is drawn first; but note that the per-request sequence is only stable for a FIXED
``StrataConfig``, which is the determinism contract: same seed AND same config ⇒ identical plans):

    1. keys        — ``strata.keys.assign(key_population, rng)``        (if ``strata.multi_key``)
    2. tenants     — ``strata.tenants.assign(tenant_population, rng)``  (if ``strata.tenant_scale``)
    3. prompt      — ``strata.prompts.pick(corpus, share, rng)``        (if ``long_prompt``)
    4. retry       — ``strata.retry.client_retries(policy, rng)``       (if ``retry``)
    5. secret      — ``strata.secret_output.plan(share, rng)``          (if ``secret_output``)

This order is the single authority; the property test for Property 10 (task 4.10) pins it, and
``plan_requests`` applies it per arrival over a shared ``rng`` so the whole run is one reproducible
draw sequence.

Populations are passed in, not rebuilt here (DRY, Req 3/7 floors). The key and tenant populations
have HARD builders with GW20b cardinality floors (``keys.build_key_population`` ≥ 2k/≥ 200,
``tenants.build_tenant_population`` ≥ 10k) that raise on an out-of-range size — a size the
``StrataConfig`` does not carry (it only holds enable flags + shares). The composer therefore
accepts a pre-built :class:`Populations` bundle (populations, prompt corpus, retry policy) rather
than re-deriving floors here; a disabled stratum's bundle member may be ``None``. The ``rng`` is
built from ``run_spec.seed`` by the caller (``driver.py`` / ``__main__.py``) and injected, never
module-global ``random.*``.

Offline + pure (Req 13.3). :func:`plan_one` and :func:`plan_requests` touch no network, no server,
no wall-clock — they are pure functions of ``(run_spec, arrival/schedule, rng, populations)`` — so
a full set of planned requests is generated and verified offline. This module holds no
capacity-position literal and no module-level mutable; :class:`PlannedRequest` and
:class:`Populations` are frozen + slots (Req 14.2, 14.3).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from load_harness.arrivals import Arrival, ArrivalSchedule
from load_harness.config import RunSpec
from load_harness.strata import keys as keys_stratum
from load_harness.strata import prompts as prompts_stratum
from load_harness.strata import retry as retry_stratum
from load_harness.strata import secret_output as secret_output_stratum
from load_harness.strata import tenants as tenants_stratum

__all__ = (
    "Populations",
    "PlannedRequest",
    "plan_one",
    "plan_requests",
)


@dataclass(frozen=True, slots=True)
class Populations:
    """The pre-built per-run strata populations/corpus/policy the composer layers (frozen + slots).

    Bundles the inputs that cannot live on ``StrataConfig`` because they carry GW20b cardinality
    floors enforced by hard builders (``keys.build_key_population`` ≥ 2k keys / ≥ 200 orgs,
    ``tenants.build_tenant_population`` ≥ 10k tenants) or are richer-than-a-share corpus/policy
    values. A member is ``None`` when its stratum is disabled; the composer reads a member ONLY when
    the corresponding ``StrataConfig`` flag is set, so a ``None`` for an enabled stratum is a
    caller error (surfaced by :func:`plan_one` as a ``ValueError`` rather than a silent neutral
    plan).

    ``key_population`` / ``tenant_population`` are the validated identity populations;
    ``prompt_corpus`` is the long/short size policy (default ``prompts.default_corpus()``);
    ``retry_policy`` is the shared base + aggressive retry model. Frozen + slots (Req 14.3).
    """

    key_population: keys_stratum.KeyPopulation | None
    tenant_population: tenants_stratum.TenantPopulation | None
    prompt_corpus: prompts_stratum.PromptCorpus | None
    retry_policy: retry_stratum.RetryPolicy | None


@dataclass(frozen=True, slots=True)
class PlannedRequest:
    """One open-loop arrival with every ENABLED stratum layered onto it (frozen + slots, Req 13).

    ``arrival`` is the open-loop schedule entry (its ``index`` + absolute ``scheduled_s``) the
    driver dispatches at. The five stratum fields each carry the stratum's own frozen value type
    when that stratum is enabled in the run's ``StrataConfig``, or ``None`` (the documented neutral
    default meaning "this dimension does not shape this request") when disabled:

    - ``key_assignment`` — the drawn key + its org (multi-key stratum, Req 3), or ``None``.
    - ``tenant_assignment`` — the drawn tenant (tenant-scale stratum, Req 7), or ``None``.
    - ``prompt_pick`` — the long/short prompt draw + body (long-prompt stratum, Req 4), or ``None``.
    - ``client_is_retrying`` — whether this request's modeled client is a retrying client (SDK-retry
      stratum, Req 5), or ``None``. The shared ``RetryPolicy`` and the ``x-should-retry: false``
      re-issue decision live in ``strata.retry``; this field is only the drawn per-client decision,
      which the driver combines with the observed shed response via ``retry.should_reissue``.
    - ``secret_output_plan`` — the secret-shaped-output decision + scripted template (secret-shaped
      stratum, Req 6), or ``None``.

    Byte-reproducible given the seed + the fixed strata draw order (module docstring); frozen +
    slots (Req 14.3) so a planned run is an immutable value verified offline (Req 13.3).
    """

    arrival: Arrival
    key_assignment: keys_stratum.KeyAssignment | None
    tenant_assignment: tenants_stratum.TenantAssignment | None
    prompt_pick: prompts_stratum.PromptPick | None
    client_is_retrying: bool | None
    secret_output_plan: secret_output_stratum.SecretOutputPlan | None


def _require(member: object, *, stratum: str) -> None:
    """Raise if an enabled stratum's population/corpus/policy bundle member is missing.

    An enabled stratum with a ``None`` bundle member is a caller wiring error (the driver/entrypoint
    must build the population before composing); fail loud rather than emit a silently-neutral plan
    that would under-count the stratum's realized share.
    """
    if member is None:
        raise ValueError(
            f"stratum {stratum!r} is enabled in the RunSpec but its Populations member is None; "
            f"build the population before composing requests"
        )


def plan_one(
    run_spec: RunSpec,
    arrival: Arrival,
    rng: random.Random,
    *,
    populations: Populations,
) -> PlannedRequest:
    """Compose one :class:`PlannedRequest` by layering every enabled stratum onto ``arrival``.

    Pure in ``(run_spec, arrival, rng, populations)`` (Req 13.3). Each enabled stratum consumes its
    draw(s) from the shared injected ``rng`` in the FIXED order documented at module scope —
    keys → tenants → prompt → retry → secret — and a disabled stratum consumes ZERO draws and
    contributes ``None``. The same seed + same ``StrataConfig`` therefore reproduces a
    byte-identical plan (Req 13.1, 13.2).
    """
    strata = run_spec.strata

    key_assignment: keys_stratum.KeyAssignment | None = None
    if strata.multi_key:
        _require(populations.key_population, stratum="multi_key")
        assert populations.key_population is not None  # narrowed by _require
        key_assignment = keys_stratum.assign(populations.key_population, rng)

    tenant_assignment: tenants_stratum.TenantAssignment | None = None
    if strata.tenant_scale:
        _require(populations.tenant_population, stratum="tenant_scale")
        assert populations.tenant_population is not None  # narrowed by _require
        tenant_assignment = tenants_stratum.assign(populations.tenant_population, rng)

    prompt_pick: prompts_stratum.PromptPick | None = None
    if strata.long_prompt:
        _require(populations.prompt_corpus, stratum="long_prompt")
        assert populations.prompt_corpus is not None  # narrowed by _require
        prompt_pick = prompts_stratum.pick(
            populations.prompt_corpus, strata.long_prompt_share, rng
        )

    client_is_retrying: bool | None = None
    if strata.retry:
        _require(populations.retry_policy, stratum="retry")
        assert populations.retry_policy is not None  # narrowed by _require
        client_is_retrying = retry_stratum.client_retries(populations.retry_policy, rng)

    secret_output_plan: secret_output_stratum.SecretOutputPlan | None = None
    if strata.secret_output:
        secret_output_plan = secret_output_stratum.plan(strata.secret_output_share, rng)

    return PlannedRequest(
        arrival=arrival,
        key_assignment=key_assignment,
        tenant_assignment=tenant_assignment,
        prompt_pick=prompt_pick,
        client_is_retrying=client_is_retrying,
        secret_output_plan=secret_output_plan,
    )


def plan_requests(
    run_spec: RunSpec,
    schedule: ArrivalSchedule,
    rng: random.Random,
    *,
    populations: Populations,
) -> tuple[PlannedRequest, ...]:
    """Compose the full tuple of :class:`PlannedRequest` for ``schedule`` (Req 13.1, 13.2, 13.3).

    Applies :func:`plan_one` to each arrival in ``schedule.arrivals`` IN ORDER, threading the one
    shared injected ``rng`` so the whole run is a single reproducible draw sequence: the arrivals
    are consumed ascending by ``index`` and, within each arrival, the strata consume draws in the
    fixed documented order. Pure — no network, no server, no wall-clock — so a planned run is
    generated and verified offline (Req 13.3). An empty schedule yields an empty tuple.
    """
    return tuple(
        plan_one(run_spec, arrival, rng, populations=populations)
        for arrival in schedule.arrivals
    )
