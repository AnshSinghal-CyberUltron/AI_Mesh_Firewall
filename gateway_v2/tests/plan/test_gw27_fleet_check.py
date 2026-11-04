"""GW27 D-10: scripts/check_fleet_plan_schema.py against canned exposition text (no live scrape)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_fleet_plan_schema.py"
_OK = "# TYPE amf_state_plan_schema_version gauge\namf_state_plan_schema_version 1.0\n"
_OLD = "# TYPE amf_state_rounds_total counter\namf_state_rounds_total 4\n"
_LOW = "amf_state_plan_schema_version 0\n"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_fleet_plan_schema", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_version() -> None:
    mod = _script()
    assert mod.parse_version(_OK) == 1 and mod.parse_version(_LOW) == 0
    assert mod.parse_version(_OLD) is None
    assert mod.parse_version('x_plan_schema_version{zone="a"} 2') == 2


def test_every_replica_must_export_a_version_of_at_least_one() -> None:
    mod = _script()
    assert mod.evaluate({"a": _OK, "b": _OK}) == []
    problems = mod.evaluate({"a": _OK, "b": _OLD, "c": _LOW, "d": None})
    assert len(problems) == 3
    assert any("b" in p and "older gateway" in p for p in problems)
    assert any("c" in p and "< 1" in p for p in problems)
    assert any("d" in p and "unreachable" in p for p in problems)
    assert mod.evaluate({}) != []  # an empty fleet is never certified


def test_main_exit_codes() -> None:
    mod = _script()
    pages = {"http://a": _OK, "http://b": _OLD}
    assert mod.main(["http://a"], fetcher=pages.get) == 0
    assert mod.main(["http://a", "http://b"], fetcher=pages.get) == 1
    assert mod.main(["http://missing"], fetcher=pages.get) == 1
    assert mod.main([], fetcher=pages.get) == 1
