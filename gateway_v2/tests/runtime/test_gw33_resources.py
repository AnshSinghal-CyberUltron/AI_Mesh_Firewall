"""GW33 task 2: output-parity resource derivations and the D-30 sensitivity clamp."""
from __future__ import annotations

import dataclasses

import pytest

from gateway_v2.runtime.errors import CapacityUnavailable, CapacityUnset
from gateway_v2.runtime.resources import ResourceContract
from gateway_v2.runtime.resources_output import (
    DEFAULT_OUTPUT_BOUNDS,
    REWRITE_TEMPLATE_MAX_CHARS,
    OutputBounds,
)


def _contract(p99: float = 20.0) -> ResourceContract:
    return ResourceContract(
        cpu_quota=4.0,
        memory_limit=400 * 1024 * 1024 * 32,
        fd_limit=4096,
        guard_capacity=None,
        target_p99_ms=p99,
        utilization_cap=0.75,
        per_worker_rss=400 * 1024 * 1024,
        worker_override=None,
        cpu_source="test",
        mem_source="test",
        fd_source="test",
    )


def test_every_accessor_is_positive_on_the_default_contract() -> None:
    contract = _contract()
    values = (
        contract.exfil_min_payload_chars(),
        contract.exfil_min_encoded_run(),
        contract.exfil_min_label_chars(),
        contract.exfil_max_host_chars(),
        contract.output_scan_candidate_cap(),
        contract.rewrite_template_max_chars(),
        contract.rewrite_proposal_max_chars(),
        contract.rewrite_survivor_min_chars(),
        contract.canary_entropy_bytes(),
        contract.canary_text_max_chars(),
        contract.canary_scan_overlap_chars(),
        contract.grounding_max_input_chars(),
        contract.output_ceiling_chars(),
        contract.exfil_allow_hosts_max(),
    )
    assert all(isinstance(value, int) and value > 0 for value in values)
    assert contract.grounding_wait_budget_s() > 0
    assert contract.rewrite_template_max_chars() == REWRITE_TEMPLATE_MAX_CHARS
    assert contract.canary_scan_overlap_chars() == 310


@pytest.mark.parametrize("p99", [1.0, 20.0, 400.0])
def test_contract_bounds_are_never_less_sensitive_than_the_default(p99: float) -> None:
    """D-30 clamp across three contract sizes: minimums >= default, scan allowances >= default."""
    bounds = _contract(p99).output_bounds()
    default = DEFAULT_OUTPUT_BOUNDS
    assert bounds.exfil_min_payload_chars >= default.exfil_min_payload_chars
    assert bounds.exfil_min_encoded_run >= default.exfil_min_encoded_run
    assert bounds.exfil_min_label_chars >= default.exfil_min_label_chars
    assert bounds.output_scan_candidate_cap >= default.output_scan_candidate_cap
    assert bounds.decode_expansion_bound >= default.decode_expansion_bound
    assert bounds.canary_scan_overlap_chars >= default.canary_scan_overlap_chars


def test_unset_and_degenerate_signals_fail_closed() -> None:
    with pytest.raises(CapacityUnset):
        _contract(float("nan")).exfil_min_payload_chars()
    with pytest.raises(CapacityUnset):
        _contract(float("inf")).output_bounds()
    with pytest.raises(CapacityUnavailable):
        _contract(0.0).output_scan_candidate_cap()
    with pytest.raises(CapacityUnavailable):
        OutputBounds(0, 1, 1, 1, 1, 1, 1)


def test_bounds_are_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_OUTPUT_BOUNDS.exfil_min_payload_chars = 1  # type: ignore[misc]
