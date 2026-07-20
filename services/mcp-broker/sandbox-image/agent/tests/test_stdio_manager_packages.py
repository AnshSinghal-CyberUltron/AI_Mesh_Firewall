"""Tests for package-gating logic in sandbox-agent stdio_manager (P7.23 N2+N3).

Verifies that _extract_package_spec / _package_name / _is_pinned helpers and
the _ensure_process enforcement work correctly for both the PACKAGE_ALLOWLIST
(N3) and REQUIRE_PINNED_PACKAGES (N2) controls, mirroring the gateway path.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

SANDBOX_IMAGE = Path(__file__).resolve().parents[2]


def _reload_stdio_manager(monkeypatch, **env_overrides):
    """Re-import stdio_manager with the given env overrides active."""
    for k, v in env_overrides.items():
        monkeypatch.setenv(k, v)
    # Also clear any previously-set values not in overrides so defaults apply.
    for k in ("MCP_STDIO_PACKAGE_ALLOWLIST", "MCP_STDIO_REQUIRE_PINNED_PACKAGES"):
        if k not in env_overrides:
            monkeypatch.delenv(k, raising=False)

    sandbox_image = str(SANDBOX_IMAGE)
    if sandbox_image not in sys.path:
        sys.path.insert(0, sandbox_image)
    for mod in ("agent.stdio_manager", "agent"):
        sys.modules.pop(mod, None)
    return importlib.import_module("agent.stdio_manager")


# ---------------------------------------------------------------------------
# _extract_package_spec
# ---------------------------------------------------------------------------

class TestExtractPackageSpec:
    def test_npx_bare(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert m._extract_package_spec("npx", ["-y", "mcp-remote", "https://x.com"]) == "mcp-remote"

    def test_npx_pinned(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert m._extract_package_spec("npx", ["-y", "ruflo@1.2.3", "mcp"]) == "ruflo@1.2.3"

    def test_uvx_bare(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert m._extract_package_spec("uvx", ["semgrep-mcp"]) == "semgrep-mcp"

    def test_uvx_from_flag(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert m._extract_package_spec("uvx", ["--from", "semgrep-mcp==1.0", "semgrep-mcp"]) == "semgrep-mcp==1.0"

    def test_node_returns_none(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert m._extract_package_spec("node", ["-e", "console.log('hi')"]) is None

    def test_python_returns_none(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert m._extract_package_spec("python3", ["-m", "some_mcp"]) is None

    # CHG-0126: --flag=value (equals) form + multiple package flags. The old
    # single/space-only extractor missed these -> allowlist/pinned BYPASS.
    def test_package_equals_form_is_extracted(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        # npx --package=evil safe-cmd : the FETCHED pkg is evil, not the command.
        assert m._extract_package_specs("npx", ["--package=evil", "safe-cmd"]) == ["evil"]

    def test_uvx_from_equals_form_is_extracted(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert m._extract_package_specs("uvx", ["--from=semgrep-mcp==1.0", "semgrep"]) == ["semgrep-mcp==1.0"]

    def test_multiple_package_flags_all_extracted(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert m._extract_package_specs("npx", ["-p", "a", "-p", "evil", "cmd"]) == ["a", "evil"]

    def test_positional_after_pkg_flag_is_command_not_package(self, monkeypatch):
        # With a package flag present, the bare positional is the COMMAND, not a pkg.
        m = _reload_stdio_manager(monkeypatch)
        assert m._extract_package_specs("npx", ["-p", "realpkg", "runcmd"]) == ["realpkg"]

    def test_bare_positional_still_the_package(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert m._extract_package_specs("npx", ["-y", "mcp-remote", "https://x"]) == ["mcp-remote"]


# ---------------------------------------------------------------------------
# _package_name
# ---------------------------------------------------------------------------

class TestPackageName:
    def test_bare(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert m._package_name("mcp-remote") == "mcp-remote"

    def test_at_version(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert m._package_name("ruflo@1.2.3") == "ruflo"

    def test_at_latest(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert m._package_name("ruflo@latest") == "ruflo"

    def test_pypi_pin(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert m._package_name("semgrep-mcp==1.0.0") == "semgrep-mcp"

    def test_scoped_npm(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert m._package_name("@anthropic/mcp@2.0.0") == "@anthropic/mcp"

    def test_scoped_npm_bare(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert m._package_name("@anthropic/mcp") == "@anthropic/mcp"


# ---------------------------------------------------------------------------
# _is_pinned
# ---------------------------------------------------------------------------

class TestIsPinned:
    def test_bare_is_not_pinned(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert not m._is_pinned("mcp-remote")

    def test_at_latest_is_not_pinned(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert not m._is_pinned("ruflo@latest")

    def test_at_version_is_pinned(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert m._is_pinned("ruflo@1.2.3")

    def test_pypi_pin_is_pinned(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert m._is_pinned("semgrep-mcp==1.0.0")

    def test_scoped_npm_pinned(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert m._is_pinned("@anthropic/mcp@2.0.0")

    def test_scoped_npm_bare_not_pinned(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert not m._is_pinned("@anthropic/mcp")


# ---------------------------------------------------------------------------
# _ensure_process enforcement (N2 + N3) — unit-level, no subprocess needed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestEnsureProcessPackageGating:

    async def test_allowlist_blocks_unlisted_package(self, monkeypatch):
        """N3: package not in allowlist must be rejected before spawning."""
        m = _reload_stdio_manager(
            monkeypatch, MCP_STDIO_PACKAGE_ALLOWLIST="mcp-remote,ruflo"
        )
        with pytest.raises(RuntimeError, match="not in the on-demand allowlist"):
            await m._ensure_process(
                "test-org/evil-mcp", "npx", ["-y", "evil-pkg"], {}
            )

    async def test_allowlist_permits_listed_package(self, monkeypatch):
        """N3: allowlisted package must not be rejected by the allowlist guard."""
        m = _reload_stdio_manager(
            monkeypatch, MCP_STDIO_PACKAGE_ALLOWLIST="mcp-remote,ruflo"
        )
        # Mock subprocess so we reach the registry/spawn code without a real shell.
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("npx not found")):
            with pytest.raises(RuntimeError, match="not found") as exc_info:
                await m._ensure_process(
                    "test-org/srv", "npx", ["-y", "ruflo", "mcp"], {}
                )
        assert "not in the on-demand allowlist" not in str(exc_info.value)

    async def test_allowlist_empty_permits_any(self, monkeypatch):
        """N3: empty allowlist (default) must not block any package."""
        m = _reload_stdio_manager(monkeypatch)
        assert m._PACKAGE_ALLOWLIST == set()
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("npx not found")):
            with pytest.raises(RuntimeError, match="not found") as exc_info:
                await m._ensure_process(
                    "test-org/srv", "npx", ["-y", "any-unknown-pkg"], {}
                )
        assert "not in the on-demand allowlist" not in str(exc_info.value)

    async def test_require_pinned_blocks_bare(self, monkeypatch):
        """N2: unpinned spec must be rejected when REQUIRE_PINNED is true."""
        m = _reload_stdio_manager(
            monkeypatch, MCP_STDIO_REQUIRE_PINNED_PACKAGES="true"
        )
        with pytest.raises(RuntimeError, match="must be version-pinned"):
            await m._ensure_process(
                "test-org/srv", "npx", ["-y", "mcp-remote"], {}
            )

    async def test_require_pinned_blocks_at_latest(self, monkeypatch):
        """N2: @latest spec must be rejected when REQUIRE_PINNED is true."""
        m = _reload_stdio_manager(
            monkeypatch, MCP_STDIO_REQUIRE_PINNED_PACKAGES="true"
        )
        with pytest.raises(RuntimeError, match="must be version-pinned"):
            await m._ensure_process(
                "test-org/srv", "npx", ["-y", "ruflo@latest"], {}
            )

    async def test_require_pinned_permits_versioned(self, monkeypatch):
        """N2: pinned spec must pass the pinned guard."""
        m = _reload_stdio_manager(
            monkeypatch, MCP_STDIO_REQUIRE_PINNED_PACKAGES="true"
        )
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("npx not found")):
            with pytest.raises(RuntimeError, match="not found") as exc_info:
                await m._ensure_process(
                    "test-org/srv", "npx", ["-y", "ruflo@1.2.3"], {}
                )
        assert "must be version-pinned" not in str(exc_info.value)

    async def test_require_pinned_off_by_default(self, monkeypatch):
        """N2: default (off) must not block unpinned specs."""
        m = _reload_stdio_manager(monkeypatch)
        assert not m._REQUIRE_PINNED_PACKAGES
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("npx not found")):
            with pytest.raises(RuntimeError, match="not found") as exc_info:
                await m._ensure_process(
                    "test-org/srv", "npx", ["-y", "mcp-remote@latest"], {}
                )
        assert "must be version-pinned" not in str(exc_info.value)

    async def test_allowlist_blocks_equals_form_smuggled_package(self, monkeypatch):
        """CHG-0126: --package=evil (=-form) must NOT bypass the allowlist by making
        the check run against the trailing command token instead of the real pkg."""
        m = _reload_stdio_manager(
            monkeypatch, MCP_STDIO_PACKAGE_ALLOWLIST="safe-cmd,mcp-remote"
        )
        with pytest.raises(RuntimeError, match="not in the on-demand allowlist"):
            await m._ensure_process(
                "test-org/srv", "npx", ["--package=evil-pkg", "safe-cmd"], {}
            )

    async def test_allowlist_blocks_second_package_flag(self, monkeypatch):
        """CHG-0126: a 2nd -p must also be checked (not just the first)."""
        m = _reload_stdio_manager(
            monkeypatch, MCP_STDIO_PACKAGE_ALLOWLIST="allowed,mcp-remote"
        )
        with pytest.raises(RuntimeError, match="not in the on-demand allowlist"):
            await m._ensure_process(
                "test-org/srv", "npx", ["-p", "allowed", "-p", "evil", "cmd"], {}
            )

    async def test_require_pinned_blocks_equals_form_unpinned(self, monkeypatch):
        """CHG-0126: --from=<unpinned> (=-form) must be caught by the pinned guard."""
        m = _reload_stdio_manager(
            monkeypatch, MCP_STDIO_REQUIRE_PINNED_PACKAGES="true"
        )
        with pytest.raises(RuntimeError, match="must be version-pinned"):
            await m._ensure_process(
                "test-org/srv", "uvx", ["--from=semgrep-mcp", "semgrep"], {}
            )

    async def test_node_interpreter_bypasses_package_checks(self, monkeypatch):
        """N2+N3: non-fetching interpreters (node/python) skip package checks."""
        m = _reload_stdio_manager(
            monkeypatch,
            MCP_STDIO_PACKAGE_ALLOWLIST="nothing",
            MCP_STDIO_REQUIRE_PINNED_PACKAGES="true",
        )
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("node not found")):
            with pytest.raises(RuntimeError, match="not found") as exc_info:
                await m._ensure_process(
                    "test-org/srv", "node", ["-e", "require('x')"], {}
                )
        err = str(exc_info.value)
        assert "not in the on-demand allowlist" not in err
        assert "must be version-pinned" not in err


# ---------------------------------------------------------------------------
# _safe_args_for_log (CHG-0053) — mask secret-looking arg VALUES before logging
# ---------------------------------------------------------------------------

class TestSafeArgsForLog:
    def test_masks_value_after_secret_flag(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert m._safe_args_for_log(["--token", "s3cr3t", "-y", "@pkg"]) == [
            "--token", "***", "-y", "@pkg",
        ]

    def test_masks_inline_secret_flag(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert m._safe_args_for_log(["--api-key=s3cr3t"]) == ["--api-key=***"]

    def test_masks_password_and_auth(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        assert m._safe_args_for_log(["--password", "p", "--auth-secret=q"]) == [
            "--password", "***", "--auth-secret=***",
        ]

    def test_leaves_non_secret_args_untouched(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        args = ["-y", "@playwright/mcp@latest", "https://mcp.example.com"]
        assert m._safe_args_for_log(args) == args

    def test_bare_positional_not_masked(self, monkeypatch):
        # A standalone value with NO embedded credentials is left intact (package
        # specs / plain URLs must not be corrupted); flag-based secrets are one target.
        m = _reload_stdio_manager(monkeypatch)
        assert m._safe_args_for_log(["mcp-remote", "tokenish-pkgname"]) == [
            "mcp-remote", "tokenish-pkgname",
        ]

    # CHG-0107: the agent inherits the shared helper, which ALSO masks credentials
    # embedded in a URL passed as a STANDALONE arg (the CHG-0053 follow-up leak).
    def test_masks_url_userinfo_in_standalone_arg(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        out = m._safe_args_for_log(
            ["-y", "mcp-remote", "postgres://admin:S3cr3tPass@db.internal:5432/prod"])
        assert "S3cr3tPass" not in " ".join(out)
        assert out[-1] == "postgres://***@db.internal:5432/prod"

    def test_masks_url_query_secret_params_in_standalone_arg(self, monkeypatch):
        m = _reload_stdio_manager(monkeypatch)
        out = m._safe_args_for_log(
            ["https://api.example.com/mcp?api_key=AKIAIOSFODNN7EXAMPLE&token=abc&page=2"])
        assert "AKIAIOSFODNN7EXAMPLE" not in " ".join(out)
        assert out == ["https://api.example.com/mcp?api_key=***&token=***&page=2"]
