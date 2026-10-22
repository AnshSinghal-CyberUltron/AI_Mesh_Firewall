"""DEV PROFILE boot wiring — opt-in ONLY via ``AMF_DEV_PROFILE=1``.

The default boot path (:func:`gateway_v2.edge.serve.build_boot_dependencies` with the variable
unset) is the shipped honest Fail_Closed_Fallback table and is not touched by this module. When
``AMF_DEV_PROFILE=1`` the serving entrypoint instead calls :func:`build_dev_dependencies`, which
wires the REAL pipeline pieces against local, explicitly-declared inputs so a developer can drive
identity → plan → admission → budget → input scan → resolver → provider → output guard with curl
or the stock OpenAI SDK.

**Selection happens per process from the ENVIRONMENT.** uvicorn's multiprocess manager re-imports
``serve`` in each worker and calls the module-level ``serving_app`` factory, so the decision is
re-made in every worker from ``os.environ`` — never captured in a parent closure.

**Refuse, never fall back.** Every required input is validated here; a missing / invalid value
raises :class:`~gateway_v2.edge.dev_components.DevProfileError`, which ``serve.main`` maps to the
shipped refuse-to-start contract (stderr JSON + exit 2). Nothing silently degrades to the
fail-closed defaults or to an invented value: ``q_safe``, the audit calibration inputs and the
budget inputs have NO default (no capacity literal outside ``runtime/resources.py``).

**Loud.** Every dev-supplied dependency is named in one ``boot: <dep> -> <impl>`` line carrying a
``DEV PROFILE`` marker, plus the honest notes on what the dev profile does NOT do.

Environment:

``AMF_DEV_PROFILE``            ``1`` to activate (``0`` / unset = shipped default boot)
``AMF_DEV_API_KEY``            the one accepted bearer key (required)
``AMF_DEV_ORG_ID``             the dev org (default ``dev-org``; must equal the plan's org_id)
``AMF_DEV_PLAN_PATH``          JSON plan file in the signed ``plan.document`` shape (required)
``AMF_DEV_MODELS``             comma-separated model ids for ``/v1/models`` (required)
``AMF_DEV_Q_SAFE``             DECLARED guard service rate, float > 0 (required)
``AMF_DEV_AUDIT_DRAIN_RATE_PER_S`` / ``AMF_DEV_AUDIT_BYTES_PER_RECORD``  DECLARED audit sizing
``AMF_DEV_BUDGET``             ``lease`` (real GW06 over Redis) or ``unlimited`` (required)
``AMF_DEV_REDIS_URL`` / ``AMF_DEV_BUDGET_POOL_REQUESTS`` / ``AMF_DEV_RATE_PER_S`` /
``AMF_DEV_BURST`` / ``AMF_DEV_LEASE_TTL_S``  required when ``AMF_DEV_BUDGET=lease``
``AMF_DEV_PROVIDER_URL`` / ``AMF_DEV_PROVIDER_API_KEY``  optional real upstream (else echo)
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from gateway_v2.detect.holdback import scan as holdback_scan
from gateway_v2.domain.plan import ExecutionPlan, Phase
from gateway_v2.edge.app import ASGIApplication
from gateway_v2.edge.dev_admit import (
    DevCompletingAdmission,
    DevLeaseBudget,
    DevServedApp,
    DevUnlimitedBudget,
    LeaseSettings,
    build_dev_admission,
)
from gateway_v2.edge.dev_components import (
    DeterministicFindings,
    DevProfileError,
    DevStaticIdentity,
    EchoProviderClient,
    StaticCatalogSource,
    env_text,
    load_dev_plan,
    parse_model_ids,
    plan_resolver,
    warm_dev_snapshot_source,
)
from gateway_v2.edge.dev_upstream import DevUpstreamProvider
from gateway_v2.runtime.errors import CapacityUnavailable, CapacityUnset
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.serving_config import load_serving_config

__all__ = (
    "DEV_PROFILE_ENV",
    "DevProfileConfig",
    "DevProfileError",
    "build_dev_dependencies",
    "dev_profile_active",
    "load_dev_config",
    "wrap_dev_app",
)

#: The single opt-in switch.
DEV_PROFILE_ENV = "AMF_DEV_PROFILE"
#: The default dev org id (a name, not a capacity value).
DEFAULT_DEV_ORG_ID = "dev-org"
#: The two accepted ``AMF_DEV_BUDGET`` modes.
BUDGET_LEASE = "lease"
BUDGET_UNLIMITED = "unlimited"
_INACTIVE_VALUES = frozenset(("", "0"))
_ACTIVE_VALUE = "1"


def dev_profile_active(env: Mapping[str, str]) -> bool:
    """``True`` iff ``AMF_DEV_PROFILE=1``; unset / ``0`` / blank ⇒ ``False``; else refuse.

    An unrecognised value (``yes``, ``true``) raises rather than being read as "off", so a typo
    can never leave an operator believing the dev profile is (or is not) active.
    """
    raw = env.get(DEV_PROFILE_ENV)
    if raw is None or raw.strip() in _INACTIVE_VALUES:
        return False
    if raw.strip() == _ACTIVE_VALUE:
        return True
    raise DevProfileError(f"{DEV_PROFILE_ENV}={raw!r} is not recognised; use 1 (on) or 0 (off)")


@dataclass(frozen=True, slots=True)
class DevProfileConfig:
    """Every validated DEV PROFILE input (secrets are held, never logged)."""

    api_key: str
    org_id: str
    plan_path: str
    models: tuple[str, ...]
    q_safe: float
    audit_drain_rate_per_s: float
    audit_bytes_per_record: int
    budget_mode: str
    lease: LeaseSettings | None
    provider_url: str | None
    provider_api_key: str


def _required(env: Mapping[str, str], name: str) -> str:
    value = env_text(env, name)
    if value is None:
        raise DevProfileError(f"{name} is required when {DEV_PROFILE_ENV}=1")
    return value


def _positive_float(env: Mapping[str, str], name: str) -> float:
    raw = _required(env, name)
    try:
        value = float(raw)
    except ValueError as exc:
        raise DevProfileError(f"{name}={raw!r} is not a number") from exc
    if not value > 0 or value != value or value == float("inf"):
        raise DevProfileError(f"{name}={raw!r} must be a finite number > 0")
    return value


def _int_at_least(env: Mapping[str, str], name: str, minimum: int) -> int:
    raw = _required(env, name)
    try:
        value = int(raw)
    except ValueError as exc:
        raise DevProfileError(f"{name}={raw!r} is not an integer") from exc
    if value < minimum:
        raise DevProfileError(f"{name}={raw!r} must be >= {minimum}")
    return value


def _lease_settings(env: Mapping[str, str]) -> LeaseSettings:
    return LeaseSettings(
        redis_url=_required(env, "AMF_DEV_REDIS_URL"),
        pool_requests=_int_at_least(env, "AMF_DEV_BUDGET_POOL_REQUESTS", 1),
        rate_per_s=_positive_float(env, "AMF_DEV_RATE_PER_S"),
        burst=_int_at_least(env, "AMF_DEV_BURST", 0),
        ttl_s=_positive_float(env, "AMF_DEV_LEASE_TTL_S"),
    )


def load_dev_config(env: Mapping[str, str]) -> DevProfileConfig:
    """Validate every DEV PROFILE input from ``env``; raise :class:`DevProfileError` on any gap."""
    budget_mode = _required(env, "AMF_DEV_BUDGET")
    if budget_mode not in (BUDGET_LEASE, BUDGET_UNLIMITED):
        raise DevProfileError(
            f"AMF_DEV_BUDGET={budget_mode!r}; use {BUDGET_LEASE!r} (real GW06 lease over Redis) "
            f"or {BUDGET_UNLIMITED!r} (budget NOT enforced)"
        )
    provider_url = env_text(env, "AMF_DEV_PROVIDER_URL")
    if provider_url is not None and urlsplit(provider_url).scheme not in ("http", "https"):
        raise DevProfileError("AMF_DEV_PROVIDER_URL must be an http(s) URL")
    return DevProfileConfig(
        api_key=_required(env, "AMF_DEV_API_KEY"),
        org_id=env_text(env, "AMF_DEV_ORG_ID") or DEFAULT_DEV_ORG_ID,
        plan_path=_required(env, "AMF_DEV_PLAN_PATH"),
        models=parse_model_ids(env_text(env, "AMF_DEV_MODELS")),
        q_safe=_positive_float(env, "AMF_DEV_Q_SAFE"),
        audit_drain_rate_per_s=_positive_float(env, "AMF_DEV_AUDIT_DRAIN_RATE_PER_S"),
        audit_bytes_per_record=_int_at_least(env, "AMF_DEV_AUDIT_BYTES_PER_RECORD", 1),
        budget_mode=budget_mode,
        lease=_lease_settings(env) if budget_mode == BUDGET_LEASE else None,
        provider_url=provider_url,
        provider_api_key=env.get("AMF_DEV_PROVIDER_API_KEY", ""),
    )


def _redact_url(url: str) -> str:
    """Drop any userinfo (password) from a URL before it reaches the boot log."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    netloc = f"{host}:{parts.port}" if parts.port is not None else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _admission(cfg: DevProfileConfig, contract: ResourceContract) -> DevCompletingAdmission:
    serving_cfg, _logs = load_serving_config()
    try:
        return build_dev_admission(
            contract=contract,
            q_safe=cfg.q_safe,
            audit_drain_rate_per_s=cfg.audit_drain_rate_per_s,
            audit_bytes_per_record=cfg.audit_bytes_per_record,
            drain_window_s=serving_cfg.drain_s,
        )
    except (CapacityUnset, CapacityUnavailable) as exc:
        raise DevProfileError(f"admission bounds refused: {exc}") from exc


