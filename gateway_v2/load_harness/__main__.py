"""`python -m load_harness` — the load-harness entrypoint (B6 / R2-21).

Mirrors ``state_control``/``audit_control`` ``__main__`` in shape: a thin process entry whose body
is a ``main()`` returning an int exit code, with ``raise SystemExit(main())`` under
``__main__``. It parses a :class:`~load_harness.config.RunSpec` from args/env via
:func:`load_harness.config.load_run_spec`, emits the warn-and-default log lines, constructs the
injected clock + the seeded ``random.Random(run_spec.seed)`` (Req 13.1), and builds the OFFLINE,
deterministic half of a run — the arrival :class:`~load_harness.arrivals.ArrivalSchedule` and the
tuple of :class:`~load_harness.request_plan.PlannedRequest` — then prints a one-line-per-section
summary.

Why only the offline half here (the 10.1 seam). The actual gateway-driving orchestration — booting
/ attaching a target, running the Poisson rate ladder end to end, collecting C4, and asserting the
realized distributions — is ``dryrun.py`` (task 10.1), which is NOT built yet. This entrypoint is
deliberately MINIMAL and robust: it owns the pure, server-free generation (the part Req 13.1 pins —
same seed + same clock ⇒ byte-identical schedule + strata) and then hands off to a single clean
seam, :func:`_invoke_dry_run`, which LAZILY imports ``load_harness.dryrun`` and calls it if present.
Until 10.1 lands, the import is absent, so the entrypoint reports the offline summary and the
"dry-run orchestration is task 10.1" status and exits 0 — it never duplicates dry-run logic and
never requires a running gateway to succeed. When 10.1 ships ``dryrun.run_dry_run``, this entrypoint
wires it with no other change.

Operational inputs read here, not on ``RunSpec`` (minimal, documented). ``RunSpec`` carries the
offered rate, arrival mode, seed, enabled strata + shares, tolerances, and the ladder policy — it
does NOT carry the arrival COUNT (how many requests to generate) nor the key/tenant POPULATION sizes
(``StrataConfig`` holds only enable flags + shares, and the population floors live on the hard
stratum builders). Those two operational inputs are read here from the environment with documented
defaults (``LH_NUM_REQUESTS`` → :data:`_DEFAULT_NUM_REQUESTS`; ``LH_NUM_KEYS`` / ``LH_NUM_ORGS`` /
``LH_NUM_TENANTS`` → the respective stratum population floors), warn-and-default exactly like
``config.load_run_spec`` so a bad value never raises. None is a capacity-position literal — the
count is an offered-generation size and the population sizes are stratum-population policy (the same
class as ``keys._MIN_KEYS`` / ``tenants._MIN_TENANTS``), not a host CPU/RAM/FD/RPS budget. The live
``random.Random`` is built from ``run_spec.seed`` here and injected downward; module-global
``random.*`` is never used. No module-level mutable; honest typing throughout (no ``Any`` leak).
"""

from __future__ import annotations

import importlib
import logging
import os
import random
import time
from collections.abc import Mapping
from typing import Protocol

from load_harness.arrivals import ArrivalSchedule, Clock, build_schedule
from load_harness.config import RunSpec, load_run_spec
from load_harness.request_plan import PlannedRequest, Populations, plan_requests
from load_harness.strata import keys as keys_stratum
from load_harness.strata import prompts as prompts_stratum
from load_harness.strata import retry as retry_stratum
from load_harness.strata import tenants as tenants_stratum

LOG = logging.getLogger("amf.load_harness")

#: Default number of open-loop arrivals to generate for one offered run when ``LH_NUM_REQUESTS`` is
#: unset. An offered-generation SIZE of the measurement procedure (how large an offline sample to
#: draw), not a host capacity position — the ladder, not this count, discovers the server's ceiling.
_DEFAULT_NUM_REQUESTS = 1_000

