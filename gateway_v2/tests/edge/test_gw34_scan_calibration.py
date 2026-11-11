"""GW34 boot scan calibration: one host measurement, a floor, finite inputs, a shared result.

Review of PR #100 (Reviewer C): a calibration taken under boot-time load could size a bound
below what a realistic tool request needs (a 400 for the life of the worker), a non-finite p99
target hung boot, and workers could each serve a different bound.
"""
from __future__ import annotations

import math
import types

import pytest

from gateway_v2.detect.deterministic import BASE_DETECTORS
from gateway_v2.detect.deterministic.detectors import (
    InjectionSignalDetector,
    PiiDetector,
    SecretDetector,
)
from gateway_v2.edge import scan_calibration
from gateway_v2.edge.chat_deps import resolve_tool_bounds
from gateway_v2.edge.scan_calibration import (
    CALIBRATION_SLOTS,
    SCAN_COST_ENV,
    SCAN_COST_SOURCE_ENV,
    export_for_workers,
    measure_scan_slot_cost_us,
    scan_sizing,
    scanned_slots_floor,
)
from gateway_v2.edge.wire.openai_template import JOINED_CHANNEL_ORDER
from gateway_v2.runtime import resources
from gateway_v2.runtime.errors import CapacityUnavailable
from gateway_v2.runtime.resources import load_contract

_TICK = 2.0**-10  # exactly representable, so elapsed-time arithmetic has no rounding


def _contract() -> resources.ResourceContract:
    return load_contract()[0]


def _fake_clock(monkeypatch: pytest.MonkeyPatch, *, ticks_per_round: int = 1) -> list[str]:
    """Replace the detectors and the clock: each round of the 4 slots advances the clock."""
    calls: list[str] = []
    counter = iter(range(10_000_000))

    def fake_scan(text: str, _budget_s: float) -> int:
        calls.append(text)
        return 0

    def perf_counter() -> float:
        # One clock read per round (after the 4 slots), plus one per pass start.
        return next(counter) * _TICK * ticks_per_round

    monkeypatch.setattr(scan_calibration, "scan_slot", fake_scan)
    monkeypatch.setattr(scan_calibration, "time", types.SimpleNamespace(perf_counter=perf_counter))
    return calls


def test_the_floor_covers_every_joined_channel_with_the_design_headroom() -> None:
    floor = scanned_slots_floor()
    assert floor == math.ceil(len(JOINED_CHANNEL_ORDER) / resources._SCAN_SLOT_HEADROOM)
    assert len(JOINED_CHANNEL_ORDER) <= resources._SCAN_SLOT_HEADROOM * floor


def test_calibration_and_serving_time_the_same_detector_set() -> None:
    assert BASE_DETECTORS == (PiiDetector, SecretDetector, InjectionSignalDetector)
    found = scan_calibration.scan_slot(CALIBRATION_SLOTS[1], 1.0)  # the secret-shaped token
    assert found >= 1


@pytest.mark.parametrize("window", [float("inf"), float("nan"), 0.0, -1.0])
def test_a_non_finite_or_empty_window_refuses_instead_of_hanging(window: float) -> None:
    with pytest.raises(CapacityUnavailable):
        measure_scan_slot_cost_us.__wrapped__(0.05, window, 1)


def test_a_huge_window_is_capped_so_boot_cost_stays_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_clock(monkeypatch)
    measure_scan_slot_cost_us.__wrapped__(0.05, 1e9, 1)  # target_p99 of ~2e9 ms
    rounds_per_pass = math.ceil(resources._SCAN_CALIBRATION_MAX_PASS_S / _TICK)
    passes = resources._SCAN_CALIBRATION_PASSES
    assert len(calls) == 1 + passes * rounds_per_pass * len(CALIBRATION_SLOTS)


def test_a_bound_below_the_floor_is_re_measured_and_the_cheapest_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_clock(monkeypatch)
    window = 10 * _TICK
    one = 1 + resources._SCAN_CALIBRATION_PASSES * 10 * len(CALIBRATION_SLOTS)
    measure_scan_slot_cost_us.__wrapped__(0.05, window, 1)  # bound 26 >= 1: no retry
    assert len(calls) == one
    calls.clear()
    measure_scan_slot_cost_us.__wrapped__(0.05, window, 10_000)  # never reachable: all retries
    assert len(calls) == one * (1 + resources._SCAN_CALIBRATION_RETRIES)


