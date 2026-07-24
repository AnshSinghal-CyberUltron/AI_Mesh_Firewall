"""External-MCP-proxy posture must be STRICTLY per-org — never inherited (2026-07-24).

Red-team finding LANE2-EXT-DEFAULT-INHERIT. ``_ext_proxy_enabled_info`` resolves the
transparent ext-proxy scan posture from ``FirewallConfig.mcp_ext_scan_action`` for the
calling org. Its docstring promises: an org that has NOT chosen a posture resolves to
observe-only ("tag"), because "enforcing something the operator did not choose is exactly
what this rule forbids".

The implementation broke that promise by reading through ``ConfigSync.get_config``, whose
documented fallback returns the ``default``/global config when the org is absent. So an org
that had never synced an ext posture SILENTLY INHERITED the platform/default org's
``mcp_ext_scan_action`` — enforcing redact/block on third-party MCP traffic the org operator
never selected. This is the exact "no by-defaults" violation the contract forbids.

Fix: ``_ext_proxy_enabled_info`` now reads ``ConfigSync.get_own_config`` (this org only, no
fallback); an unsynced org resolves to observe-only. This file locks BOTH halves: an org's
OWN selection is honored, and an unsynced org is NEVER handed another org's enforcing action.

Run: cd gateway && .venv/bin/python -m pytest \
    ai_mesh_gateway/tests/test_mcp_ext_posture_no_inherit.py -q -p no:cacheprovider
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mcp_proxy  # noqa: E402
from config_sync import ConfigSync  # noqa: E402


def _sync_with(default_action: str, **per_org: str) -> ConfigSync:
    """A ConfigSync whose global + 'default' org carry an ENFORCING ext posture, plus any
    named per-org postures. The global/default enforcement is the trap: a strict per-org
    read must NOT surface it for an org that never synced."""
    cs = ConfigSync("redis://unused", {"mcp_ext_scan_action": default_action})
    cs._config_by_org["default"] = {"mcp_ext_scan_action": default_action}
    for org, action in per_org.items():
        cs._config_by_org[org] = {"mcp_ext_scan_action": action}
    return cs


def _wire(cs: ConfigSync):
    """Patch _gateway_app_module to a stub module carrying our CONFIG_SYNC (hermetic — never
    touches the real gateway singletons)."""
    mod = types.ModuleType("_fake_gateway_main")
    mod.CONFIG_SYNC = cs
    return patch.object(mcp_proxy, "_gateway_app_module", return_value=mod)


def test_unsynced_org_is_observe_only_not_inherited():
    """The core fix: an org with no synced ext posture resolves to observe-only ``tag`` even
    when the default/global config selected an enforcing ``block``. Never inherit enforcement."""
    cs = _sync_with("block", acme="redact")
    with _wire(cs):
        assert mcp_proxy._ext_proxy_enabled_info("never-synced") == {"default_scan_action": "tag"}


def test_org_own_posture_is_honored():
    """Sovereignty is a real choice, not a euphemism for 'always tag': an org's OWN
    selection (redact) is honored, and the default org's own selection (block) is honored
    for the default org itself."""
    cs = _sync_with("block", acme="redact")
    with _wire(cs):
        assert mcp_proxy._ext_proxy_enabled_info("acme") == {"default_scan_action": "redact"}
        assert mcp_proxy._ext_proxy_enabled_info("default") == {"default_scan_action": "block"}


def test_own_posture_tag_stays_observe_only():
    """An org that explicitly synced ``tag`` is observe-only, indistinguishable in effect from
    an unsynced org — both forward, neither inherits the default's enforcement."""
    cs = _sync_with("block", acme="tag")
    with _wire(cs):
        assert mcp_proxy._ext_proxy_enabled_info("acme") == {"default_scan_action": "tag"}


def test_get_own_config_never_falls_back():
    """Unit-level guard on the accessor the fix introduced: get_own_config returns the org's
    own dict or None, and NEVER the default/global fallback that get_config returns."""
    cs = _sync_with("block", acme="redact")
    assert cs.get_own_config("acme") == {"mcp_ext_scan_action": "redact"}
    assert cs.get_own_config("never-synced") is None
    # Contrast: the lenient lookup DOES leak the default/global posture.
    assert cs.get_config("never-synced") == {"mcp_ext_scan_action": "block"}


def test_no_org_slug_is_observe_only():
    """Unauthenticated transport (no org) has no selection to honor -> observe-only."""
    cs = _sync_with("block")
    with _wire(cs):
        assert mcp_proxy._ext_proxy_enabled_info("") is None or \
            mcp_proxy._ext_proxy_enabled_info("") == {"default_scan_action": "tag"}