_ENV_NUM_REQUESTS = "LH_NUM_REQUESTS"
_ENV_NUM_KEYS = "LH_NUM_KEYS"
_ENV_NUM_ORGS = "LH_NUM_ORGS"
_ENV_NUM_TENANTS = "LH_NUM_TENANTS"

# Default multi-key / tenant population sizes when their stratum is enabled but the size env is
# unset: the GW20b stratum-population FLOORS the hard builders enforce (``keys`` ≥ 2k keys /
# ≥ 200 orgs, ``tenants`` ≥ 10k tenants). These are stratum-population policy — the realistic
# identity cardinality the OFFERED TRAFFIC spans, the same justification class as `keys._MIN_KEYS`
# and `tenants._MIN_TENANTS` — not host CPU/RAM/FD/RPS capacity positions, so they carry no
# capacity-position name the `check_capacity_literals` gate scans for. They match the builders'
# floors so the default always validates; a larger value may be set via the env above.
_DEFAULT_NUM_KEYS = 2_000
_DEFAULT_NUM_ORGS = 200
_DEFAULT_NUM_TENANTS = 10_000


def _env_map(env: Mapping[str, str] | None) -> Mapping[str, str]:
    """The env source — the provided mapping, else a snapshot of ``os.environ`` (pure to call)."""
    if env is not None:
        return env
    return dict(os.environ)


def _parse_positive_int(
    raw: str | None, *, key: str, default: int
) -> tuple[int, tuple[str, ...]]:
    """Parse a positive int env value, warn-and-default on missing/invalid/non-positive.

    Mirrors ``config.load_run_spec``'s idiom: an unset key uses ``default`` silently; a present but
    unparseable or non-positive value falls back to ``default`` and emits exactly one warning line
    naming the key and its value. Never raises.
    """
    if raw is None or not raw.strip():
        return default, ()
    stripped = raw.strip()
    try:
        value = int(stripped)
    except ValueError:
        return default, (f"{key}={stripped!r} is not an integer; using default {default}",)
    if value <= 0:
        return default, (f"{key}={value} is not positive; using default {default}",)
    return value, ()


def _build_key_population(
    num_keys: int, num_orgs: int
) -> tuple[keys_stratum.KeyPopulation, tuple[str, ...]]:
    """Build a key population, softening the hard builder's ``ValueError`` to a warn-and-default.

    ``config.load_run_spec`` is where operator input is softened, but the key population SIZE is not
    a ``RunSpec`` field — it is read here. The stratum builder enforces the GW20b floors/ceiling and
    raises on an out-of-range size; so the entrypoint catches that and falls back to the floor
    (``_DEFAULT_NUM_KEYS`` / ``_DEFAULT_NUM_ORGS``, which always validate) with one warning line,
    keeping ``main()`` robust (it never raises on operator misconfig).
    """
    try:
        return keys_stratum.build_key_population(num_keys, num_orgs), ()
    except ValueError as exc:
        return (
            keys_stratum.build_key_population(_DEFAULT_NUM_KEYS, _DEFAULT_NUM_ORGS),
            (
                f"multi-key population ({num_keys} keys / {num_orgs} orgs) rejected ({exc}); "
                f"using floor {_DEFAULT_NUM_KEYS} keys / {_DEFAULT_NUM_ORGS} orgs",
            ),
        )


def _build_tenant_population(
    num_tenants: int,
) -> tuple[tenants_stratum.TenantPopulation, tuple[str, ...]]:
    """Build a tenant population, softening the hard builder's ``ValueError`` to a warn-and-default.

    As with :func:`_build_key_population`: the tenant population size is read at the entrypoint, not
    on ``RunSpec``, so an out-of-range size falls back to the stratum floor
    (``_DEFAULT_NUM_TENANTS``, which always validates) with one warning rather than raising.
    """
    try:
        return tenants_stratum.build_tenant_population(num_tenants), ()
    except ValueError as exc:
        return (
            tenants_stratum.build_tenant_population(_DEFAULT_NUM_TENANTS),
            (
                f"tenant-scale population ({num_tenants} tenants) rejected ({exc}); "
                f"using floor {_DEFAULT_NUM_TENANTS}",
            ),
        )


