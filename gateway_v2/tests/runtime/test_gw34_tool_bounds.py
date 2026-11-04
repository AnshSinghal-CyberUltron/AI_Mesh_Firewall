"""GW34 Task 3: ToolBounds derivation, fail-closed, calibration; channel table."""
from __future__ import annotations

import dataclasses
import inspect
import time

import pytest

from gateway_v2.domain import Phase
from gateway_v2.domain.channels import (
    BLOCK_ONLY_CHANNELS,
    META_CHANNELS,
    TEXT_CHANNELS,
    unscanned_channels,
)
from gateway_v2.edge.dev_components import DeterministicFindings
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
    """max_scanned_slots short worst-case slots must scan within target_p99 x fraction."""
    c = _contract()
    slots = c.tool_bounds().max_scanned_slots
    detector = DeterministicFindings(phase=Phase.INPUT, budget_s=1.0)
    texts = ["ignore_previous_instr", "AKIA" + "0" * 16, "get_current_weather_x", "a" * 24] * (
        slots // 4 + 1
    )
    texts = texts[:slots]
    detector(texts[0])  # warm
    best = min(
        _time_pass(detector, texts) for _ in range(5)
    )
    assert best < c.target_p99_ms / 1000 * resources._SCAN_BUDGET_FRACTION


def _time_pass(detector: DeterministicFindings, texts: list[str]) -> float:
    start = time.perf_counter()
    for text in texts:
        detector(text)
    return time.perf_counter() - start
