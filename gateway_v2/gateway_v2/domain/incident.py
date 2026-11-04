"""GW29 incident-response value types, scope grammar and the one canonical model name.

Pure and dependency-free (`domain` is the bottom layer). Everything here is a frozen, slotted
value or a pure function: no clock, no random, no I/O, no module state. `admit` decides, `edge`
renders; nothing in this module knows what an HTTP object is.

Two rules carry most of the weight (design 4.6):

* `canonical_model` is the ONLY place a model name is normalised. Kills, org-model kills and
  isolations are matched by exact set membership, so the writer, the snapshot parser and the gate
  must all agree on the spelling or a case/alias variant bypasses a kill.
* Absence never means "off", and a record this module cannot understand is either quarantined
  (known shape, bad model: it can never match an admitted request) or raised as
  `StoreDataUnavailable` (unknown shape: could be an engaged switch we would otherwise skip).
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol

from gateway_v2.domain.posture import KILL_SWITCH_UNAVAILABLE, MIN_RETRY_AFTER_S
from gateway_v2.domain.state import SignedRecord, StoreDataUnavailable

__all__ = (
    "AUTO_REASONS",
    "INCIDENT_CAUSES",
    "KS_SCOPE_GENERATION",
    "MODEL_NAME_MAX",
    "REASON_MAX",
    "ROUTING_SENTINEL",
    "SLUG_MAX",
    "ConstraintSource",
    "FallbackPolicy",
    "GateVerdict",
    "IncidentDecider",
    "IncidentEvent",
    "IncidentEventKind",
    "IsolationOrigin",
    "IsolationRecord",
    "KillEvaluation",
    "ModelCatalog",
    "ModelOutcome",
    "ModelProfile",
    "ModelStatus",
    "Notifier",
    "ParsedScope",
    "RequestConstraints",
    "RerouteNote",
    "RouteDecision",
    "RouteOutcome",
    "ScopeKind",
    "StreamBinding",
    "SweepResult",
    "canonical_model",
    "fail_closed_incident",
    "fail_closed_org",
    "fail_closed_recheck",
    "isolation_scope",
    "org_model_scope",
    "parse_isolation_body",
    "parse_scope",
)

# -- protocol limits -------------------------------------------------------------------------
# These are limits of the scope GRAMMAR (what a record key / model name may look like), not
# capacities, so they live in `domain` by the same reasoning as the projector's `_SLUG`.
MODEL_NAME_MAX: Final = 128
SLUG_MAX: Final = 64
REASON_MAX: Final = 200
ROUTING_SENTINEL: Final = "auto"
"""The product's routing mode, not a model name. The gate resolves or refuses it (no router)."""
KS_SCOPE_GENERATION: Final = 2
"""Scope-grammar generation: 1 = global/org/model, 2 adds `om:` and `iso:` (rollout, design 9)."""

MODEL_NAME: Final = re.compile(rf"[A-Za-z0-9._:/-]{{1,{MODEL_NAME_MAX}}}")
MODEL_NAME_CANON: Final = re.compile(rf"[a-z0-9._:/-]{{1,{MODEL_NAME_MAX}}}")
SLUG: Final = re.compile(rf"[-a-zA-Z0-9_]{{1,{SLUG_MAX}}}")
_REASON_OK: Final = re.compile(rf"[ -~]{{0,{REASON_MAX}}}")

_GLOBAL_KEY: Final = "global"
_ORG_PREFIX: Final = "org:"
_MODEL_PREFIX: Final = "model:"
_ORG_MODEL_PREFIX: Final = "om:"
_ISO_PREFIX: Final = "iso:"

INCIDENT_CAUSES: Final[tuple[str, ...]] = (
    # phase 4b / recheck refusals (the nine refusal counters of design 5.10)
    "stale",
    "global",
    "org",
    "org_model",
    "model",
    "isolated_no_fallback",
    "reroute_refused",
    "gate_error",
    "recheck",
    # client-attributable name problems (carried on the single `model_killed` posture code)
    "unresolvable_model",
    "unknown_model",
    # wiring / surface causes
    "unwired",
    "reroute_unsupported",
    # in-flight cuts
    "cut_killed",
    "cut_key_revoked",
    "cut_snapshot_stale",
    # reroute and isolation-change audit records
    "reroute",
    "isolate",
    "recover",
    "auto_skipped",
)
"""Closed vocabulary for `detail.cause` on every incident audit record and `GateVerdict.cause`."""

