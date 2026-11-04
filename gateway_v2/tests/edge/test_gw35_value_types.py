"""GW35: new value types are frozen and slotted; the gate is slotted; no module-level mutable."""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

from gateway_v2.edge.console_stages import ChatFacts, StageOutcome
from gateway_v2.edge.unauth_audit import UnauthAuditCounters, UnauthAuditGate

_ROOT = Path(__file__).resolve().parents[2] / "gateway_v2"
_NEW_MODULES = (
    "domain/failure_class.py",
    "edge/console_stages.py",
    "edge/produced_holder.py",
    "edge/unauth_audit.py",
    "runtime/resources_audit.py",
)


@pytest.mark.parametrize("cls", [ChatFacts, StageOutcome])
def test_value_types_are_frozen_and_slotted(cls: type) -> None:
    assert dataclasses.is_dataclass(cls)
    params = cls.__dataclass_params__  # type: ignore[attr-defined]
    assert params.frozen
    assert "__slots__" in vars(cls)
    instance = ChatFacts() if cls is ChatFacts else StageOutcome("auth", "allow", True)
    field = dataclasses.fields(cls)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, field, "x")


def test_the_gate_and_counters_have_slots_and_no_dict() -> None:
    gate = UnauthAuditGate(rate_per_window=1, window_s=1.0, clock=lambda: 0.0)
    assert not hasattr(gate, "__dict__") and not hasattr(UnauthAuditCounters(gate), "__dict__")


@pytest.mark.parametrize("module", _NEW_MODULES)
def test_no_module_level_mutable_container(module: str) -> None:
    tree = ast.parse((_ROOT / module).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            assert not isinstance(value, (ast.List, ast.Dict, ast.Set)), (module, node.lineno)
