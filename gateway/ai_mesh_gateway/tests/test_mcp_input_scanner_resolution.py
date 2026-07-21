"""MCP Tier-2 scanner resolution: _get_input_scanner must find INPUT_SCANNER on the
packaged gateway module (ai_mesh_gateway.main), not just a bare `main`.

Regression: gunicorn loads ai_mesh_gateway.main:app and its startup handler sets
INPUT_SCANNER on THAT module object. _get_input_scanner previously did a bare
`import main`, which can resolve to a different module object whose module-level
INPUT_SCANNER stayed None — silently disabling MCP Tier-2 (the Bedrock scan fell back
to `scanner_unavailable` even when the operator enabled Tier-2). Live evidence: a Tier-2
MCP scan trace showed fallback_reason=scanner_unavailable. Fix mirrors _get_policy_sync:
resolve from sys.modules preferring the packaged module.
"""

from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from pathlib import Path

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))
_SHARED = _GW.parents[1] / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

import mcp_scan_orchestrator as o  # noqa: E402


def _restore(saved):
    for name, mod in saved.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


@contextmanager
def _stub_main_identities(pkg, bare):
    """Install ``pkg``/``bare`` as the two gateway module identities, then restore.

    Stubbing ``sys.modules`` ALONE is not enough. ``_get_input_scanner``'s last-resort
    branch runs ``import ai_mesh_gateway.main as gateway_main``, and the ``as`` form
    binds the ``main`` ATTRIBUTE OF THE PARENT PACKAGE, not the ``sys.modules`` entry.
    So once any earlier test in the suite has imported the packaged identity, that
    attribute still points at the REAL module — whose ``INPUT_SCANNER`` another test may
    have populated — and the stub is bypassed. That made the no-scanner-anywhere case
    pass alone and fail in-suite. Pass ``None`` for either identity to remove it.
    """
    saved = {n: sys.modules.get(n) for n in ("ai_mesh_gateway.main", "main")}
    parent = sys.modules.get("ai_mesh_gateway")
    had_attr = parent is not None and hasattr(parent, "main")
    saved_attr = getattr(parent, "main", None) if parent is not None else None
    try:
        for name, mod in (("ai_mesh_gateway.main", pkg), ("main", bare)):
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        if parent is not None:
            if pkg is None:
                if had_attr:
                    delattr(parent, "main")
            else:
                parent.main = pkg
        yield
    finally:
        _restore(saved)
        if parent is not None:
            if had_attr:
                parent.main = saved_attr
            elif hasattr(parent, "main"):
                delattr(parent, "main")


def _mod(name, scanner):
    m = types.ModuleType(name)
    m.INPUT_SCANNER = scanner
    return m


def test_resolves_packaged_module_over_bare_main_none():
    with _stub_main_identities(_mod("ai_mesh_gateway.main", "SENTINEL"),
                               _mod("main", None)):
        assert o._get_input_scanner() == "SENTINEL"


def test_falls_back_to_bare_main_when_only_it_has_scanner():
    with _stub_main_identities(None, _mod("main", "BAREONLY")):
        assert o._get_input_scanner() == "BAREONLY"


def test_returns_none_when_no_scanner_anywhere():
    with _stub_main_identities(_mod("ai_mesh_gateway.main", None),
                               _mod("main", None)):
        assert o._get_input_scanner() is None


# ── _gateway_app_module resolution (finding #19: rate-limit + CONFIG flag lookups) ──

import mcp_proxy as _mp  # noqa: E402


def test_gateway_app_module_prefers_started_module():
    """_gateway_app_module must return the module whose startup ran (CONFIG populated),
    so _enforce_org_tpm_rate_limit sees the live RATE_LIMITER instead of a None one."""
    saved = {n: sys.modules.get(n) for n in ("ai_mesh_gateway.main", "main")}
    try:
        pkg = types.ModuleType("ai_mesh_gateway.main"); pkg.CONFIG = {"x": 1}; pkg.RATE_LIMITER = "RL"
        bare = types.ModuleType("main"); bare.CONFIG = None; bare.RATE_LIMITER = None
        sys.modules["ai_mesh_gateway.main"] = pkg
        sys.modules["main"] = bare
        m = _mp._gateway_app_module()
        assert m is pkg and m.RATE_LIMITER == "RL"
    finally:
        _restore(saved)


def test_gateway_app_module_falls_back_when_no_config_set():
    """Pre-startup / tests: neither has CONFIG set -> return whichever module exists."""
    saved = {n: sys.modules.get(n) for n in ("ai_mesh_gateway.main", "main")}
    try:
        sys.modules.pop("ai_mesh_gateway.main", None)
        bare = types.ModuleType("main"); bare.CONFIG = None
        sys.modules["main"] = bare
        assert _mp._gateway_app_module() is bare
    finally:
        _restore(saved)