AUTO_REASONS: Final[tuple[str, ...]] = (
    "error_rate",
    "guard_rate",
    "probation_failed",
    "no_fallback",
    "fallback_ineligible",
)
"""Closed reasons for auto-origin events; never request content."""


class IsolationOrigin(StrEnum):
    OPERATOR = "operator"
    AUTO = "auto"


class ScopeKind(StrEnum):
    GLOBAL = "global"
    ORG = "org"
    MODEL = "model"
    ORG_MODEL = "org_model"
    ISOLATION = "isolation"


class IncidentEventKind(StrEnum):
    ISOLATE = "isolate"
    RECOVER = "recover"
    REROUTE = "reroute"
    AUTO_ISOLATE_SKIPPED = "auto_isolate_skipped"


class RouteOutcome(StrEnum):
    DIRECT = "direct"
    REROUTE = "reroute"
    REFUSE_KILLED = "refuse_killed"
    REFUSE_NO_FALLBACK = "refuse_no_fallback"
    REFUSE_REROUTE = "refuse_reroute"


class ModelOutcome(StrEnum):
    """What one provider attempt tells the risk tracker (design 5.5)."""

    OK = "ok"
    TIMEOUT = "timeout"
    SERVER_ERROR = "server_error"
    RATE_LIMITED = "rate_limited"
    MALFORMED = "malformed"
    GUARD_HIT = "guard_hit"


@dataclass(frozen=True, slots=True)
class IsolationRecord:
    """One `iso:<model>` record. `malformed` refuses THAT model only, never global."""

    model: str
    fallback: str | None
    origin: IsolationOrigin
    reason: str
    created_at: float | None
    expires_at: float | None
    no_expiry: bool
    feed_seq: int
    malformed: bool


@dataclass(frozen=True, slots=True)
class ModelStatus:
    model: str
    killed: bool
    isolation: IsolationRecord | None
    """The ACTIVE isolation only: expiry has already been applied."""


@dataclass(frozen=True, slots=True)
class ModelProfile:
    name: str
    provider: str
    compliance_tags: frozenset[str]
    sensitivity_ceiling: int


@dataclass(frozen=True, slots=True)
class RequestConstraints:
    known: bool
    allowed_models: frozenset[str] | None
    required_tags: frozenset[str]
    sensitivity_rank: int


@dataclass(frozen=True, slots=True)
class RouteDecision:
    outcome: RouteOutcome
    routed_model: str | None
    isolated_model: str | None
    fallback: str | None
    decision_source: str
    reason: str


@dataclass(frozen=True, slots=True)
class RerouteNote:
    """What an audit record needs to say about a reroute. Model names are operator-known."""

    requested: str
    routed: str
    isolated_model: str
    decision_source: str
    reason: str


@dataclass(frozen=True, slots=True)
class StreamBinding:
    """What an in-flight stream is bound to: org and model for the kill, key for the revoke."""

    org_id: str
    model: str
    key_hash: str
    key_feed_seq: int


@dataclass(frozen=True, slots=True)
class IncidentEvent:
    """A notification. No content, key, credential or request-id field, by construction."""

    kind: IncidentEventKind
    scope: str
    model: str
    fallback: str | None
    reason: str
    origin: IsolationOrigin
    recorded_at: float
    dedup_key: str


@dataclass(frozen=True, slots=True)
class KillEvaluation:
    """Every fact the gate needs, read under ONE lock acquisition.

    `state` is the `KillSwitchState` string value (`ok` | `engaged` | `stale`); `admit` owns the
    enum and `domain` cannot import it, so the value is carried as the plain string it is.
    """

    state: str
    global_engaged: bool
    org_engaged: bool
    org_model_engaged: bool
    model_engaged: bool
    refresh_age_s: float | None

    @property
    def stale(self) -> bool:
        return self.state == "stale"

    @property
    def engaged(self) -> bool:
        return (
            self.global_engaged
            or self.org_engaged
            or self.org_model_engaged
            or self.model_engaged
        )


@dataclass(frozen=True, slots=True)
class SweepResult:
    """Models whose isolation ended since the previous sweep, each exactly once, disjoint."""

    local_expired: tuple[str, ...]
    signed_expired: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParsedScope:
    kind: ScopeKind
    org: str | None
    model: str | None
    quarantined: bool