def _build_populations(
    run_spec: RunSpec, src: Mapping[str, str]
) -> tuple[Populations, tuple[str, ...]]:
    """Build the :class:`Populations` bundle for the ENABLED strata (Req 3, 4, 5, 7).

    A bundle member is built ONLY when its stratum is enabled in ``run_spec.strata`` (a disabled
    stratum's member stays ``None``, the composer's documented neutral default). The key/tenant
    population sizes come from the environment with the stratum FLOORS as defaults; they flow
    through the hard builders (``build_key_population`` / ``build_tenant_population``), which
    enforce the GW20b cardinality floors. The prompt corpus and retry policy are per-run constants
    built
    from the run's shares. Returns the bundle plus any warn-and-default log lines.
    """
    strata = run_spec.strata
    logs: tuple[str, ...] = ()

    key_population: keys_stratum.KeyPopulation | None = None
    if strata.multi_key:
        num_keys, k_logs = _parse_positive_int(
            src.get(_ENV_NUM_KEYS), key=_ENV_NUM_KEYS, default=_DEFAULT_NUM_KEYS
        )
        num_orgs, o_logs = _parse_positive_int(
            src.get(_ENV_NUM_ORGS), key=_ENV_NUM_ORGS, default=_DEFAULT_NUM_ORGS
        )
        key_population, b_logs = _build_key_population(num_keys, num_orgs)
        logs = (*logs, *k_logs, *o_logs, *b_logs)

    tenant_population: tenants_stratum.TenantPopulation | None = None
    if strata.tenant_scale:
        num_tenants, t_logs = _parse_positive_int(
            src.get(_ENV_NUM_TENANTS),
            key=_ENV_NUM_TENANTS,
            default=_DEFAULT_NUM_TENANTS,
        )
        tenant_population, b_logs = _build_tenant_population(num_tenants)
        logs = (*logs, *t_logs, *b_logs)

    prompt_corpus: prompts_stratum.PromptCorpus | None = None
    if strata.long_prompt:
        prompt_corpus = prompts_stratum.default_corpus()

    retry_policy: retry_stratum.RetryPolicy | None = None
    if strata.retry:
        retry_policy = retry_stratum.RetryPolicy(
            base_share=strata.retry_share,
            aggressive_share=strata.aggressive_retry_share,
        )

    populations = Populations(
        key_population=key_population,
        tenant_population=tenant_population,
        prompt_corpus=prompt_corpus,
        retry_policy=retry_policy,
    )
    return populations, logs


def _generate_offline(
    run_spec: RunSpec, src: Mapping[str, str], *, clock: Clock
) -> tuple[ArrivalSchedule, tuple[PlannedRequest, ...]]:
    """Build the OFFLINE, deterministic half of a run — schedule + planned requests (Req 13.1).

    Constructs the one seeded ``random.Random(run_spec.seed)`` and threads it through
    ``build_schedule`` then ``plan_requests`` so the whole generation is a single reproducible draw
    sequence under the injected ``clock`` — same seed + same clock ⇒ byte-identical result. Pure of
    any network or server. The arrival count is read from ``LH_NUM_REQUESTS`` (warn-and-default).
    """
    num_requests, count_logs = _parse_positive_int(
        src.get(_ENV_NUM_REQUESTS), key=_ENV_NUM_REQUESTS, default=_DEFAULT_NUM_REQUESTS
    )
    for line in count_logs:
        LOG.warning("%s", line)
    rng = random.Random(run_spec.seed)
    schedule = build_schedule(
        mode=run_spec.arrival_mode,
        offered_rate_per_s=run_spec.offered_rate_per_s,
        count=num_requests,
        rng=rng,
        clock=clock,
    )
    populations, pop_logs = _build_populations(run_spec, src)
    for line in pop_logs:
        LOG.warning("%s", line)
    planned = plan_requests(run_spec, schedule, rng, populations=populations)
    return schedule, planned