def _budget(
    cfg: DevProfileConfig, contract: ResourceContract
) -> tuple[DevLeaseBudget | DevUnlimitedBudget, str]:
    if cfg.lease is None:
        return (
            DevUnlimitedBudget(org_id=cfg.org_id),
            "boot: quota -> DevUnlimitedBudget (DEV PROFILE: budget NOT enforced)",
        )
    try:
        budget = DevLeaseBudget(
            contract=contract, q_safe=cfg.q_safe, settings=cfg.lease, org_id=cfg.org_id
        )
    except (CapacityUnset, CapacityUnavailable, ValueError) as exc:
        raise DevProfileError(f"budget lease refused: {exc}") from exc
    lease = cfg.lease
    return budget, (
        f"boot: quota -> QuotaComponent(LocalGCRA + BudgetLease) GW06 over "
        f"{_redact_url(lease.redis_url)} (DEV PROFILE: spends 1 unit PER REQUEST because GW06 "
        "sizes its Lease_Chunk in requests while the GW15 lifecycle passes a character-count "
        f"cost; chunk={budget.lease.config.chunk} low_watermark="
        f"{budget.lease.config.low_watermark}; pool={lease.pool_requests} requests, "
        f"rate={lease.rate_per_s}/s, burst={lease.burst}, lease_ttl={lease.ttl_s}s -- all "
        "DECLARED via env; generation read once at startup, no budget-config feed)"
    )