@dataclass(frozen=True, slots=True)
class GateVerdict:
    """The ONLY verdict shape: exactly one of `refuse_code` / `routed_model` is set."""

    refuse_code: str | None
    cause: str | None
    retry_after_s: float | None
    routed_model: str | None
    reroute: RouteDecision | None

    def __post_init__(self) -> None:
        refusing = self.refuse_code is not None
        if refusing == (self.routed_model is not None):
            raise ValueError("a GateVerdict is exactly one of refuse or proceed")
        if refusing:
            if self.cause not in INCIDENT_CAUSES:
                raise ValueError(f"a refusal needs a cause from INCIDENT_CAUSES: {self.cause!r}")
            retry = self.retry_after_s
            if retry is None or not math.isfinite(retry) or retry < MIN_RETRY_AFTER_S:
                raise ValueError("a refusal needs a finite retry_after_s >= MIN_RETRY_AFTER_S")
            if self.reroute is not None:
                raise ValueError("a refusal carries no reroute")
        else:
            if self.cause is not None or self.retry_after_s is not None:
                raise ValueError("a proceed carries no cause and no retry_after_s")
            if not self.routed_model:
                raise ValueError("a proceed needs a non-empty routed_model")

    @classmethod
    def proceed(cls, routed_model: str, reroute: RouteDecision | None = None) -> GateVerdict:
        return cls(None, None, None, routed_model, reroute)

    @classmethod
    def refuse(cls, code: str, cause: str, retry_after_s: float) -> GateVerdict:
        return cls(code, cause, retry_after_s, None, None)


class Notifier(Protocol):
    async def notify(self, event: IncidentEvent) -> None: ...


class FallbackPolicy(Protocol):
    def fallback_for(self, model: str) -> str | None: ...


class ModelCatalog(Protocol):
    def profile(self, model: str) -> ModelProfile | None: ...


class ConstraintSource(Protocol):
    def for_request(
        self, plan: object, org_id: str, requested_model: str,
    ) -> RequestConstraints: ...


class IncidentDecider(Protocol):
    """What `ChatDeps` types against. Synchronous, RAM only, never raises."""

    def decide(self, *, org_id: str, requested_model: str, request_id: str) -> GateVerdict: ...

    def recheck(self, *, org_id: str, model: str) -> GateVerdict: ...

    def decide_org(self, *, org_id: str, request_id: str) -> GateVerdict: ...


def fail_closed_incident(*, org_id: str, requested_model: str, request_id: str) -> GateVerdict:
    """Default for an unwired gate: refuse. Never proceed on a missing component."""
    return GateVerdict.refuse(KILL_SWITCH_UNAVAILABLE, "unwired", MIN_RETRY_AFTER_S)


def fail_closed_recheck(*, org_id: str, model: str) -> GateVerdict:
    return GateVerdict.refuse(KILL_SWITCH_UNAVAILABLE, "unwired", MIN_RETRY_AFTER_S)


def fail_closed_org(*, org_id: str, request_id: str) -> GateVerdict:
    return GateVerdict.refuse(KILL_SWITCH_UNAVAILABLE, "unwired", MIN_RETRY_AFTER_S)


# -- canonical model identity (design 4.6) ---------------------------------------------------


def canonical_model(raw: object, aliases: Mapping[str, str] | None = None) -> str | None:
    """The one normalisation of a model name, or None when it cannot be one. Fail closed.

    strip, NFKC, ASCII only, casefold, one alias hop, charset and length. Control characters
    anywhere (including NUL) are refused. The alias table must not chain (an alias target is
    already canonical), which is what makes this idempotent; the catalog loader enforces it.
    """
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    if not name or len(name) > MODEL_NAME_MAX:
        return None
    if any(unicodedata.category(ch) == "Cc" for ch in name):
        return None
    name = unicodedata.normalize("NFKC", name)
    if not name.isascii():
        return None
    name = name.casefold()
    if aliases is not None:
        name = aliases.get(name, name)
    return name if MODEL_NAME_CANON.fullmatch(name) else None


def org_model_scope(org: str, model: str) -> str:
    """`om:<slug>:<model>` for a valid slug and a model that canonicalises. Raises ValueError."""
    canonical = canonical_model(model)
    if canonical is None or not SLUG.fullmatch(org):
        raise ValueError("org-model scope needs a valid org slug and model name")
    return f"{_ORG_MODEL_PREFIX}{org}:{canonical}"


def isolation_scope(model: str) -> str:
    """`iso:<model>` for a model that canonicalises. Raises ValueError."""
    canonical = canonical_model(model)
    if canonical is None:
        raise ValueError("isolation scope needs a valid model name")
    return f"{_ISO_PREFIX}{canonical}"


