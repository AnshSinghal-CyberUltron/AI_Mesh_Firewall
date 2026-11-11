"""GW34 Task 3: ToolBounds derivation, fail-closed, calibration; channel table."""
from __future__ import annotations

import dataclasses
import inspect
import time
import types

import pytest

from gateway_v2.domain import Phase
from gateway_v2.domain.channels import (
    BLOCK_ONLY_CHANNELS,
    META_CHANNELS,
    TEXT_CHANNELS,
    unscanned_channels,
)
from gateway_v2.edge.chat_deps import resolve_tool_bounds
from gateway_v2.edge.dev_components import DeterministicFindings
from gateway_v2.edge.scan_calibration import (
    CALIBRATION_SLOTS,
    measure_scan_slot_cost_us,
    scanned_slots_floor,
)
from gateway_v2.runtime import resources, resources_tools
from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.resources import load_contract


def _contract() -> resources.ResourceContract:
    return load_contract()[0]


def test_channel_table_has_the_gw34_channels_all_scanned() -> None:
    names = {channel.name: channel for channel in TEXT_CHANNELS}
    for name in (
        "tool_description", "tool_schema_text", "response_format_schema", "tool_arguments_raw",
        "tool_name", "tool_call_id", "tool_schema_struct", "output_tool_id", "output_tool_name",
        "logprobs",
    ):
        assert names[name].scanned, name
    assert unscanned_channels() == ()
    assert META_CHANNELS == frozenset({"finish_reason"})
    assert BLOCK_ONLY_CHANNELS <= set(names)
    assert not META_CHANNELS & set(names)


def test_derivation_matches_the_design_table() -> None:
    c = _contract()
    b = c.tool_bounds()
    assert b.max_tools == resources._MAX_TOOLS
    assert b.max_tool_calls == resources._MAX_TOOL_CALLS
    assert b.schema_depth == resources._SCHEMA_DEPTH
    assert b.schema_nodes == c.max_request_bytes() // resources._SCHEMA_NODE_BYTES
    assert b.arguments_bytes == c.max_request_bytes() // resources._TOOL_ARGUMENTS_DIVISOR
    assert b.parts_allowance == resources._PARTS_PER_SCHEMA_NODE * b.schema_nodes
    expected = int(
        c.target_p99_ms * 1000 * resources._SCAN_BUDGET_FRACTION / resources._SCAN_SLOT_COST_US
    )
    assert b.max_scanned_slots == expected
    assert b.max_withheld_bytes == c.stream_buffer_bytes(c.connection_budget())
    assert dataclasses.is_dataclass(b) and b.__dataclass_params__.frozen  # type: ignore[attr-defined]
    # max_request_parts() is unchanged by GW34
    assert c.max_request_parts() == c.workers() * resources._MAX_REQUEST_PARTS_MULT


def test_fail_closed_when_any_bound_is_below_one() -> None:
    c = _contract()
    tiny = dataclasses.replace(c, target_p99_ms=0.001)  # zero scan slots
    with pytest.raises(CapacityUnavailable):
        tiny.tool_bounds()


def test_the_mixin_module_holds_no_numeric_capacity_literal() -> None:
    src = inspect.getsource(resources_tools)
    body = src.split('"""', 2)[2]
    for token in ("= 128", "= 64", "= 32", "= 500"):
        assert token not in body