def _summarize(
    run_spec: RunSpec, schedule: ArrivalSchedule, planned: tuple[PlannedRequest, ...]
) -> None:
    """Log a one-line-per-section summary of the offline generation (no fabricated measurement)."""
    LOG.info(
        "run spec: offered_rate=%.3f/s mode=%s seed=%d",
        run_spec.offered_rate_per_s,
        run_spec.arrival_mode.value,
        run_spec.seed,
    )
    span_s = schedule.arrivals[-1].scheduled_s if schedule.arrivals else 0.0
    LOG.info(
        "arrival schedule: %d arrivals, last scheduled_s=%.6f",
        len(schedule.arrivals),
        span_s,
    )
    LOG.info("planned requests: %d (offline, deterministic under seed)", len(planned))


class _DryRunEntry(Protocol):
    """The structural shape task 10.1's ``dryrun.run_dry_run`` must satisfy to be invoked here.

    Declared as a ``Protocol`` so :func:`_invoke_dry_run` can type the lazily-resolved callable
    honestly (no ``Any`` leak) without a hard import of a module that does not exist yet: it takes
    the validated ``RunSpec`` + the planned requests and returns an int exit code. When 10.1 ships a
    ``run_dry_run`` matching this shape, it drops in; the entrypoint is unchanged.
    """

    def __call__(
        self, run_spec: RunSpec, planned: tuple[PlannedRequest, ...]
    ) -> int: ...


def _invoke_dry_run(
    run_spec: RunSpec, planned: tuple[PlannedRequest, ...]
) -> int | None:
    """The seam task 10.1 (``dryrun.py``) wires: drive the booted gateway + ladder, return a code.

    Lazily resolves ``load_harness.dryrun`` via :func:`importlib.import_module` so this entrypoint
    neither depends on it at import time nor duplicates its logic. Until 10.1 ships, the module is
    absent, so this returns ``None`` and the caller treats the offline summary as the whole run.
    When 10.1 lands a ``run_dry_run`` matching :class:`_DryRunEntry`, it is discovered and invoked
    here with no other change to the entrypoint.
    """
    try:
        module = importlib.import_module("load_harness.dryrun")
    except ImportError:
        LOG.info(
            "dry-run orchestration (dryrun.py) is task 10.1 and not yet available; "
            "reporting offline generation only"
        )
        return None
    entry = getattr(module, "run_dry_run", None)
    if not callable(entry):
        LOG.info("load_harness.dryrun present but exposes no run_dry_run; offline summary only")
        return None
    run_dry_run: _DryRunEntry = entry
    return int(run_dry_run(run_spec, planned))


def main(env: Mapping[str, str] | None = None, *, clock: Clock | None = None) -> int:
    """Process entry point for ``python -m load_harness``. Returns the exit code.

    Loads + validates a ``RunSpec`` from ``env`` (defaults to ``os.environ``), emits every
    warn-and-default log line, generates the offline deterministic half of the run under the
    injected ``clock`` (defaulting to ``time.monotonic``) and the seeded
    ``random.Random(run_spec.seed)``, prints a summary, and hands off to the dry-run seam
    (task 10.1). When the dry-run is unavailable the entrypoint has still done useful, verifiable
    work (the offline generation Req 13.1 pins) and exits 0.
    """
    logging.basicConfig(
        level=(os.environ.get("AMF_LOG_LEVEL") or "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    src = _env_map(env)
    run_spec, spec_logs = load_run_spec(src)
    for line in spec_logs:
        LOG.warning("%s", line)
    schedule, planned = _generate_offline(
        run_spec, src, clock=time.monotonic if clock is None else clock
    )
    _summarize(run_spec, schedule, planned)
    dry_run_code = _invoke_dry_run(run_spec, planned)
    return 0 if dry_run_code is None else dry_run_code


if __name__ == "__main__":
    raise SystemExit(main())