def _provider(
    cfg: DevProfileConfig, contract: ResourceContract, probe: DeterministicFindings
) -> tuple[EchoProviderClient | DevUpstreamProvider, str]:
    if cfg.provider_url is None:
        return (
            EchoProviderClient(probe=probe),
            "boot: provider -> EchoProviderClient (DEV PROFILE: socket-free; replies "
            "'echo: <exact text the gateway forwarded>'; logs provider-side finding counts)",
        )
    return (
        DevUpstreamProvider(
            url=cfg.provider_url,
            api_key=cfg.provider_api_key,
            timeout_s=contract.max_stream_duration_s(),
        ),
        f"boot: provider -> HttpProviderClient (B4) to {_redact_url(cfg.provider_url)} "
        "(DEV PROFILE adapter: destination label replaced by the URL; body sent as OpenAI "
        "messages with EVERY input channel as role=user because Transform does not carry "
        "roles; non-2xx fails closed; a fresh client per request)",
    )


def _plan_log(cfg: DevProfileConfig, plan: ExecutionPlan) -> tuple[str, str]:
    rules = ", ".join(
        f"{rule.rule_id}:{rule.category.value}->{rule.action.value}/{rule.mode.value}"
        for rule in plan.rules
    )
    return (
        f"boot: snapshot_source -> warmed ReplicaSnapshot over {cfg.plan_path} (DEV PROFILE: "
        f"compiled by plan.compiler, org={plan.org_id} version={plan.version} "
        f"streaming={plan.streaming_mode.value}; rules=[{rules}]; no control-plane feed)",
        f"boot: resolver -> resolve.PlanResolver(plan {plan.version}) (DEV PROFILE: the REAL "
        "GW07 resolver; ONE resolver serves both phases, so only scope=both rules are accepted)",
    )


