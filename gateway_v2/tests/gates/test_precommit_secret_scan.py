"""Property 6 (bash half): the pre-commit secret-scan guard agrees on header forms.

This drives the real `scripts/ralph/precommit-secret-scan.sh` over a throwaway
`git init` scratch repo built in `tmp_path`. The script scans the *staged added
diff* (`git diff --cached`), so each case writes a fixture into the scratch repo's
working tree, `git add`s it, then runs the hook with `cwd` set to the scratch repo
and asserts on the hook's exit code and (path-only) stderr.

Isolation: the scratch repo is a fresh `git init` under `tmp_path`; a throwaway
`user.email`/`user.name` is set with `git -C <scratch> config` (local, never the
real repo) and `GIT_CONFIG_GLOBAL=/dev/null` + `GIT_CONFIG_SYSTEM=/dev/null` keep it
from reading or writing the developer's real git config. Nothing touches the real
repo except reading the committed hook script by absolute path.

No literal `BEGIN ... PRIVATE KEY` / `BEGIN OPENSSH PRIVATE KEY` appears in this
file: the headers are assembled from fragments at runtime, and the fake bodies are
inert placeholders so only part (3) of the hook (header detection) is exercised,
never the live-key fingerprint (part 1) or the real-shape token checks (part 2).

Validates: Requirements 6.3, 7.1, 7.2, 7.3 (Property 6).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
HOOK = REPO / "scripts" / "ralph" / "precommit-secret-scan.sh"

# Assemble headers from fragments so this source carries no literal PEM/OpenSSH
# header (keeps the file clean independent of the tests/ allowlist excusal).
_FAKE_BODY = "NOT-A-REAL-KEY"
OPENSSH_HEADER = (
    "-----" + "BEGIN OPEN" + "SSH PRIVATE KEY" + "-----\n"
    + _FAKE_BODY + "\n"
    + "-----END OPEN" + "SSH PRIVATE KEY-----\n"
)
PKCS8_HEADER = (
    "-----" + "BEGIN " + "PRIVATE KEY" + "-----\n"
    + _FAKE_BODY + "\n"
    + "-----END " + "PRIVATE KEY-----\n"
)
# A header substring (as data) that part (3) would match but the body is secret.
_HEADER_NEEDLE = "PRIVATE KEY"


def _tools_available() -> bool:
    return shutil.which("git") is not None and shutil.which("bash") is not None


def _scratch_env() -> dict[str, str]:
    env = dict(os.environ)
    # Never read or write the developer's real git config.
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


@pytest.fixture()
def scratch_repo(tmp_path: Path) -> Path:
    """A fresh, isolated git repo under tmp_path with throwaway identity."""
    if not _tools_available():
        pytest.skip("git and/or bash not available in this environment")
    if not HOOK.is_file():
        pytest.skip(f"hook script missing: {HOOK}")

    repo = tmp_path / "scratch"
    repo.mkdir()
    env = _scratch_env()
    subprocess.run(
        ["git", "init", "-q", str(repo)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    for key, value in (("user.email", "ralph@example.invalid"), ("user.name", "Ralph Test")):
        subprocess.run(
            ["git", "-C", str(repo), "config", key, value],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
    return repo


def _stage(repo: Path, name: str, content: str) -> None:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "--", name],
        check=True,
        capture_output=True,
        text=True,
        env=_scratch_env(),
    )


def _run_hook(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(HOOK)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env=_scratch_env(),
    )


def test_staged_extensionless_openssh_header_blocks(scratch_repo: Path) -> None:
    """LGW00-2 pre-commit: extensionless OpenSSH header -> exit 1, path-only stderr."""
    _stage(scratch_repo, "secret-blob", OPENSSH_HEADER)
    result = _run_hook(scratch_repo)
    assert result.returncode == 1, result.stderr
    # Path is reported...
    assert "secret-blob" in result.stderr
    # ...but no key material: no header substring and no fake body in stderr.
    assert "PRIVATE KEY" not in result.stderr
    assert _FAKE_BODY not in result.stderr


def test_staged_suffixed_pkcs8_header_blocks(scratch_repo: Path) -> None:
    """Suffixed file with bare PKCS#8 BEGIN PRIVATE KEY -> exit 1 (Req 1.2, 7.2)."""
    _stage(scratch_repo, "leaked.txt", PKCS8_HEADER)
    result = _run_hook(scratch_repo)
    assert result.returncode == 1, result.stderr
    assert "leaked.txt" in result.stderr
    assert "PRIVATE KEY" not in result.stderr


def test_staged_dotfile_header_blocks(scratch_repo: Path) -> None:
    """Dotfile `.id_rsa` with an OpenSSH header -> exit 1 (dotfile at pre-commit)."""
    _stage(scratch_repo, ".id_rsa", OPENSSH_HEADER)
    result = _run_hook(scratch_repo)
    assert result.returncode == 1, result.stderr
    assert ".id_rsa" in result.stderr
    assert "PRIVATE KEY" not in result.stderr


def test_staged_py_mention_is_allowlisted(scratch_repo: Path) -> None:
    """A header-as-data mention in a .py file is allowlisted -> exit 0 (Req 7.3)."""
    source = f'NEEDLE = "BEGIN " + "{_HEADER_NEEDLE}"  # fixture mention, not a key\n'
    _stage(scratch_repo, "mentions.py", source)
    result = _run_hook(scratch_repo)
    assert result.returncode == 0, result.stderr


def test_staged_md_mention_is_allowlisted(scratch_repo: Path) -> None:
    """A header-as-data mention in a .md doc is allowlisted -> exit 0 (Req 7.3)."""
    doc = f"The scanner looks for a `BEGIN {_HEADER_NEEDLE}` header in staged files.\n"
    _stage(scratch_repo, "notes.md", doc)
    result = _run_hook(scratch_repo)
    assert result.returncode == 0, result.stderr


def test_staged_clean_file_passes(scratch_repo: Path) -> None:
    """A clean file with no header staged -> exit 0."""
    _stage(scratch_repo, "clean.conf", "key = value\nanother = setting\n")
    result = _run_hook(scratch_repo)
    assert result.returncode == 0, result.stderr
