"""GW28 multi-model routing value types (design C1).

Every type is a frozen, slotted dataclass; mapping-valued data is stored as sorted tuples. There is
no module-level mutable state and no capacity literal: every bound (candidate count, weight,
identifier length) is injected by the caller from ``ResourceContract``. The identifier pattern is
built by a pure function from the injected maximum length (``re`` memoises the compile), so no
compiled-pattern global exists.

Nothing here carries prompt text or an HTTP object. ``RoutingContext`` is passed to the router as an
argument, never placed inside ``UpstreamRequest.body``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from gateway_v2.domain.plan import ExecutionPlan

__all__ = (
    "DECISION_SOURCE",
    "REASON_FALLBACK_AFTER_RATE_LIMITED",
    "REASON_FALLBACK_AFTER_TIMEOUT",
    "REASON_FALLBACK_AFTER_UNAVAILABLE",
    "REASON_FALLBACK_AFTER_UPSTREAM_ERROR",
    "REASON_PRIMARY",
    "REASON_REFUSED_ALL_UNAVAILABLE",
    "REASON_REFUSED_ATTEMPTS_EXHAUSTED",
    "REASON_REFUSED_CONTEXT_MISSING",
    "REASON_REFUSED_INTERNAL_ERROR",
    "REASON_REFUSED_NO_CANDIDATES",
    "REASON_REFUSED_SNAPSHOT_UNAVAILABLE",
    "REASON_REFUSED_TEARDOWN_UNCONFIRMED",
    "REASON_REFUSED_UNKNOWN_MODEL",
    "Candidate",
    "CandidateSet",
    "RoutingConfigError",
    "RoutingContext",
    "RoutingDecision",
    "RoutingRefusal",
    "RoutingSnapshot",
    "RoutingTable",
    "ident_pattern",
    "is_valid_model_name",
    "upstream_model_pattern",
)

DECISION_SOURCE = "plan_hrw"

REASON_PRIMARY = "primary"
REASON_FALLBACK_AFTER_TIMEOUT = "fallback_after_timeout"
REASON_FALLBACK_AFTER_UNAVAILABLE = "fallback_after_unavailable"
REASON_FALLBACK_AFTER_RATE_LIMITED = "fallback_after_rate_limited"
REASON_FALLBACK_AFTER_UPSTREAM_ERROR = "fallback_after_upstream_error"
REASON_REFUSED_NO_CANDIDATES = "refused_no_candidates"
REASON_REFUSED_UNKNOWN_MODEL = "refused_unknown_model"
REASON_REFUSED_ALL_UNAVAILABLE = "refused_all_unavailable"
REASON_REFUSED_ATTEMPTS_EXHAUSTED = "refused_attempts_exhausted"
REASON_REFUSED_CONTEXT_MISSING = "refused_context_missing"
REASON_REFUSED_TEARDOWN_UNCONFIRMED = "refused_teardown_unconfirmed"
REASON_REFUSED_SNAPSHOT_UNAVAILABLE = "refused_snapshot_unavailable"
REASON_REFUSED_INTERNAL_ERROR = "refused_internal_error"

_SEP = "\x1f"


class RoutingConfigError(ValueError):
    """Raised only at boot-time construction of routing configuration (fatal)."""


def ident_pattern(max_len: int) -> re.Pattern[str]:
    """``[A-Za-z0-9._:-]{1,max_len}``, ASCII-only (no ``|``, ``\\x1f``, space or control chars)."""
    return re.compile(rf"[A-Za-z0-9._:-]{{1,{int(max_len)}}}", re.ASCII)


def upstream_model_pattern(max_len: int) -> re.Pattern[str]:
    """Looser pattern for an upstream model name (``/``, ``@`` and ``+`` allowed; round 8 F2)."""
    return re.compile(rf"[A-Za-z0-9._:/@+-]{{1,{int(max_len)}}}", re.ASCII)


def is_valid_model_name(name: object, max_len: int) -> bool:
    """True iff ``name`` is a ``str`` that fully matches the identifier pattern (fullmatch)."""
    if not isinstance(name, str) or max_len < 1:
        return False
    return ident_pattern(max_len).fullmatch(name) is not None


def _label_ok(label: str, max_len: int) -> bool:
    return (
        bool(label)
        and len(label) <= max_len
        and label.isascii()
        and label.isprintable()
        and _SEP not in label
    )


@dataclass(frozen=True, slots=True)
class Candidate:
    """One routable upstream model. ``provider_key`` selects the client; it is not a HRW input."""

    candidate_id: str
    destination: str
    upstream_model: str
    served_models: frozenset[str]
    weight: int
    provider_key: str
    passthrough: bool = False


def _validate_candidate(
    c: Candidate, *, max_weight: int, max_len: int, allow_passthrough: bool
) -> None:
    ident = ident_pattern(max_len)
    if not isinstance(c.candidate_id, str) or ident.fullmatch(c.candidate_id) is None:
        raise RoutingConfigError("candidate_id must match the identifier pattern")
    if not isinstance(c.provider_key, str) or ident.fullmatch(c.provider_key) is None:
        raise RoutingConfigError(f"candidate {c.candidate_id}: bad provider_key")
    if not isinstance(c.destination, str) or not _label_ok(c.destination, max_len):
        raise RoutingConfigError(f"candidate {c.candidate_id}: bad destination label")
    if isinstance(c.weight, bool) or not isinstance(c.weight, int):
        raise RoutingConfigError(f"candidate {c.candidate_id}: weight must be an int")
    if not 1 <= c.weight <= max_weight:
        raise RoutingConfigError(f"candidate {c.candidate_id}: weight out of range")
    if c.passthrough:
        if not allow_passthrough:
            raise RoutingConfigError(f"candidate {c.candidate_id}: passthrough not allowed here")
        if c.served_models or c.upstream_model:
            raise RoutingConfigError(f"candidate {c.candidate_id}: passthrough carries no model")
        return
    if not c.served_models:
        raise RoutingConfigError(f"candidate {c.candidate_id}: served_models empty")
    for alias in c.served_models:
        if not isinstance(alias, str) or ident.fullmatch(alias) is None:
            raise RoutingConfigError(f"candidate {c.candidate_id}: bad served alias")
    if (
        not isinstance(c.upstream_model, str)
        or upstream_model_pattern(max_len).fullmatch(c.upstream_model) is None
    ):
        raise RoutingConfigError(f"candidate {c.candidate_id}: bad upstream_model")


@dataclass(frozen=True, slots=True)
class CandidateSet:
    """A non-empty, validated, id-sorted set of candidates."""

    candidates: tuple[Candidate, ...]

    @classmethod
    def build(
        cls,
        candidates: Iterable[Candidate],
        *,
        max_candidates: int,
        max_weight: int,
        max_ident_len: int,
        allow_passthrough: bool = False,
    ) -> CandidateSet:
        items = tuple(candidates)
        if not items:
            raise RoutingConfigError("candidate set is empty")
        if len(items) > max_candidates:
            raise RoutingConfigError("candidate set exceeds the candidate cap")
        seen_ids: set[str] = set()
        seen_dest: set[str] = set()
        for c in items:
            _validate_candidate(
                c,
                max_weight=max_weight,
                max_len=max_ident_len,
                allow_passthrough=allow_passthrough,
            )
            if c.candidate_id in seen_ids:
                raise RoutingConfigError(f"duplicate candidate_id {c.candidate_id}")
            if c.destination in seen_dest:
                raise RoutingConfigError(f"duplicate destination for {c.candidate_id}")
            seen_ids.add(c.candidate_id)
            seen_dest.add(c.destination)
        return cls(tuple(sorted(items, key=lambda c: c.candidate_id)))


@dataclass(frozen=True, slots=True)
class RoutingTable:
    """Boot-time org -> candidate-set table plus an explicit operator ``fallback_set`` (D-1)."""

    by_org: tuple[tuple[str, CandidateSet], ...]
    fallback_set: CandidateSet | None = None

    @classmethod
    def build(
        cls,
        by_org: Mapping[str, CandidateSet],
        *,
        fallback_set: CandidateSet | None = None,
    ) -> RoutingTable:
        for org in by_org:
            if not isinstance(org, str) or not org or _SEP in org:
                raise RoutingConfigError("org id must be a non-empty string")
        # org ids are operator data, never client text; only the structure is validated here.
        return cls(tuple(sorted(by_org.items(), key=lambda kv: kv[0])), fallback_set)

    def candidates_for(self, org_id: str) -> CandidateSet | None:
        for org, cset in self.by_org:
            if org == org_id:
                return cset
        return None

    def all_candidates(self) -> tuple[Candidate, ...]:
        out: list[Candidate] = []
        for _org, cset in self.by_org:
            out.extend(cset.candidates)
        if self.fallback_set is not None:
            out.extend(self.fallback_set.candidates)
        return tuple(out)


@dataclass(frozen=True, slots=True)
class RoutingRefusal:
    """A typed, returned (never raised) refusal: ``code`` is a ``ROUTING_*`` posture code."""

    code: str
    reason: str


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """One routing decision record. No content, no HTTP object, no client free text."""

    request_key: str
    requested_model: str
    selected_candidate: str | None
    selected_model: str | None
    destination: str | None
    attempt_index: int
    remapped_from: str | None
    routing_reason: str
    decision_source: str
    candidate_count: int
    refusal_code: str | None
    cause: str | None


@dataclass(frozen=True, slots=True)
class RoutingSnapshot:
    """Health as an injected, once-per-request reading; never a probe."""

    unavailable: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class RoutingContext:
    """Selection inputs, passed out of band. ``requested_model`` is ``""`` when it was invalid."""

    org_id: str
    epoch: int
    sequence: int
    content_hash: str
    request_key: str
    requested_model: str
    sink: Callable[[RoutingDecision], None]

    @classmethod
    def from_plan(
        cls,
        plan: ExecutionPlan,
        *,
        request_key: str,
        requested_model: object,
        sink: Callable[[RoutingDecision], None],
        max_ident_len: int,
    ) -> RoutingContext:
        """The single canonicalisation point for the client-supplied model name."""
        valid = isinstance(requested_model, str) and is_valid_model_name(
            requested_model, max_ident_len
        )
        name = requested_model if valid and isinstance(requested_model, str) else ""
        return cls(
            org_id=plan.org_id,
            epoch=plan.epoch,
            sequence=plan.sequence,
            content_hash=plan.content_hash,
            request_key=request_key,
            requested_model=name,
            sink=sink,
        )