def build_dev_dependencies(
    *,
    contract: ResourceContract,
    env: Mapping[str, str],
    metrics_registry: object,
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Build the DEV PROFILE ``build_app`` kwargs + boot-log lines (refuse on any bad input)."""
    cfg = load_dev_config(env)
    plan = load_dev_plan(cfg.plan_path, org_id=cfg.org_id, compiled_at=time.time())
    admission = _admission(cfg, contract)
    budget, budget_log = _budget(cfg, contract)
    budget_s = contract.guard_wait_budget_s()
    input_detector = DeterministicFindings(phase=Phase.INPUT, budget_s=budget_s)
    output_detector = DeterministicFindings(phase=Phase.OUTPUT, budget_s=budget_s)
    provider, provider_log = _provider(cfg, contract, input_detector)
    catalog = StaticCatalogSource(model_ids=cfg.models, org_id=cfg.org_id, created=int(time.time()))
    deps: dict[str, object] = {
        "contract": contract,
        "provider": provider,
        "scanner": holdback_scan,
        "resolver": plan_resolver(plan),
        "metrics_registry": metrics_registry,
        "clock": time.monotonic,
        "input_detector": input_detector,
        "error_scanner": output_detector,
        "output_detector": output_detector,
        "identity": DevStaticIdentity(api_key=cfg.api_key, org_id=cfg.org_id),
        "snapshot_source": warm_dev_snapshot_source(plan, now=time.monotonic()),
        "admission": admission,
        "quota": budget,
        "catalog": catalog,
    }
    snapshot_log, resolver_log = _plan_log(cfg, plan)
    bounds = admission.bounds
    logs: tuple[str, ...] = (
        "boot: DEV PROFILE ACTIVE (AMF_DEV_PROFILE=1) -- local development wiring, NOT FOR "
        "PRODUCTION; every dependency marked DEV PROFILE below is dev-supplied",
        provider_log,
        "boot: scanner -> detect.holdback.scan (shipped default scan protocol)",
        resolver_log,
        "boot: metrics_registry -> minimal exposition registry (GW14d Publisher not wired)",
        "boot: input_detector -> GW09 deterministic PII+secret+injection detectors "
        "(DEV PROFILE wiring of the real detectors)",
        "boot: error_scanner -> GW09 deterministic detectors (DEV PROFILE: non-streaming output "
        "guard + mid-stream error frames)",
        "boot: output_detector -> GW09 deterministic detectors (DEV PROFILE: streaming "
        "completed-match seam of the holdback pipeline)",
        f"boot: identity -> DevStaticIdentity (DEV PROFILE: one static bearer key "
        f"AMF_DEV_API_KEY -> org {cfg.org_id}; hmac.compare_digest; anything else refuses)",
        snapshot_log,
        f"boot: admission -> AdmissionController (GW19) via DevCompletingAdmission (DEV PROFILE: "
        f"q_safe={cfg.q_safe} (DECLARED, not measured — real value comes from the GW20 fleet "
        f"run); audit_drain_rate={cfg.audit_drain_rate_per_s}/s audit_bytes_per_record="
        f"{cfg.audit_bytes_per_record} (DECLARED); derive_bounds request_depth="
        f"{bounds.request_depth} audit_depth={bounds.audit_depth}; slots "
        "released when each ASGI request returns because the GW15 lifecycle never calls "
        "complete())",
        budget_log,
        f"boot: catalog -> StaticCatalogSource {list(cfg.models)} (DEV PROFILE: AMF_DEV_MODELS)",
        "boot: DEV PROFILE NOTE: input REDACT findings are NOT masked before the provider -- the "
        "shipped GW15 input phase refuses only on BLOCK; redactable PII reaches the provider and "
        "the OUTPUT guard masks it in the reply",
    )
    return deps, logs


def wrap_dev_app(app: ASGIApplication, deps: Mapping[str, object]) -> ASGIApplication:
    """Wrap the shipped app for the dev profile; return ``app`` UNCHANGED on the default path."""
    admission = deps.get("admission")
    if not isinstance(admission, DevCompletingAdmission):
        return app
    quota = deps.get("quota")
    budget = quota if isinstance(quota, DevLeaseBudget) else None
    served = DevServedApp(app, admission=admission, budget=budget)
    return served