def test_a_declared_cost_is_used_without_measuring(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_clock(monkeypatch)
    c = _contract()
    sizing = scan_sizing(c, {SCAN_COST_ENV: "250.5"})
    assert sizing is not None
    assert sizing.cost_us == 250.5 and sizing.source == f"{SCAN_COST_ENV} (pinned)"
    assert calls == []
    assert sizing.served_slots == c.tool_bounds(
        slot_cost_us=250.5, scanned_slots_floor=scanned_slots_floor()
    ).max_scanned_slots


@pytest.mark.parametrize("raw", ["abc", "inf", "nan", "0", "-3"])
def test_an_invalid_declared_cost_refuses_to_start(raw: str) -> None:
    with pytest.raises(CapacityUnavailable):
        scan_sizing(_contract(), {SCAN_COST_ENV: raw})


def test_a_slow_host_serves_the_floor_and_says_so(caplog: pytest.LogCaptureFixture) -> None:
    c = _contract()
    sizing = scan_sizing(c, {SCAN_COST_ENV: "2000"})  # 2 ms per slot: 5 slots, below the floor
    assert sizing is not None
    assert sizing.calibrated_slots < sizing.floor == sizing.served_slots
    assert sizing.clamped
    assert "CLAMPED from" in sizing.boot_line()
    assert any("CLAMPED" in record.getMessage() for record in caplog.records)


def test_the_served_bound_is_floored_on_a_slow_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review of #104: pin the cost, so the served-path floor is tested on every host."""
    monkeypatch.setenv(SCAN_COST_ENV, "2000")  # 2 ms per slot: the calibrated bound is 5
    served = resolve_tool_bounds(_contract())
    assert served is not None
    assert served.max_scanned_slots == scanned_slots_floor()


def test_a_measured_cost_that_is_not_finite_and_positive_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for bad in (0.0, float("nan"), float("inf"), -1.0):
        monkeypatch.setattr(scan_calibration, "_measure_once", lambda _b, _p, v=bad: v)
        with pytest.raises(CapacityUnavailable):
            measure_scan_slot_cost_us.__wrapped__(0.05, 0.01, 1)


def test_the_cheapest_re_measurement_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    costs = iter([5_000.0, 900.0, 2_000.0])
    monkeypatch.setattr(scan_calibration, "_measure_once", lambda _b, _p: next(costs))
    assert measure_scan_slot_cost_us.__wrapped__(0.05, 0.01, 1_000) == 900.0


def test_a_fractional_p99_does_not_report_a_false_clamp() -> None:
    import dataclasses

    c = dataclasses.replace(_contract(), target_p99_ms=500.5)
    sizing = scan_sizing(c, {SCAN_COST_ENV: "1"})
    assert sizing is not None and not sizing.clamped


def test_the_clamp_warning_is_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    scan_calibration._warn_clamped.cache_clear()
    c = _contract()
    for _ in range(3):
        scan_sizing(c, {SCAN_COST_ENV: "2500"})
    assert sum("CLAMPED" in r.getMessage() for r in caplog.records) == 1


def test_main_exports_the_calibration_for_its_workers() -> None:
    import inspect

    from gateway_v2.edge import serve

    source = inspect.getsource(serve.main)
    assert "export_for_workers(contract)" in source
    assert source.index("_assemble_app(deps)") < source.index("export_for_workers(contract)")


@pytest.mark.parametrize("raw", ["100000", "1e-320", "5e-324"])
def test_a_cost_that_fits_no_slot_or_overflows_refuses_with_a_clear_message(raw: str) -> None:
    """Reviewer B on #104: an absurd SLO or cost is a mis-configuration, not a busy host."""
    with pytest.raises(CapacityUnavailable, match="AMF_"):
        scan_sizing(_contract(), {SCAN_COST_ENV: raw})


def test_the_clamp_line_names_the_remedy() -> None:
    sizing = scan_sizing(_contract(), {SCAN_COST_ENV: "2000"})
    assert sizing is not None and sizing.clamped
    # floor 12 x 2000 us / (1000 x 0.5) = 48 ms
    assert "AMF_TARGET_P99_MS >= 48" in sizing.boot_line()


def test_the_source_label_tells_inherited_from_pinned() -> None:
    c = _contract()
    inherited = scan_sizing(c, {SCAN_COST_ENV: "300", SCAN_COST_SOURCE_ENV: "parent"})
    pinned = scan_sizing(c, {SCAN_COST_ENV: "300"})
    assert inherited is not None and pinned is not None
    assert "inherited from the parent" in inherited.boot_line()
    assert "(pinned)" in pinned.boot_line()


def test_a_fast_host_is_not_clamped() -> None:
    sizing = scan_sizing(_contract(), {SCAN_COST_ENV: "1"})
    assert sizing is not None and not sizing.clamped
    assert sizing.served_slots == sizing.calibrated_slots > sizing.floor
    assert "CLAMPED" not in sizing.boot_line()
    assert f"max_scanned_slots={sizing.served_slots}" in sizing.boot_line()


def test_the_parent_exports_one_cost_and_workers_serve_the_same_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = _contract()
    env: dict[str, str] = {}
    monkeypatch.setattr(
        scan_calibration, "measure_scan_slot_cost_us", lambda _b, _w, _f: 321.0
    )
    export_for_workers(c, env)
    assert env == {SCAN_COST_ENV: repr(321.0), SCAN_COST_SOURCE_ENV: "parent"}
    first = scan_sizing(c, env)
    monkeypatch.setattr(
        scan_calibration, "measure_scan_slot_cost_us", lambda _b, _w, _f: 9_999.0
    )
    second = scan_sizing(c, env)  # a "worker" with a different load reads the shared value
    assert first == second


def test_an_operator_pin_is_never_overwritten() -> None:
    env = {SCAN_COST_ENV: "400"}
    export_for_workers(_contract(), env)
    assert env == {SCAN_COST_ENV: "400"}


def test_a_contract_without_tool_bounds_has_no_sizing() -> None:
    assert scan_sizing(types.SimpleNamespace()) is None
    assert resolve_tool_bounds(types.SimpleNamespace()) is None
