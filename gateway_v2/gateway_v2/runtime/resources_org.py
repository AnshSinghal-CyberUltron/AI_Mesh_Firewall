"""GW27 (D-13): capacity derivations for org rate/burst/TPM limits and audit retention.

``runtime/resources.py`` is at the 800-line module cap and is not edited; the capacity-literal
gate allowlists this module as its second home (``lint/check_capacity_literals.ALLOWED_RELS``).
Everything here is a pure function of an injected ``ResourceContract`` (gateway side) or of named
constants (``derive_retention_bounds``, which the audit exporter calls with no gateway worker
configuration). Each derivation fails closed with ``CapacityUnavailable`` below its minimum.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.resources import ResourceContract

# Standard deviations of binomial spread added to a worker's even burst share so a compliant
# tenant is not falsely refused when the kernel spreads accepts unevenly (about W*Q(z)).
_BURST_SPREAD_Z = 3.0
# Token-bucket window: ``org_tpm_limit`` is tokens per minute.
_ORG_TPM_WINDOW_S = 60.0
_SECONDS_PER_MINUTE = 60.0

# Retention (audit exporter). The hard floor is the youngest age any sweep may ever delete,
# whatever a stamp says; a batch bounds one DELETE; the period spaces sweeps; the page bounds org
# enumeration; max batches per org stops one backlog starving the rest; the stamp ceiling mirrors
# the serializer.
_RETENTION_HARD_FLOOR_DAYS = 1
_RETENTION_SWEEP_BATCH = 1000
_RETENTION_SWEEP_PERIOD_S = 3600.0
_RETENTION_ORG_PAGE = 100
_RETENTION_MAX_BATCHES_PER_ORG = 10
_RETENTION_STAMP_MAX_DAYS = 365


@dataclass(frozen=True, slots=True)
class OrgBounds:
    """Per-worker org-limit parameters derived from the contract."""

    workers: int
    slots: int
    max_single_request_cost: int
    tpm_window_s: float
    burst_spread_z: float

    def request_interval_s(self, requests_per_minute: int) -> float:
        """GCRA emission interval T_req = 60 / (rpm / W) for one worker's share."""
        if requests_per_minute < 1:
            raise CapacityUnavailable("org requests_per_minute below one")
        return _SECONDS_PER_MINUTE / (requests_per_minute / self.workers)

    def burst_per_worker(self, burst: int) -> int:
        """ceil(burst/W + z*sqrt(burst*(1/W)*(1-1/W))); exactly ``burst`` at W == 1."""
        if burst < 1:
            raise CapacityUnavailable("org burst below one")
        share = 1.0 / self.workers
        spread = self.burst_spread_z * math.sqrt(burst * share * (1.0 - share))
        return max(1, math.ceil(burst * share + spread))

    def token_interval_s(self, org_tpm_limit: int) -> float:
        """T_tok = window / (tpm / W)."""
        if org_tpm_limit < 1:
            raise CapacityUnavailable("org_tpm_limit below one")
        return self.tpm_window_s / (org_tpm_limit / self.workers)

    def token_tolerance_s(self, org_tpm_limit: int) -> float:
        """tau_tok = max(window, T_tok * max cost): no admissible request is starved."""
        return max(
            self.tpm_window_s,
            self.token_interval_s(org_tpm_limit) * self.max_single_request_cost,
        )


def derive_org_bounds(contract: ResourceContract) -> OrgBounds:
    workers = contract.workers()
    slots = contract.metrics_directory_slots()
    cost = contract.estimate_request_tokens(
        contract.max_request_bytes(),
        contract.max_output_tokens(),
        contract.max_completions(),
    )
    if workers < 1 or slots < 1 or cost < 1:
        raise CapacityUnavailable("org bounds below minimum to serve")
    return OrgBounds(
        workers=workers,
        slots=slots,
        max_single_request_cost=cost,
        tpm_window_s=_ORG_TPM_WINDOW_S,
        burst_spread_z=_BURST_SPREAD_Z,
    )


@dataclass(frozen=True, slots=True)
class RetentionBounds:
    """Sweep bounds for ``audit_control``; needs no gateway contract."""

    hard_floor_days: int
    sweep_batch: int
    sweep_period_s: float
    org_page: int
    max_batches_per_org: int
    stamp_max_days: int


def derive_retention_bounds() -> RetentionBounds:
    bounds = RetentionBounds(
        hard_floor_days=_RETENTION_HARD_FLOOR_DAYS,
        sweep_batch=_RETENTION_SWEEP_BATCH,
        sweep_period_s=_RETENTION_SWEEP_PERIOD_S,
        org_page=_RETENTION_ORG_PAGE,
        max_batches_per_org=_RETENTION_MAX_BATCHES_PER_ORG,
        stamp_max_days=_RETENTION_STAMP_MAX_DAYS,
    )
    if (
        bounds.hard_floor_days < 1
        or bounds.sweep_batch < 1
        or bounds.sweep_period_s <= 0
        or bounds.org_page < 1
        or bounds.max_batches_per_org < 1
        or bounds.stamp_max_days < bounds.hard_floor_days
    ):
        raise CapacityUnavailable("retention bounds below minimum to run")
    return bounds
