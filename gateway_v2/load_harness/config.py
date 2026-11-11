"""RunSpec config + named operational constants for the load harness (B6 / R2-21).

Parses and validates a run spec — offered rate, arrival mode, enabled strata + their configured
shares, measurement tolerances, and the injected `random.Random` **seed** — from args/env, using
the same env to dataclass+logs **warn-and-default** idiom as
``gateway_v2/gateway_v2/runtime/serving_config.py`` (and ``runtime/resources.py::load_contract``):
parse, validate, and return a frozen value together with a tuple of warning log lines. An unset key
uses the documented default with no warning; a present-but-unparseable or out-of-range value falls
back to that default and emits exactly one warning line naming the rejected key and its value. The
function never raises and never leaves a field unset.

Capacity-literal posture (Req 14.2). This module holds **no** capacity-position literal. The run's
pass bound (the SLO) is **not** a field here — it is read later from
``gateway_v2.runtime.resources.ResourceContract.target_p99_ms`` (the one capacity authority), and
the lost-segment tail comes from ``gateway_v2.runtime.latency_slo`` — so ``resources`` is
deliberately **not** imported here. The harness's own knobs below
(``_SCHEDULE_DROP_TOLERANCE_S``, ``_MAX_LADDER_STEP_FRACTION``, ``_MIN_HEADROOM_MARGIN``,
``_LADDER_REPEAT_COUNT``, ``_MAX_INFRA_ERROR_SHARE``, and the strata tolerances) are **named
operational / statistical constants with a documented rationale** — the same category as
``latency_slo._LOST_SEGMENT_TAIL_MS`` and ``serving_config.py``'s port/drain defaults. None is
derived from CPU / RAM / FD / RPS, so none is a capacity-position literal (the
``check_capacity_literals`` gate).

Seed, not a live Random (Req 13.1, 14.3). ``RunSpec`` stores the seed as an ``int`` so the value
type stays frozen + slots and byte-reproducible; the live ``random.Random`` is constructed from
that seed at the generation boundary (``arrivals.py`` / ``request_plan.py`` / ``driver.py``), never
module-global ``random.*``.

ArrivalMode placement decision. ``ArrivalMode`` (POISSON | CONSTANT) is defined **here** in
``config.py`` rather than in ``arrivals.py``: ``RunSpec`` must reference it, and keeping the one
authoritative definition on the config value type (which ``arrivals.py`` will later import) is the
DRY choice — a single enum, no circular edge (``config`` imports nothing from ``arrivals``).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

__all__ = (
    "ArrivalMode",
    "LadderConfig",
    "RunSpec",
    "StrataConfig",
    "Tolerances",
    "default_ladder_config",
    "default_tolerances",
    "load_run_spec",
)


# --------------------------------------------------------------------------------------------------
# Named operational / statistical constants (NOT capacity-position literals).
#
# Each is an operational or statistical knob of the measurement instrument, not a value derived from
# the host's CPU / RAM / FD / RPS budget. They are allowed outside `runtime/resources.py` for the
# same reason `latency_slo._LOST_SEGMENT_TAIL_MS` and `serving_config.py`'s port/drain defaults are:
# `lint/check_capacity_literals.py` keys only on capacity-position names (max_connections, workers,
# queue_depth, pool_size, ...) and capacity-position calls (Semaphore, Queue, ...); none of these is
# such a name. The SLO pass bound is deliberately NOT one of these — it is read from
# `ResourceContract.target_p99_ms` at the ladder/report boundary, never declared here.
# --------------------------------------------------------------------------------------------------

_SCHEDULE_DROP_TOLERANCE_S = 0.001
"""Max slip (seconds) of an actual dispatch past its scheduled start before it is a Schedule_Drop.

OPERATIONAL constant (Req 1.3). A dispatch whose actual start exceeds its ``scheduled_s`` by more
than this tolerance is recorded as a ``Schedule_Drop`` — evidence the *generator*, not the server,
fell behind, which fails the run's pass test (Req 10.3) and flags it as not a clean server
measurement (Req 2.4). 1 ms is a scheduling-slack figure (loop-wakeup / dispatch jitter), far below
any serving-path latency the harness measures; it is NOT derived from CPU / RAM / FD, so it is not a
capacity position.
"""

_MAX_LADDER_STEP_FRACTION = 0.10
"""Maximum fractional increase of offered rate between adjacent rate-ladder steps (≤ 0.10).

