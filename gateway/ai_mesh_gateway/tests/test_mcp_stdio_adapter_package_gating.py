"""CHG-0126: the gateway host-side stdio adapter's package extraction only recognized
the SPACE-separated ``--from/--with/--package/-p <pkg>`` form and returned a SINGLE spec.
The ``--flag=value`` (equals) form and a 2nd package flag slipped past the allowlist /
pinned-version guards — a supply-chain BYPASS that could pull an unlisted/unpinned npm/PyPI
package onto the SHARED GATEWAY HOST ("no unknown npm on host"). Parity with the sandbox
agent (services/mcp-broker/sandbox-image/agent/stdio_manager.py) which has the same fix.
"""
from __future__ import annotations

import sys
from pathlib import Path

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))

import mcp_stdio_adapter as A  # noqa: E402


# ── extraction: the fixed helper returns EVERY fetched spec ──────────────────

def test_space_form_single_pkg_unchanged():
    assert A._extract_package_specs("npx", ["-y", "mcp-remote", "https://x"]) == ["mcp-remote"]
    assert A._extract_package_spec("npx", ["-y", "ruflo@1.2.3", "mcp"]) == "ruflo@1.2.3"


def test_equals_form_extracted():
    # npx --package=evil safe-cmd : the FETCHED package is evil, not the command.
    assert A._extract_package_specs("npx", ["--package=evil", "safe-cmd"]) == ["evil"]
    assert A._extract_package_specs("uvx", ["--from=semgrep-mcp==1.0", "semgrep"]) == ["semgrep-mcp==1.0"]


def test_multiple_flags_all_extracted():
    assert A._extract_package_specs("npx", ["-p", "a", "-p", "evil", "cmd"]) == ["a", "evil"]


def test_positional_after_pkg_flag_is_command_not_package():
    assert A._extract_package_specs("npx", ["-p", "realpkg", "runcmd"]) == ["realpkg"]


def test_non_fetching_interpreter_returns_empty():
    assert A._extract_package_specs("node", ["-e", "x"]) == []
    assert A._extract_package_specs("python3", ["-m", "m"]) == []


# ── enforcement: the equals-form / 2nd-flag package is actually blocked ───────

def _reload_with_allowlist(monkeypatch, allowlist):
    import importlib
    monkeypatch.setenv("MCP_STDIO_PACKAGE_ALLOWLIST", allowlist)
    monkeypatch.delenv("MCP_STDIO_REQUIRE_PINNED_PACKAGES", raising=False)
    sys.modules.pop("mcp_stdio_adapter", None)
    return importlib.import_module("mcp_stdio_adapter")


def test_allowlist_blocks_equals_form_smuggled_package(monkeypatch):
    m = _reload_with_allowlist(monkeypatch, "safe-cmd,mcp-remote")
    # Enforce directly via the pure helpers (no subprocess): the smuggled pkg is caught.
    specs = m._extract_package_specs("npx", ["--package=evil-pkg", "safe-cmd"])
    blocked = [s for s in specs if m._package_name(s) not in m._PACKAGE_ALLOWLIST]
    assert blocked == ["evil-pkg"]


def test_allowlist_blocks_second_package_flag(monkeypatch):
    m = _reload_with_allowlist(monkeypatch, "allowed,mcp-remote")
    specs = m._extract_package_specs("npx", ["-p", "allowed", "-p", "evil", "cmd"])
    blocked = [s for s in specs if m._package_name(s) not in m._PACKAGE_ALLOWLIST]
    assert blocked == ["evil"]
