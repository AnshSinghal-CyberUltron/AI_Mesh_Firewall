"""Load the control-tree modules by file path, with no import of ``ai_mesh_control``."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

CONTROL = Path(__file__).resolve().parents[3] / "control" / "ai_mesh_control" / "core"
EVIDENCE = Path(__file__).resolve().parents[3] / "docs" / "plans" / "evidence" / "2026-10-27-gw27"
_NAMES = (
    "firewall_v2_map",
    "firewall_v2_controls",
    "v2_audit_adapter",
    "compliance",
    "firewall_v2_publish",
)


def load() -> types.SimpleNamespace:
    """A fresh copy of the control modules under a shim ``core`` package."""
    package = types.ModuleType("core")
    package.__path__ = [str(CONTROL)]  # type: ignore[attr-defined]
    sys.modules["core"] = package
    loaded: dict[str, Any] = {}
    for name in _NAMES:
        spec = importlib.util.spec_from_file_location(f"core.{name}", CONTROL / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        loaded[name] = module
    return types.SimpleNamespace(
        map=loaded["firewall_v2_map"],
        controls=loaded["firewall_v2_controls"],
        publish=loaded["firewall_v2_publish"],
        compliance=loaded["compliance"],
    )
