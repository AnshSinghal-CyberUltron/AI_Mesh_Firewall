"""GW29 writer-side incident scopes: org-model projection, isolation bodies, rollout guard.

``state_control`` imports only ``gateway_v2.domain`` (the allowed direction): the scope grammar,
the canonical model function and the isolation origin come from ``domain.incident``, so the
writer, the parser and the gateway gate cannot disagree about a model name.

Rollout (design 9). An OLD gateway raises ``StoreDataUnavailable`` on an ``om:`` / ``iso:`` key and
stales its whole snapshot, so a record of a new shape may reach the store only after the whole fleet
understands it. The projection is therefore behind ``AMF_STATE_PROJECT_INCIDENT_SCOPES`` (default
OFF) AND a deploy-time declaration ``AMF_GATEWAY_MIN_SCOPE_GENERATION`` >= ``KS_SCOPE_GENERATION``.
Flag ON with the declaration missing or lower is a refusal to write the new scopes, logged at ERROR
with the remediation. The declaration is an operator statement, not a verified fact: the rollout
runbook confirms ``min(incident_scope_generation) >= 2`` across the fleet first (decision C22).
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from gateway_v2.domain.incident import (
    KS_SCOPE_GENERATION,
    REASON_MAX,
    IsolationOrigin,
    canonical_model,
    org_model_scope,
)

__all__ = (
    "MIN_GENERATION_ENV",
    "PROJECT_FLAG_ENV",
    "IncidentScopePolicy",
    "is_isolation_scope",
    "isolation_body",
    "org_model_projection",
    "policy_from_env",
)

PROJECT_FLAG_ENV = "AMF_STATE_PROJECT_INCIDENT_SCOPES"
MIN_GENERATION_ENV = "AMF_GATEWAY_MIN_SCOPE_GENERATION"

#: The `iso:` scope prefix. Pinned to `domain.incident.isolation_scope` by a test.
_ISO_PREFIX = "iso:"
_TRUE = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True, slots=True)
class IncidentScopePolicy:
    """Whether this projector may write ``om:`` / ``iso:`` scopes."""

    flag_on: bool = False
    declared_generation: int | None = None

    @property
    def enabled(self) -> bool:
        return (
            self.flag_on
            and self.declared_generation is not None
            and self.declared_generation >= KS_SCOPE_GENERATION
        )

    @property
    def refusal(self) -> str | None:
        """Why a flag-ON projector still refuses to write the new scopes, else None."""
        if not self.flag_on or self.enabled:
            return None
        return (
            f"{PROJECT_FLAG_ENV} is on but {MIN_GENERATION_ENV} is "
            f"{'unset' if self.declared_generation is None else self.declared_generation}, below "
            f"the scope generation {KS_SCOPE_GENERATION}: org-model and isolation scopes are NOT "
            "written. An old gateway would stale its whole snapshot on them. Roll every gateway "
            f"to a build that exports incident_scope_generation >= {KS_SCOPE_GENERATION}, confirm "
            f"min(incident_scope_generation) >= {KS_SCOPE_GENERATION} across the fleet, then set "
            f"{MIN_GENERATION_ENV}={KS_SCOPE_GENERATION}"
        )


def policy_from_env(env: Mapping[str, str]) -> IncidentScopePolicy:
    flag = (env.get(PROJECT_FLAG_ENV) or "").strip().casefold() in _TRUE
    raw = (env.get(MIN_GENERATION_ENV) or "").strip()
    try:
        declared: int | None = int(raw) if raw else None
    except ValueError:
        declared = None
    return IncidentScopePolicy(flag_on=flag, declared_generation=declared)


def is_isolation_scope(scope: str) -> bool:
    """True for an `iso:<model>` key. The projector never derives or clears these: control's
    isolation path (`StateWriter.isolation`) owns them, including the explicit OFF on expiry."""
    return scope.startswith(_ISO_PREFIX)


def org_model_projection(org_slug: str, model_name: str) -> str | None:
    """``om:<slug>:<model>`` for a valid slug and a model that canonicalises, else None.

    The writer canonicalises WITHOUT aliases (it has no catalog); the gateway applies its own
    alias table at read, so the record is valid for any gateway.
    """
    try:
        return org_model_scope(org_slug, model_name)
    except ValueError:
        return None


def _finite_positive(value: float | None, what: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{what} must be a finite positive timestamp")
    return float(value)


def isolation_body(
    *,
    on: bool,
    fallback: str | None = None,
    origin: IsolationOrigin = IsolationOrigin.OPERATOR,
    reason: str = "",
    expires_at: float | None = None,
    created_at: float | None = None,
    no_expiry: bool = False,
) -> dict[str, object]:
    """The body of an ``iso:<model>`` record. Raises ValueError rather than write a body the
    gateway would read as malformed: an operator isolation needs a finite ``expires_at`` or an
    explicit ``no_expiry``, never both and never neither. ``on=False`` is the explicit OFF."""
    if not on:
        return {"on": False}
    if len(reason) > REASON_MAX or any(not " " <= ch <= "~" for ch in reason):
        raise ValueError("isolation reason must be printable ASCII within the reason bound")
    expires = _finite_positive(expires_at, "expires_at")
    created = _finite_positive(created_at, "created_at")
    if origin is IsolationOrigin.OPERATOR and (expires is None) == (not no_expiry):
        raise ValueError("an operator isolation needs exactly one of expires_at or no_expiry")
    if no_expiry and expires is not None:
        raise ValueError("no_expiry excludes expires_at")
    body: dict[str, object] = {"on": True, "origin": origin.value}
    if fallback is not None:
        canon = canonical_model(fallback)
        if canon is None:
            raise ValueError("isolation fallback is not a valid model name")
        body["fallback"] = canon
    if reason:
        body["reason"] = reason
    if created is not None:
        body["created_at"] = created
    if expires is not None:
        body["expires_at"] = expires
    if no_expiry:
        body["no_expiry"] = True
    return body
