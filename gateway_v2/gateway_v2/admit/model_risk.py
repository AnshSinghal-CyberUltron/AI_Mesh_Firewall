"""GW29 `ModelRiskTracker`: bucketed sliding-window error score per model (design 5.5).

Each tracked model owns a ring of `buckets` counters keyed by `floor(clock / bucket_s)`; a bucket
that fell out of the window is zeroed lazily on write and ignored on read. The score is

    0.0                                   while total < the sample floor
    min(1, sum(weight[o] * n[o]) / total) otherwise

(the floor is `min_samples`, or `probation_min_samples` while on probation)

so it is always in `[0, 1]`, 0 below the sample floor, and monotone non-decreasing in the weighted
error count at a fixed total (weights are in `[0, 1]`).

Inputs are attacker-influenced, so the tracker is deliberately narrow:

* only KNOWN models are tracked (`known` is the operator catalog + fallback map), so a client
  cannot occupy slots with garbage names; `max_models` bounds the rest;
* each bucket keeps two small bounded sets of hash-truncated org ids, capped at the distinct-org
  floor: orgs that contributed a weighted (non-OK) sample, and orgs that contributed ANY sample.
  `AutoIsolator` needs `distinct_error_orgs >= min(floor, distinct_orgs_seen)`, so one abusive
  tenant cannot isolate a model for everyone while a one-tenant deployment still works. The org id
  is used to count and is never stored, labelled or reported;
* `observe` is O(1) under a lock and never raises: anything it cannot account for is counted in
  `untracked_total` and dropped.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from gateway_v2.domain.incident import ModelOutcome
from gateway_v2.runtime.resources import ResourceContract

__all__ = ("ModelRiskTracker", "RiskParams", "RiskReading")

_OUTCOMES: tuple[ModelOutcome, ...] = tuple(ModelOutcome)
_INDEX: Mapping[ModelOutcome, int] = MappingProxyType({o: i for i, o in enumerate(_OUTCOMES)})
_ORG_DIGEST_BYTES = 8


@dataclass(frozen=True, slots=True)
class RiskParams:
    """Everything the tracker needs from the contract. No literal lives here."""

    window_s: float
    bucket_s: float
    buckets: int
    min_samples: int
    probation_min_samples: int
    min_distinct_orgs: int
    weights: Mapping[ModelOutcome, float]
    threshold: float

    @classmethod
    def from_contract(cls, contract: ResourceContract) -> RiskParams:
        return cls(
            window_s=contract.risk_window_s(),
            bucket_s=contract.risk_bucket_s(),
            buckets=contract.risk_buckets(),
            min_samples=contract.risk_min_samples(),
            probation_min_samples=contract.risk_probation_min_samples(),
            min_distinct_orgs=contract.risk_min_distinct_orgs(),
            weights=contract.risk_weights(),
            threshold=contract.risk_threshold(),
        )


@dataclass(frozen=True, slots=True)
class RiskReading:
    model: str
    score: float
    total: int
    distinct_error_orgs: int
    distinct_orgs_seen: int
    on_probation: bool
    dominant: ModelOutcome | None
    """The weighted outcome contributing most to the score, or None when nothing weighed."""


class _Ring:
    __slots__ = ("ids", "counts", "err_orgs", "seen_orgs", "probation", "since_reset")

    def __init__(self, buckets: int) -> None:
        self.ids: list[int | None] = [None] * buckets
        self.counts: list[list[int]] = [[0] * len(_OUTCOMES) for _ in range(buckets)]
        self.err_orgs: list[set[str]] = [set() for _ in range(buckets)]
        self.seen_orgs: list[set[str]] = [set() for _ in range(buckets)]
        self.probation = False
        self.since_reset = 0

    def clear(self) -> None:
        for i in range(len(self.ids)):
            self._zero(i, None)

    def _zero(self, slot: int, bucket_id: int | None) -> None:
        self.ids[slot] = bucket_id
        self.counts[slot] = [0] * len(_OUTCOMES)
        self.err_orgs[slot].clear()
        self.seen_orgs[slot].clear()


def _org_token(org_id: str) -> str:
    raw = org_id.encode("utf-8", "replace")
    return hashlib.blake2b(raw, digest_size=_ORG_DIGEST_BYTES).hexdigest()


class ModelRiskTracker:
    """Per-model sliding-window risk. Thread-safe; O(1) observe; never raises."""

    def __init__(
        self,
        *,
        clock: Callable[[], float],
        params: RiskParams,
        max_models: int,
        known: Callable[[str], bool] | None = None,
    ) -> None:
        if max_models < 1:
            raise ValueError("a risk tracker needs at least one model slot")
        self._clock = clock
        self._params = params
        self._max_models = max_models
        self._known = known
        self._lock = threading.Lock()
        self._rings: dict[str, _Ring] = {}
        self._untracked = 0

    @property
    def untracked_total(self) -> int:
        with self._lock:
            return self._untracked

    def _bucket_id(self) -> int:
        return int(self._clock() // self._params.bucket_s)

    def observe(self, model: str, outcome: ModelOutcome, org_id: str) -> None:
        """Record one attempt outcome. Unknown models, a full table or a bad outcome are counted."""
        index = _INDEX.get(outcome) if isinstance(outcome, ModelOutcome) else None
        known = isinstance(model, str) and (self._known is None or self._known(model))
        with self._lock:
            if index is None or not known:
                self._untracked += 1
                return
            ring = self._rings.get(model)
            if ring is None:
                if len(self._rings) >= self._max_models:
                    self._untracked += 1
                    return
                ring = self._rings[model] = _Ring(self._params.buckets)
            bucket = self._bucket_id()
            slot = bucket % self._params.buckets
            if ring.ids[slot] != bucket:
                ring._zero(slot, bucket)  # noqa: SLF001 - ring is this module's private helper
            ring.counts[slot][index] += 1
            ring.since_reset += 1
            if ring.probation and ring.since_reset >= self._params.min_samples:
                ring.probation = False
            token = _org_token(org_id if isinstance(org_id, str) else "")
            cap = self._params.min_distinct_orgs
            if len(ring.seen_orgs[slot]) < cap:
                ring.seen_orgs[slot].add(token)
            if self._params.weights.get(outcome, 0.0) > 0 and len(ring.err_orgs[slot]) < cap:
                ring.err_orgs[slot].add(token)

    def score(self, model: str) -> RiskReading:
        with self._lock:
            return self._reading_locked(model)

    def readings(self) -> tuple[RiskReading, ...]:
        with self._lock:
            return tuple(self._reading_locked(m) for m in sorted(self._rings))

    def reset_for_recovery(self, model: str) -> None:
        """Zero the window and enter probation: stale pre-isolation samples never count again."""
        with self._lock:
            ring = self._rings.get(model)
            if ring is None:
                return
            ring.clear()
            ring.probation = True
            ring.since_reset = 0

    def _reading_locked(self, model: str) -> RiskReading:
        ring = self._rings.get(model)
        if ring is None:
            return RiskReading(model, 0.0, 0, 0, 0, False, None)
        params = self._params
        current = self._bucket_id()
        totals = [0] * len(_OUTCOMES)
        err: set[str] = set()
        seen: set[str] = set()
        for slot in range(params.buckets):
            bucket = ring.ids[slot]
            if bucket is None or not current - params.buckets < bucket <= current:
                continue
            for i, n in enumerate(ring.counts[slot]):
                totals[i] += n
            err |= ring.err_orgs[slot]
            seen |= ring.seen_orgs[slot]
        total = sum(totals)
        floor = params.probation_min_samples if ring.probation else params.min_samples
        weighted = [totals[_INDEX[o]] * params.weights.get(o, 0.0) for o in _OUTCOMES]
        cap = params.min_distinct_orgs
        score = 0.0 if total < floor or total == 0 else min(1.0, sum(weighted) / total)
        dominant = _OUTCOMES[weighted.index(max(weighted))] if max(weighted) > 0 else None
        return RiskReading(
            model, score, total, min(len(err), cap), min(len(seen), cap), ring.probation, dominant,
        )