def parse_scope(key: str, aliases: Mapping[str, str] | None = None) -> ParsedScope:
    """Parse a KS record key. Unknown SHAPE raises; a known shape with a bad model quarantines."""
    if key == _GLOBAL_KEY:
        return ParsedScope(ScopeKind.GLOBAL, None, None, False)
    if key.startswith(_ORG_PREFIX):
        org = key[len(_ORG_PREFIX):]
        if not org:
            raise StoreDataUnavailable(f"kill-switch scope {key!r} names nothing")
        return ParsedScope(ScopeKind.ORG, org, None, False)
    if key.startswith(_MODEL_PREFIX):
        return _model_scope(key, key[len(_MODEL_PREFIX):], ScopeKind.MODEL, None, aliases)
    if key.startswith(_ORG_MODEL_PREFIX):
        org, sep, model = key[len(_ORG_MODEL_PREFIX):].partition(":")
        if not sep or not SLUG.fullmatch(org):
            raise StoreDataUnavailable(
                f"kill-switch scope {key[:SLUG_MAX]!r} has no valid org slug",
            )
        return _model_scope(key, model, ScopeKind.ORG_MODEL, org, aliases)
    if key.startswith(_ISO_PREFIX):
        return _model_scope(key, key[len(_ISO_PREFIX):], ScopeKind.ISOLATION, None, aliases)
    raise StoreDataUnavailable(
        f"kill-switch scope {key[:SLUG_MAX]!r} is not recognised; an unknown scope could be an "
        "engaged switch this process would ignore",
    )


def _model_scope(
    key: str,
    raw_model: str,
    kind: ScopeKind,
    org: str | None,
    aliases: Mapping[str, str] | None,
) -> ParsedScope:
    if not raw_model and kind is ScopeKind.MODEL:
        raise StoreDataUnavailable(f"kill-switch scope {key!r} names nothing")
    model = canonical_model(raw_model, aliases)
    # Known shape, unusable model: no admitted request can carry it, so ignoring it cannot admit a
    # killed model (C19). The caller counts it toward `on_count` and reports it.
    return ParsedScope(kind, org, model, model is None)


# -- isolation body parser -------------------------------------------------------------------


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _malformed(model: str, record: SignedRecord) -> IsolationRecord:
    return IsolationRecord(
        model, None, IsolationOrigin.OPERATOR, "", None, None, False, record.feed_seq, True,
    )


def _clean_reason(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = value[:REASON_MAX]
    return text if _REASON_OK.fullmatch(text) else "".join(
        ch if " " <= ch <= "~" else "?" for ch in text
    )


def parse_isolation_body(
    record: SignedRecord,
    model: str,
    aliases: Mapping[str, str] | None = None,
) -> IsolationRecord:
    """Parse the extras of an `on: true` `iso:` record. Bad extras mean `malformed=True`.

    A missing `origin` is `operator`. An operator isolation needs a finite `expires_at` or an
    explicit `no_expiry: true` (with `expires_at` null or absent); otherwise it is malformed. An
    auto isolation with a missing or unusable `expires_at` is NOT malformed: it expires by
    `created_at + auto_isolation_duration_s()` (the read model applies that, design 4.4).
    """
    try:
        body: object = json.loads(record.body)
    except (ValueError, UnicodeDecodeError):
        return _malformed(model, record)
    if not isinstance(body, dict):
        return _malformed(model, record)
    origin = _origin(body)
    fallback_raw = body.get("fallback")
    fallback = None if fallback_raw is None else canonical_model(fallback_raw, aliases)
    no_expiry = body.get("no_expiry", False)
    created_raw = body.get("created_at")
    created = _finite(created_raw)
    if (
        origin is None
        or (fallback_raw is not None and fallback is None)
        or not isinstance(no_expiry, bool)
        or (created_raw is not None and created is None)
    ):
        return _malformed(model, record)
    expires = _finite(body.get("expires_at"))
    if origin is IsolationOrigin.OPERATOR:
        # exactly one of: a finite deadline, or the explicit permanent marker
        if (expires is None) == (not no_expiry) or (expires is not None and expires <= 0):
            return _malformed(model, record)
    elif expires is not None and expires <= 0:
        expires = None
    return IsolationRecord(
        model, fallback, origin, _clean_reason(body.get("reason")), created, expires,
        no_expiry, record.feed_seq, False,
    )


def _origin(body: dict[object, object]) -> IsolationOrigin | None:
    if "origin" not in body:
        return IsolationOrigin.OPERATOR
    value = body["origin"]
    if value == IsolationOrigin.OPERATOR.value:
        return IsolationOrigin.OPERATOR
    if value == IsolationOrigin.AUTO.value:
        return IsolationOrigin.AUTO
    return None
