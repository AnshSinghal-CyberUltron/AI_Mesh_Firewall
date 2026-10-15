"""Pollution-proof EXAMPLE tests — the frozen artifact never pollutes tooling (task 9.1).

# Feature: harness-release-hygiene-r2-20
# Validates: Requirements 6.4

The frozen-artifact location is the critical packaging/gate hazard the design resolves: a
``load_harness/bin/<version>/`` tree must be INVISIBLE to setuptools package discovery, to ruff's
and mypy's ``.py`` file selection, and to a re-``freeze`` (no recursive self-freeze). These are
focused, representative EXAMPLE assertions (NOT property-based — the >= 10,000-iteration house-idiom
sweeps live in the ``test_release*`` / ``test_verify*`` / ``test_provenance`` / ``test_version_*``
files). They pin the four pollution-proof guarantees the design's Testing Strategy (Gate / pollution
proof, Req 6.4) and Architecture (Frozen-artifact location) call out:

* (a) **No recursive self-freeze** — :func:`load_harness.release.iter_source_files` over a package
  root that contains a ``bin/`` artifact subtree returns ONLY live source: no returned path has
  ``bin`` in its parts, so a re-freeze reads the live tree, never a prior Frozen_Release
  (Req 6.4, 3.3);
* (b) **setuptools discovery excludes bin** — package discovery over the tree does NOT list a
  ``load_harness.bin*`` package;
* (c) **pyproject declares the exclusions** — the setuptools / ruff / mypy excludes exist and
  ``[tool.importlinter].root_packages`` is UNCHANGED (``["gateway_v2"]``);
* (d) **.gitignore ignores bin** — the repo-root ``.gitignore`` ignores
  ``gateway_v2/load_harness/bin/`` so a Frozen_Release is never committed.

Everything is pure / offline / server-free (Req 6.5). The only freeze here runs under pytest's
``tmp_path`` — nothing is ever frozen into the real ``load_harness/`` tree, and this test leaves no
``load_harness/bin/`` behind.

Test files are not under the import-linter layer contract.
"""

from __future__ import annotations

import fnmatch
import tomllib
from fnmatch import fnmatch as _fnmatch
from pathlib import Path

from load_harness.release import iter_source_files

# --- repo / config locations ------------------------------------------------------------------
# This test file lives at gateway_v2/tests/load_harness/test_pollution_proof.py; the gateway_v2
# package root (which owns pyproject.toml) is three parents up, and the repo root (which owns the
# .gitignore carrying the gateway_v2 entry) is one above that.
_GATEWAY_V2_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _GATEWAY_V2_ROOT / "pyproject.toml"


