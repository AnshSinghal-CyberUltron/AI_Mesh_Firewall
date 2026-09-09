"""Unit tests for missing-attribution abort (``attribution.py``).

Additive, measurement-only (Requirement 9): this file lives under
``scripts/detection/`` and touches no production path. All git fixtures are
created under pytest's ``tmp_path``, never against the real
``tests/detection_corpus/`` or any shipped code path.

Task 7.2 covers Requirement 6.10: IF the Corpus_Version, the
Reproducible_Command, or the Target_FPR cannot be determined for a run, THEN the
harness terminates with a non-success result and an error indication naming the
missing attribution value, and emits no Posture_Report. The unit here is the
resolution layer: a non-success is signalled by returning an
``AttributionUnavailable`` sentinel that names the missing ``value`` and carries a
non-empty ``reason`` (never a fabricated / partial ``Attribution``).
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

# The detection modules import as top-level names with ``scripts/detection`` on
# the path (they are a self-contained additive package). Make that import work
# whether pytest is invoked from the repo root, the gateway dir, or elsewhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import attribution  # noqa: E402
from attribution import (  # noqa: E402
    Attribution,
    AttributionUnavailable,
    corpus_version,
    reproducible_command,
    resolve_attribution,
    target_fpr,
)


# --- Fixtures / helpers -------------------------------------------------------

# A commit hash is a 40-char lowercase hex string (git's default %H format).
_HEX = set("0123456789abcdef")


def _git(cwd, *args):
    """Run a git command inside ``cwd``, raising on failure (fixture-only)."""
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


def _init_repo_with_corpus(root):
    """Init a tiny git repo under ``root`` with a committed corpus dir.

    Mirrors the real layout just enough that ``corpus_version`` can resolve a
    pinning commit: a committed file under ``tests/detection_corpus/``. Uses a
    local, deterministic identity so the fixture never depends on host git config.
    """
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Posture Test")
    corpus_dir = root / "tests" / "detection_corpus"
    corpus_dir.mkdir(parents=True)
    (corpus_dir / "corpus.jsonl").write_text('{"id": "x", "label": "benign"}\n')
    _git(root, "add", "tests/detection_corpus/corpus.jsonl")
    _git(root, "commit", "-m", "add detection corpus")


def _assert_names_corpus_version(result):
    """A sentinel must name ``corpus_version`` and carry a non-empty reason."""
    assert isinstance(result, AttributionUnavailable)
    assert result.value == "corpus_version"
    assert isinstance(result.reason, str)
    assert result.reason.strip() != ""


# --- Constants: reproducible_command and target_fpr always determinable -------


def test_reproducible_command_is_the_documented_constant():
    """``reproducible_command`` is fixed and always determinable (Req 6.6, 7.1)."""
    command = reproducible_command()
    assert isinstance(command, str)
    assert command == (
        "cd gateway && ./.venv/bin/python ../scripts/detection/score_postures.py"
    )
    # It names the documented entry point so a reader can actually run it.
    assert "score_postures.py" in command


def test_target_fpr_is_one_percent():
    """``target_fpr`` is fixed at 0.01 and always determinable (Req 3.1, 6.6)."""
    assert target_fpr() == 0.01


# --- corpus_version: undeterminable -> sentinel naming the missing value ------


def test_corpus_version_nonexistent_root_returns_sentinel(tmp_path):
    """A root path that does not exist is a non-success naming corpus_version."""
    missing = tmp_path / "does-not-exist"
    result = corpus_version(missing)
    _assert_names_corpus_version(result)
    assert str(missing) in result.reason


def test_corpus_version_not_a_git_work_tree_returns_sentinel(tmp_path):
    """A real dir with a corpus path but NO git repo cannot pin a commit."""
    # Create the corpus path so we get past the existence check and reach the
    # git resolution, which must fail because tmp_path is not a git work tree.
    (tmp_path / "tests" / "detection_corpus").mkdir(parents=True)
    result = corpus_version(tmp_path)
    _assert_names_corpus_version(result)


def test_corpus_version_missing_corpus_path_returns_sentinel(tmp_path):
    """A git-less dir without the corpus path names corpus_version as missing."""
    result = corpus_version(tmp_path)
    _assert_names_corpus_version(result)
    assert "tests/detection_corpus" in result.reason


def test_corpus_version_untracked_corpus_returns_sentinel(tmp_path):
    """An initialised git repo whose corpus dir is UNTRACKED has no pinning commit.

    ``git log`` for an untracked path returns success with empty output, which the
    helper must treat as undeterminable rather than a blank corpus version.
    """
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Posture Test")
    # Corpus dir exists (passes existence check) but is never added/committed.
    (tmp_path / "tests" / "detection_corpus").mkdir(parents=True)
    (tmp_path / "tests" / "detection_corpus" / "corpus.jsonl").write_text("{}\n")
    result = corpus_version(tmp_path)
    _assert_names_corpus_version(result)


# --- corpus_version: happy path -> resolved commit hash -----------------------


def test_corpus_version_committed_corpus_returns_commit(tmp_path):
    """A committed corpus dir resolves to its pinning commit hash (Req 6.5)."""
    _init_repo_with_corpus(tmp_path)
    result = corpus_version(tmp_path)
    assert not isinstance(result, AttributionUnavailable)
    assert isinstance(result, str)
    assert len(result) == 40
    assert set(result.lower()) <= _HEX


# --- resolve_attribution: propagates the sentinel / composes the full object --


def test_resolve_attribution_propagates_sentinel_on_missing_corpus_version(tmp_path):
    """When corpus_version is undeterminable, resolve returns that sentinel and
    NO Attribution — the caller must emit no report (Req 6.10)."""
    missing = tmp_path / "does-not-exist"
    result = resolve_attribution(missing)
    _assert_names_corpus_version(result)
    assert not isinstance(result, Attribution)


def test_resolve_attribution_happy_path_returns_full_attribution(tmp_path):
    """With a real pinning commit, resolve returns a complete Attribution with
    all three values populated (Req 6.5, 6.6)."""
    _init_repo_with_corpus(tmp_path)
    result = resolve_attribution(tmp_path)

    assert isinstance(result, Attribution)
    # corpus_version is the resolved commit hash.
    assert isinstance(result.corpus_version, str)
    assert len(result.corpus_version) == 40
    assert set(result.corpus_version.lower()) <= _HEX
    # The other two are the always-determinable constants.
    assert result.reproducible_command == reproducible_command()
    assert result.target_fpr == target_fpr()
    assert result.target_fpr == 0.01


# --- AttributionUnavailable invariants: named value + non-empty reason --------


def test_sentinel_requires_non_empty_value_and_reason():
    """The sentinel cannot be constructed without a named value AND a reason, so a
    non-success can never be silent about what is missing (Req 6.10)."""
    ok = AttributionUnavailable("corpus_version", "not a git work tree")
    assert ok.value == "corpus_version"
    assert ok.reason == "not a git work tree"

    with pytest.raises(ValueError):
        AttributionUnavailable("", "reason present")
    with pytest.raises(ValueError):
        AttributionUnavailable("corpus_version", "")
