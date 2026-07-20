"""Regression: MCP policy eval must read POLICY_SYNC from ai_mesh_gateway.main."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock


def test_get_policy_sync_prefers_ai_mesh_gateway_main_module():
    from mcp_scan_orchestrator import _get_policy_sync

    sentinel = object()
    fake_main = types.ModuleType("ai_mesh_gateway.main")
    fake_main.POLICY_SYNC = sentinel
    legacy_main = types.ModuleType("main")
    legacy_main.POLICY_SYNC = None

    sys.modules["ai_mesh_gateway.main"] = fake_main
    sys.modules["main"] = legacy_main
    try:
        assert _get_policy_sync() is sentinel
    finally:
        sys.modules.pop("ai_mesh_gateway.main", None)
        sys.modules.pop("main", None)


def test_get_policy_sync_falls_back_to_main_when_only_legacy_loaded():
    from mcp_scan_orchestrator import _get_policy_sync

    sync = MagicMock()
    legacy_main = types.ModuleType("main")
    legacy_main.POLICY_SYNC = sync
    sys.modules.pop("ai_mesh_gateway.main", None)
    sys.modules["main"] = legacy_main
    try:
        assert _get_policy_sync() is sync
    finally:
        sys.modules.pop("main", None)
