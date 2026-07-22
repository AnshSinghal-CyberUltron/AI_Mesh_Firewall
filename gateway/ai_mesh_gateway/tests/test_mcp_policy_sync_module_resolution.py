"""Regression: MCP policy eval must read POLICY_SYNC from ai_mesh_gateway.main."""

from __future__ import annotations

import contextlib
import sys
import types
from unittest.mock import MagicMock


@contextlib.contextmanager
def _swapped_main_modules(pkg_main, bare_main):
    """Install stub ``ai_mesh_gateway.main`` / ``main`` modules, then RESTORE them.

    SUITE-WIDE POLLUTION HAZARD (fixed 2026-07-21). These tests used to tear down with
    ``sys.modules.pop(...)``. They only ever INSERTED stubs, so the pop EVICTED the
    genuine, already-imported ``ai_mesh_gateway.main``. A later runtime
    ``import ai_mesh_gateway.main`` then RE-EXECUTED main.py into a SECOND module object
    with its own ``app`` / ``CONFIG`` / ``CONFIG_SYNC`` / ``OUTPUT_GUARD`` / ``LLM_ROUTER``
    and overwrote the parent-package attribute. Tests that bound the module at COLLECTION
    time were then patching module #1 while the app under test ran on module #2 — causing
    failures like "upstream was never reached" and "'NoneType' object has no attribute
    'acompletion'" across ~10 unrelated files, and ONLY in a full-suite run (the minimal
    repro needed this file plus two others that trigger the runtime dotted re-import).

    Restoring the previous entries — including the parent-package ATTRIBUTE, which
    ``import x.y as z`` binds and which does NOT track ``sys.modules`` — keeps module
    identity stable for every other test.

    Pass ``None`` for a slot to assert the ABSENT case (the legacy-fallback test).
    """
    keys = ("ai_mesh_gateway.main", "main")
    saved = {k: sys.modules.get(k) for k in keys}
    parent = sys.modules.get("ai_mesh_gateway")
    had_attr = parent is not None and hasattr(parent, "main")
    saved_attr = getattr(parent, "main", None) if parent is not None else None
    try:
        for key, stub in (("ai_mesh_gateway.main", pkg_main), ("main", bare_main)):
            if stub is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = stub
        if parent is not None and pkg_main is not None:
            parent.main = pkg_main
        yield
    finally:
        for key in keys:
            prior = saved[key]
            if prior is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = prior
        if parent is not None:
            if had_attr:
                parent.main = saved_attr
            else:
                with contextlib.suppress(AttributeError):
                    del parent.main


def test_get_policy_sync_prefers_ai_mesh_gateway_main_module():
    from mcp_scan_orchestrator import _get_policy_sync

    sentinel = object()
    fake_main = types.ModuleType("ai_mesh_gateway.main")
    fake_main.POLICY_SYNC = sentinel
    legacy_main = types.ModuleType("main")
    legacy_main.POLICY_SYNC = None

    with _swapped_main_modules(fake_main, legacy_main):
        assert _get_policy_sync() is sentinel


def test_get_policy_sync_falls_back_to_main_when_only_legacy_loaded():
    from mcp_scan_orchestrator import _get_policy_sync

    sync = MagicMock()
    legacy_main = types.ModuleType("main")
    legacy_main.POLICY_SYNC = sync

    # pkg_main=None asserts the fallback path: only the legacy ``main`` is loaded.
    with _swapped_main_modules(None, legacy_main):
        assert _get_policy_sync() is sync