def _load_pyproject() -> dict[str, object]:
    """Parse ``gateway_v2/pyproject.toml`` with the stdlib ``tomllib`` (offline, no deps)."""
    with _PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def _find_repo_gitignore() -> Path:
    """Walk up from this test file to the repo-root ``.gitignore`` carrying the gateway_v2 entry.

    The ``bin/`` ignore line is scoped as ``gateway_v2/load_harness/bin/`` so it lives in the
    repo-root ``.gitignore``, not a package-local one. Walk parents until a ``.gitignore`` that
    actually mentions ``gateway_v2/load_harness/bin/`` is found so the test keys off the real
    ignore authority rather than a hard-coded depth.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".gitignore"
        if candidate.is_file() and "gateway_v2/load_harness/bin/" in candidate.read_text(
            encoding="utf-8"
        ):
            return candidate
    raise AssertionError("no repo-root .gitignore ignoring gateway_v2/load_harness/bin/ was found")


# --- (a) no recursive self-freeze -------------------------------------------------------------
def test_iter_source_files_excludes_bin_artifact_tree(tmp_path: Path) -> None:
    """(a) ``iter_source_files`` over a root with a ``bin/`` subtree returns NO path under ``bin``.

    Build a tmp package root with a couple of live source files AND a nested frozen-artifact tree
    (``bin/v2.3/harness.tar`` + ``bin/vX/foo.py`` — a stray ``.py`` under ``bin/`` is the worst
    case). ``iter_source_files`` MUST exclude the whole ``bin/`` tree so a re-freeze reads live
    source only and can never recursively capture a prior Frozen_Release (Req 6.4, 3.3).
    """
    package_root = tmp_path / "load_harness"
    (package_root / "strata").mkdir(parents=True)
    (package_root / "config.py").write_text("CONFIG = 1\n", encoding="utf-8")
    (package_root / "strata" / "floor.py").write_text("FLOOR = 2\n", encoding="utf-8")

    # A nested frozen-artifact tree, including a stray .py that must still be excluded.
    (package_root / "bin" / "v2.3").mkdir(parents=True)
    (package_root / "bin" / "v2.3" / "harness.tar").write_bytes(b"\x00frozen-archive-bytes")
    (package_root / "bin" / "vX").mkdir(parents=True)
    (package_root / "bin" / "vX" / "foo.py").write_text("STRAY = 3\n", encoding="utf-8")

    relpaths = iter_source_files(package_root)

    # No returned path touches the bin/ artifact tree ...
    assert all("bin" not in relpath.parts for relpath in relpaths)
    # ... and the live source IS captured (the exclusion is scoped to bin/, not everything).
    captured = {relpath.as_posix() for relpath in relpaths}
    assert captured == {"config.py", "strata/floor.py"}


# --- (b) setuptools discovery excludes bin ----------------------------------------------------
def test_setuptools_discovery_excludes_load_harness_bin() -> None:
    """(b) package discovery does NOT list a ``load_harness.bin*`` package.

    PATH USED: if ``setuptools`` is importable in the venv, run its real ``find_packages`` with the
    exact include/exclude globs from ``pyproject.toml`` over the gateway_v2 root and assert no
    discovered package matches ``load_harness.bin*``. Otherwise (setuptools absent — the base
    ``.[dev]`` tree is framework-free), read the include/exclude globs from ``pyproject.toml`` via
    ``tomllib`` and assert the ``load_harness.bin*`` exclude pattern (fnmatch, the same matcher
    setuptools uses) matches the representative sub-package names. Either way the frozen tree is
    excluded from discovery.
    """
    data = _load_pyproject()
    tool = data["tool"]
    assert isinstance(tool, dict)
    setuptools_cfg = tool["setuptools"]
    assert isinstance(setuptools_cfg, dict)
    find_cfg = setuptools_cfg["packages"]
    assert isinstance(find_cfg, dict)
    find = find_cfg["find"]
    assert isinstance(find, dict)
    include = [str(pattern) for pattern in find["include"]]  # type: ignore[union-attr]
    exclude = [str(pattern) for pattern in find["exclude"]]  # type: ignore[union-attr]

    # The exclude the card requires must be declared.
    assert "load_harness.bin*" in exclude

    try:
        from setuptools import find_packages
    except ImportError:
        # FALLBACK PATH: setuptools not installed in the venv. Assert the declared exclude glob
        # (fnmatch — setuptools' own matcher) catches load_harness.bin and its sub-packages.
        for name in ("load_harness.bin", "load_harness.bin.v2", "load_harness.bin.v2.3"):
            assert any(_fnmatch(name, pattern) for pattern in exclude), name
        return

    # REAL PATH: run setuptools' discovery over the gateway_v2 root with the declared globs. The
    # live tree has no bin/ (this test freezes nothing), so discovery simply must never surface a
    # load_harness.bin* package regardless.
    discovered = find_packages(where=str(_GATEWAY_V2_ROOT), include=include, exclude=exclude)
    assert not any(fnmatch.fnmatch(pkg, "load_harness.bin*") for pkg in discovered)


# --- (c) pyproject declares the exclusions ----------------------------------------------------
def test_pyproject_declares_bin_exclusions() -> None:
    """(c) ``pyproject.toml`` declares the setuptools/ruff/mypy excludes; importlinter unchanged.

    The frozen-artifact tree is pinned closed with belt-and-braces exclusions across the three
    tools that could otherwise sweep it up, and the import-linter root is left UNCHANGED so
    ``load_harness`` (and its ``bin/``) stay outside the layer contract (Req 6.4, 6.1).
    """
    data = _load_pyproject()
    tool = data["tool"]
    assert isinstance(tool, dict)

    setuptools_find = tool["setuptools"]["packages"]["find"]  # type: ignore[index]
    assert "load_harness.bin*" in setuptools_find["exclude"]  # type: ignore[index]

    ruff = tool["ruff"]
    assert isinstance(ruff, dict)
    assert "load_harness/bin" in ruff["extend-exclude"]  # type: ignore[index]

    mypy = tool["mypy"]
    assert isinstance(mypy, dict)
    assert "load_harness/bin/" in mypy["exclude"]  # type: ignore[index]

    importlinter = tool["importlinter"]
    assert isinstance(importlinter, dict)
    assert importlinter["root_packages"] == ["gateway_v2"]


# --- (d) .gitignore ignores bin ---------------------------------------------------------------
def test_gitignore_ignores_frozen_artifact_tree() -> None:
    """(d) the repo-root ``.gitignore`` ignores ``gateway_v2/load_harness/bin/``.

    A Frozen_Release is a reproducible build artifact (byte-stable re-freeze), so it is NOT
    committed — keeping it out of git removes the last way a frozen ``.py``-less archive could
    pollute the source tree, review, or the gates (Req 6.4).
    """
    gitignore = _find_repo_gitignore()
    assert "gateway_v2/load_harness/bin/" in gitignore.read_text(encoding="utf-8")