def test_calibration_max_scanned_slots_cost_less_than_the_scan_budget() -> None:
    """The calibrated max_scanned_slots worst-case slots scan within target_p99 x fraction.

    The bound is sized from THIS host's measured cost (edge/scan_calibration.py), exactly as
    `build_app` sizes it, so the property holds on any hardware rather than on the one VM a
    constant was measured on. The cost is measured afresh (not the per-process cache) right
    before the timed scan, so both see the same host load: under pytest-xdist the load an earlier
    test calibrated under says nothing about the load now.
    """
    c = _contract()
    cost_us = measure_scan_slot_cost_us.__wrapped__(
        c.guard_wait_budget_s(), c.scan_window_s(), scanned_slots_floor()
    )
    slots = c.tool_bounds(slot_cost_us=cost_us).max_scanned_slots  # calibrated, before any clamp
    detector = DeterministicFindings(phase=Phase.INPUT, budget_s=1.0)
    texts = list(CALIBRATION_SLOTS) * (slots // len(CALIBRATION_SLOTS) + 1)
    texts = texts[:slots]
    detector(texts[0])  # warm
    best = min(
        _time_pass(detector, texts) for _ in range(5)
    )
    assert best < c.scan_window_s()


def test_the_scan_window_is_the_scan_share_of_p99() -> None:
    c = _contract()
    expected = c.target_p99_ms / 1000 * resources._SCAN_BUDGET_FRACTION
    assert c.scan_window_s() == pytest.approx(expected)


def test_calibration_passes_last_one_scan_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each timed pass scans until a full window has elapsed, never a fixed handful of slots."""
    from gateway_v2.edge import scan_calibration

    calls: list[str] = []

    def fake_scan(text: str, _budget_s: float) -> int:
        calls.append(text)
        return 0

    monkeypatch.setattr(scan_calibration, "scan_slot", fake_scan)
    tick = 2.0**-10  # exactly representable, so elapsed-time arithmetic has no rounding
    clock = iter(n * tick for n in range(100_000))
    fake_time = types.SimpleNamespace(perf_counter=lambda: next(clock))
    monkeypatch.setattr(scan_calibration, "time", fake_time)
    rounds_per_pass = 10  # the fake clock advances one tick per round
    cost = measure_scan_slot_cost_us.__wrapped__(0.05, rounds_per_pass * tick, 1)
    passes = resources._SCAN_CALIBRATION_PASSES
    slots_per_pass = rounds_per_pass * len(CALIBRATION_SLOTS)
    assert len(calls) == 1 + passes * slots_per_pass  # one warm-up slot, then full windows
    per_slot_us = rounds_per_pass * tick / slots_per_pass * 1_000_000
    assert cost == pytest.approx(per_slot_us * resources._SCAN_COST_MARGIN)


def _time_pass(detector: DeterministicFindings, texts: list[str]) -> float:
    start = time.perf_counter()
    for text in texts:
        detector(text)
    return time.perf_counter() - start


def test_a_measured_slot_cost_replaces_the_declared_one() -> None:
    c = _contract()
    cheap = c.tool_bounds(slot_cost_us=resources._SCAN_SLOT_COST_US / 2).max_scanned_slots
    declared = c.tool_bounds().max_scanned_slots
    dear = c.tool_bounds(slot_cost_us=resources._SCAN_SLOT_COST_US * 2).max_scanned_slots
    assert dear < declared < cheap


@pytest.mark.parametrize("cost", [0.0, -1.0, float("nan"), float("inf")])
def test_a_non_finite_or_non_positive_slot_cost_fails_closed(cost: float) -> None:
    with pytest.raises(CapacityUnavailable):
        _contract().tool_bounds(slot_cost_us=cost)


def test_the_floor_raises_a_low_bound_and_never_lowers_a_high_one() -> None:
    c = _contract()
    dear = c.tool_bounds(slot_cost_us=resources._SCAN_SLOT_COST_US * 10).max_scanned_slots
    assert c.tool_bounds(
        slot_cost_us=resources._SCAN_SLOT_COST_US * 10, scanned_slots_floor=dear + 5
    ).max_scanned_slots == dear + 5
    declared = c.tool_bounds().max_scanned_slots
    assert c.tool_bounds(scanned_slots_floor=1).max_scanned_slots == declared


def test_build_app_bounds_are_the_served_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AMF_SCAN_SLOT_COST_US", raising=False)
    c = _contract()
    floor = scanned_slots_floor()
    cost_us = measure_scan_slot_cost_us(c.guard_wait_budget_s(), c.scan_window_s(), floor)
    expected = c.tool_bounds(slot_cost_us=cost_us, scanned_slots_floor=floor)
    assert resolve_tool_bounds(c) == expected
    assert expected.max_scanned_slots >= floor


@pytest.mark.parametrize("p99", [float("inf"), float("nan"), 0.0, -5.0])
def test_a_non_finite_or_non_positive_p99_target_refuses_the_contract(p99: float) -> None:
    signals = resources.HardwareSignals(
        cpu_quota=2.0, memory_limit=8 << 30, fd_limit=20_000,
        cpu_source="affinity", mem_source="meminfo", fd_source="rlimit",
    )
    with pytest.raises(CapacityUnavailable):
        resources.from_signals(
            signals, target_p99_ms=p99, utilization_cap=0.75, per_worker_rss=400 << 20
        )