OPERATIONAL constant (Req 10.1). The ladder climbs offered rate in steps of at most this fraction
so the reported knee is resolved finely enough to be repeatable (GW20b: "a ladder in steps of
≤ 10%"). A dimensionless step-size policy of the measurement procedure, not a capacity position.
"""

_MIN_HEADROOM_MARGIN = 0.20
"""Minimum Loadgen_Headroom margin below which a run is marked generator-bound and excluded.

STATISTICAL / OPERATIONAL constant (Req 2.2, gate LGW20-5). ``Loadgen_Headroom`` is the margin
between the rate the generator *can* issue and the configured offered rate; when it falls below this
margin the generator may be the bottleneck, so the run is excluded from knee determination rather
than counted as a server pass/fail. A dimensionless self-proof threshold of the instrument, not a
capacity position.
"""

_LADDER_REPEAT_COUNT = 3
"""Number of repeats run at a candidate ladder pass rate (GW20b 3/3 repeatability).

OPERATIONAL constant (Req 10.2, 10.4, 10.5). GW20b signs a knee only from 3/3 repeats, so the
ladder runs each candidate pass rate this many times; ``Highest_Repeatable_Pass`` is the greatest
rate passing all three and ``First_Repeatable_Fail`` the lowest failing all three. A repeat-count
policy of the measurement procedure, not a capacity position.
"""

_MAX_INFRA_ERROR_SHARE = 0.001
"""Maximum infrastructure-error share a single run may carry and still be eligible to pass.

STATISTICAL constant (Req 10.3). A run passes only when its C4 p99 is below the SLO bound, its
infra-error share is within this low bound, and its ``Schedule_Drop`` count is zero. This bounds
*harness/transport* errors (never the server's shed reasons, which are admitted outcomes counted in
the Reason_Mix) so a measurement polluted by client-side infra noise is not read as a server pass.
A dimensionless error-tolerance of the instrument, not a capacity position.
"""

_SHARE_CONVERGENCE_TOLERANCE = 0.02
"""Absolute tolerance for a realized strata share vs its configured share over a run.

STATISTICAL constant (Req 4.2, 5.3, 6.2, 12.4). A finite sampled run realizes a configured share
(long-prompt 1/5/25%, retry, secret-shaped) only approximately; a realized share within this
absolute tolerance of its configured value is accepted as converged. A sampling-tolerance of the
instrument, not a capacity position.
"""

_ARRIVAL_MEAN_TOLERANCE = 0.05
"""Relative tolerance for the realized Poisson mean inter-arrival vs ``1 / offered_rate``.

STATISTICAL constant (Req 1.2, 12.3). A finite Poisson draw realizes an exponential inter-arrival
mean only approximately; a realized mean within this relative tolerance of ``1 / offered_rate`` is
accepted as matching the configured process. A sampling-tolerance of the instrument, not a capacity
position.
"""


# Offered-rate validation bounds. Operational guard rails on the *configured input*, not capacity
# positions: they only reject a nonsensical offered rate (≤ 0 or absurdly large) and fall back to a
# documented default; the real capacity ceiling is discovered by the ladder against the server.
_MIN_OFFERED_RATE_PER_S = 0.0
_MAX_OFFERED_RATE_PER_S = 1_000_000.0
_DEFAULT_OFFERED_RATE_PER_S = 100.0

_DEFAULT_SEED = 0

_DEFAULT_LONG_PROMPT_SHARE = 0.0
_DEFAULT_RETRY_SHARE = 0.0
_DEFAULT_AGGRESSIVE_RETRY_SHARE = 0.0
_DEFAULT_SECRET_OUTPUT_SHARE = 0.0

# The three GW20b-sanctioned long-prompt shares (Req 4.1): 1% / 5% / 25%. A configured long-prompt
# share that is enabled but not one of these is snapped to the nearest sanctioned value with a
# warning. A statistical/operational policy of the strata, not a capacity position.
_LONG_PROMPT_SHARES = (0.01, 0.05, 0.25)

_ENV_OFFERED_RATE = "LH_OFFERED_RATE_PER_S"
_ENV_ARRIVAL_MODE = "LH_ARRIVAL_MODE"
_ENV_SEED = "LH_SEED"
_ENV_MULTI_KEY = "LH_STRATUM_MULTI_KEY"
_ENV_LONG_PROMPT = "LH_STRATUM_LONG_PROMPT"
_ENV_LONG_PROMPT_SHARE = "LH_LONG_PROMPT_SHARE"
_ENV_RETRY = "LH_STRATUM_RETRY"
_ENV_RETRY_SHARE = "LH_RETRY_SHARE"
_ENV_AGGRESSIVE_RETRY_SHARE = "LH_AGGRESSIVE_RETRY_SHARE"
_ENV_SECRET_OUTPUT = "LH_STRATUM_SECRET_OUTPUT"
_ENV_SECRET_OUTPUT_SHARE = "LH_SECRET_OUTPUT_SHARE"
_ENV_TENANT_SCALE = "LH_STRATUM_TENANT_SCALE"

_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off"})


class ArrivalMode(Enum):
    """The arrival process the harness offers load under.

    ``POISSON`` draws inter-arrival times from an exponential distribution (the core capacity-sizing
    mode, R2-01 / Req 1.2); ``CONSTANT`` uses fixed inter-arrival times and its published limits are
    labeled upper bounds in the run report (Req 1.6). Defined here (not in ``arrivals.py``) so the
    one authoritative definition lives on the ``RunSpec`` value type that references it;
    ``arrivals`` imports it from here.
    """

    POISSON = "poisson"
    CONSTANT = "constant"


@dataclass(frozen=True, slots=True)
class Tolerances:
    """The measurement tolerances a run is judged against (all dimensionless / seconds).

    ``schedule_drop_s`` is the Schedule_Drop slip bound (seconds); ``share_convergence`` is the
    absolute strata-share tolerance; ``arrival_mean_relative`` is the relative Poisson-mean
    tolerance; ``min_headroom_margin`` is the generator-bound exclusion threshold;
    ``max_infra_error_share`` is the per-run infra-error ceiling. These default to the named
    operational constants above and are never SLO/capacity values.
    """

    schedule_drop_s: float
    share_convergence: float
    arrival_mean_relative: float
    min_headroom_margin: float
    max_infra_error_share: float


@dataclass(frozen=True, slots=True)
class StrataConfig:
    """Which of the five GW20b strata are enabled and their configured shares (Req 3–7).

    ``multi_key`` / ``tenant_scale`` are population-identity strata (enabled flags; their
    population sizes/distributions are owned by the respective strata modules). ``long_prompt``
    mixes 4–7k-token prompts at ``long_prompt_share`` (one of 1/5/25% when enabled). ``retry``
    models SDK retry at
    ``retry_share`` plus an ``aggressive_retry_share``. ``secret_output`` drives the GW12b holdback
    path at ``secret_output_share``. A disabled stratum carries a 0.0 share.
    """

    multi_key: bool
    long_prompt: bool
    long_prompt_share: float
    retry: bool
    retry_share: float
    aggressive_retry_share: float
    secret_output: bool
    secret_output_share: float
    tenant_scale: bool


@dataclass(frozen=True, slots=True)
class LadderConfig:
    """The rate-ladder climb policy (Req 10) — all operational, no capacity literal.

    ``max_step_fraction`` is the ≤ 0.10 per-step offered-rate increase; ``repeat_count`` is the
    GW20b 3/3 repeat count at a candidate pass rate. The pass bound (SLO) is NOT here — it is read
    from ``ResourceContract.target_p99_ms`` at ladder-evaluation time.
    """

    max_step_fraction: float
    repeat_count: int


@dataclass(frozen=True, slots=True)
class RunSpec:
    """A fully-validated harness run specification (frozen + slots, Req 14.3).

    ``offered_rate_per_s`` is the configured offered rate; ``arrival_mode`` is POISSON | CONSTANT;
    ``seed`` is the ``random.Random`` **seed int** (a live ``Random`` is built from it at the
    generation boundary — Req 13.1); ``strata``, ``tolerances``, and ``ladder`` are the composed
    sub-configs. Carries no SLO/capacity-position literal.
    """

    offered_rate_per_s: float
    arrival_mode: ArrivalMode
    seed: int
    strata: StrataConfig
    tolerances: Tolerances
    ladder: LadderConfig


def default_tolerances() -> Tolerances:
    """The default measurement tolerances, from the named operational constants above."""
    return Tolerances(
        schedule_drop_s=_SCHEDULE_DROP_TOLERANCE_S,
        share_convergence=_SHARE_CONVERGENCE_TOLERANCE,
        arrival_mean_relative=_ARRIVAL_MEAN_TOLERANCE,
        min_headroom_margin=_MIN_HEADROOM_MARGIN,
        max_infra_error_share=_MAX_INFRA_ERROR_SHARE,
    )


def default_ladder_config() -> LadderConfig:
    """The default rate-ladder climb policy, from the named operational constants above."""
    return LadderConfig(
        max_step_fraction=_MAX_LADDER_STEP_FRACTION,
        repeat_count=_LADDER_REPEAT_COUNT,
    )


def _env_map(env: Mapping[str, str] | None) -> Mapping[str, str]:
    if env is not None:
        return env
    return dict(os.environ)


def _parse_bool(raw: str | None, *, key: str, default: bool) -> tuple[bool, tuple[str, ...]]:
    if raw is None or not raw.strip():
        return default, ()
    token = raw.strip().lower()
    if token in _TRUE_TOKENS:
        return True, ()
    if token in _FALSE_TOKENS:
        return False, ()
    return default, (
        f"{key}={raw.strip()!r} is not a boolean; using default {default}",
    )


def _parse_offered_rate(raw: str | None) -> tuple[float, tuple[str, ...]]:
    if raw is None or not raw.strip():
        return _DEFAULT_OFFERED_RATE_PER_S, ()
    stripped = raw.strip()
    try:
        value = float(stripped)
    except ValueError:
        return _DEFAULT_OFFERED_RATE_PER_S, (
            f"{_ENV_OFFERED_RATE}={stripped!r} is not a number; "
            f"using default {_DEFAULT_OFFERED_RATE_PER_S}",
        )
    if value <= _MIN_OFFERED_RATE_PER_S or value > _MAX_OFFERED_RATE_PER_S:
        return _DEFAULT_OFFERED_RATE_PER_S, (
            f"{_ENV_OFFERED_RATE}={value} outside "
            f"({_MIN_OFFERED_RATE_PER_S}, {_MAX_OFFERED_RATE_PER_S}]; "
            f"using default {_DEFAULT_OFFERED_RATE_PER_S}",
        )
    return value, ()


def _parse_arrival_mode(raw: str | None) -> tuple[ArrivalMode, tuple[str, ...]]:
    if raw is None or not raw.strip():
        return ArrivalMode.POISSON, ()
    token = raw.strip().lower()
    for mode in ArrivalMode:
        if mode.value == token:
            return mode, ()
    return ArrivalMode.POISSON, (
        f"{_ENV_ARRIVAL_MODE}={raw.strip()!r} is not a known arrival mode; "
        f"using default {ArrivalMode.POISSON.value}",
    )


def _parse_seed(raw: str | None) -> tuple[int, tuple[str, ...]]:
    if raw is None or not raw.strip():
        return _DEFAULT_SEED, ()
    stripped = raw.strip()
    try:
        return int(stripped), ()
    except ValueError:
        return _DEFAULT_SEED, (
            f"{_ENV_SEED}={stripped!r} is not an integer; using default {_DEFAULT_SEED}",
        )


def _parse_share(
    raw: str | None, *, key: str, default: float
) -> tuple[float, tuple[str, ...]]:
    """Parse a share in the closed interval [0.0, 1.0]; warn-and-default otherwise."""
    if raw is None or not raw.strip():
        return default, ()
    stripped = raw.strip()
    try:
        value = float(stripped)
    except ValueError:
        return default, (
            f"{key}={stripped!r} is not a number; using default {default}",
        )
    if value < 0.0 or value > 1.0:
        return default, (
            f"{key}={value} outside [0.0, 1.0]; using default {default}",
        )
    return value, ()


def _snap_long_prompt_share(
    share: float, *, enabled: bool
) -> tuple[float, tuple[str, ...]]:
    """Snap an enabled long-prompt share to the nearest sanctioned 1/5/25% (Req 4.1)."""
    if not enabled:
        return share, ()
    if share in _LONG_PROMPT_SHARES:
        return share, ()
    nearest = min(_LONG_PROMPT_SHARES, key=lambda s: abs(s - share))
    return nearest, (
        f"{_ENV_LONG_PROMPT_SHARE}={share} is not one of {_LONG_PROMPT_SHARES}; "
        f"snapping to nearest sanctioned share {nearest}",
    )


def _load_strata(src: Mapping[str, str]) -> tuple[StrataConfig, tuple[str, ...]]:
    multi_key, mk_logs = _parse_bool(src.get(_ENV_MULTI_KEY), key=_ENV_MULTI_KEY, default=False)
    long_prompt, lp_logs = _parse_bool(
        src.get(_ENV_LONG_PROMPT), key=_ENV_LONG_PROMPT, default=False
    )
    long_prompt_share, lps_logs = _parse_share(
        src.get(_ENV_LONG_PROMPT_SHARE),
        key=_ENV_LONG_PROMPT_SHARE,
        default=_DEFAULT_LONG_PROMPT_SHARE,
    )
    long_prompt_share, snap_logs = _snap_long_prompt_share(
        long_prompt_share, enabled=long_prompt
    )
    retry, rt_logs = _parse_bool(src.get(_ENV_RETRY), key=_ENV_RETRY, default=False)
    retry_share, rts_logs = _parse_share(
        src.get(_ENV_RETRY_SHARE), key=_ENV_RETRY_SHARE, default=_DEFAULT_RETRY_SHARE
    )
    aggressive_retry_share, art_logs = _parse_share(
        src.get(_ENV_AGGRESSIVE_RETRY_SHARE),
        key=_ENV_AGGRESSIVE_RETRY_SHARE,
        default=_DEFAULT_AGGRESSIVE_RETRY_SHARE,
    )
    secret_output, so_logs = _parse_bool(
        src.get(_ENV_SECRET_OUTPUT), key=_ENV_SECRET_OUTPUT, default=False
    )
    secret_output_share, sos_logs = _parse_share(
        src.get(_ENV_SECRET_OUTPUT_SHARE),
        key=_ENV_SECRET_OUTPUT_SHARE,
        default=_DEFAULT_SECRET_OUTPUT_SHARE,
    )
    tenant_scale, ts_logs = _parse_bool(
        src.get(_ENV_TENANT_SCALE), key=_ENV_TENANT_SCALE, default=False
    )
    strata = StrataConfig(
        multi_key=multi_key,
        long_prompt=long_prompt,
        long_prompt_share=long_prompt_share,
        retry=retry,
        retry_share=retry_share,
        aggressive_retry_share=aggressive_retry_share,
        secret_output=secret_output,
        secret_output_share=secret_output_share,
        tenant_scale=tenant_scale,
    )
    logs = (
        *mk_logs,
        *lp_logs,
        *lps_logs,
        *snap_logs,
        *rt_logs,
        *rts_logs,
        *art_logs,
        *so_logs,
        *sos_logs,
        *ts_logs,
    )
    return strata, logs


def load_run_spec(env: Mapping[str, str] | None = None) -> tuple[RunSpec, tuple[str, ...]]:
    """Load a validated ``RunSpec`` from ``env`` (defaults to ``os.environ``).

    Returns ``(run_spec, logs)`` exactly like ``load_serving_config`` / ``load_contract``: ``logs``
    is empty when every key is unset or valid, and carries one warning line per rejected key naming
    the key and its value. The function NEVER raises and never leaves a field unset — a missing or
    invalid value always falls back to the documented default. Tolerances and the ladder policy use
    the named operational constants (not env-tunable here); the SLO pass bound is not loaded here —
    it is read from ``ResourceContract.target_p99_ms`` at the ladder/report boundary.
    """
    src = _env_map(env)
    offered_rate_per_s, rate_logs = _parse_offered_rate(src.get(_ENV_OFFERED_RATE))
    arrival_mode, mode_logs = _parse_arrival_mode(src.get(_ENV_ARRIVAL_MODE))
    seed, seed_logs = _parse_seed(src.get(_ENV_SEED))
    strata, strata_logs = _load_strata(src)
    run_spec = RunSpec(
        offered_rate_per_s=offered_rate_per_s,
        arrival_mode=arrival_mode,
        seed=seed,
        strata=strata,
        tolerances=default_tolerances(),
        ladder=default_ladder_config(),
    )
    logs = (
        *rate_logs,
        *mode_logs,
        *seed_logs,
        *strata_logs,
    )
    return run_spec, logs
