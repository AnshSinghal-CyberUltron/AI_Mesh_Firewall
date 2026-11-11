"""Scan for OpenSSH/PEM private-key headers. Report paths only — never bodies."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Closed LGW00-1: this filename must never be tracked again. Empty allowlist.
KNOWN_UNTIL_LGW00_1: frozenset[str] = frozenset()
FORBIDDEN_KEY_FILENAMES = frozenset({"ai-mesh-firewall"})
_OPENSSH = b"BEGIN OPEN" + b"SSH PRIVATE KEY"
# D1: optional label group (with its trailing space) so bare PKCS#8
# ``BEGIN PRIVATE KEY`` matches via the empty branch while ``BEGIN RSA PRIVATE KEY``
# etc. still match via the label branch. This is a regex, not a literal header, so
# the scanner source does not self-match.
_PEM = re.compile(rb"BEGIN (?:[A-Z0-9][A-Z0-9 ]{0,64} )?PRIVATE KEY")
_NON_KEY_NAMES = frozenset(
    {
        "Dockerfile",
        "Makefile",
        "LICENSE",
        "CHANGELOG",
        "Jenkinsfile",
        "Procfile",
        "Gemfile",
        "Vagrantfile",
        "Pipfile",
        "Brewfile",
    },
)

# D2: path-pattern Content_Allowlist. Matched against the repo-relative POSIX path
# (falling back to the full POSIX string when the path is not under the scanned
# repo, e.g. tmp_path test fixtures). Source/config extensions and the test-fixture
# patterns are suffix/substring anchored so they also match absolute paths; the
# dir-prefix patterns need the repo-relative form. These EXTEND (never replace) the
# ``_NON_KEY_NAMES`` shape precedent and excuse files that only *mention* a header
# (the two enforcers carry the needle; source builds header strings as data; docs
# and specs discuss headers; redaction fixtures embed fake keys).
Content_Allowlist: tuple[re.Pattern[str], ...] = (
    # The two enforcers — carry the needle/regex by construction.
    re.compile(r"^gateway_v2/lint/"),
    re.compile(r"^scripts/ralph/"),
    # Source / config extensions — modules build header strings as data.
    re.compile(r"\.py$"),
    re.compile(r"\.sh$"),
    re.compile(r"\.go$"),
    re.compile(r"\.ts$"),
    re.compile(r"\.tsx$"),
    re.compile(r"\.js$"),
    re.compile(r"\.jsx$"),
    re.compile(r"\.mjs$"),
    re.compile(r"\.rs$"),
    re.compile(r"\.ya?ml$"),
    # Documentation — docs discuss headers.
    re.compile(r"^docs/"),
    re.compile(r"\.md$"),
    re.compile(r"\.html$"),
    # Specs — this very spec quotes ``BEGIN PRIVATE KEY``.
    re.compile(r"^\.kiro/specs/"),
    # Test fixtures — redaction fixtures embed fake keys.
    re.compile(r"(^|/)tests?/"),
    re.compile(r"/leakhunt/"),
    re.compile(r"patterns\.py$"),
    re.compile(r"corpus\.py$"),
    re.compile(r"ATTACK_LANDSCAPE\.md$"),
    re.compile(r"SOLUTIONS\.md$"),
    re.compile(r"fixtures.*\.py$"),
    re.compile(r"canaries\.json$"),
)


@dataclass(frozen=True)
class HeaderHit:
    path: str


def file_has_private_key_header(path: Path) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return False
    if _OPENSSH in data:
        return True
    return _PEM.search(data) is not None


def is_key_shaped_filename(path: Path) -> bool:
    """D2: content-first. Read every file for header content unless it is a named
    build file; the Content_Allowlist (consulted in ``scan_paths``) is what excuses
    source/docs/spec/fixture mentions. Suffixed files and dotfiles are now readable."""
    name = path.name
    if name in FORBIDDEN_KEY_FILENAMES:
        return True
    suffix = path.suffix.lower()
    if suffix in {".pem", ".key", ".secret"}:
        return True
    if name in _NON_KEY_NAMES or name.startswith("Dockerfile") or name.startswith("Makefile"):
        return False
    return True


def _match_text(path: Path, repo: Path | None) -> str:
    """Repo-relative POSIX form when the path is under ``repo``; else the full POSIX
    string. Suffix/substring patterns match either form; dir-prefix patterns rely on
    the repo-relative form produced here."""
    if repo is not None:
        try:
            return path.resolve().relative_to(repo.resolve()).as_posix()
        except ValueError:
            pass
    return path.as_posix()


def _is_allowlisted(path: Path, repo: Path | None) -> bool:
    text = _match_text(path, repo)
    return any(pattern.search(text) for pattern in Content_Allowlist)


def scan_paths(
    paths: list[Path],
    allowlist: set[str] | frozenset[str],
    *,
    repo: Path | None = None,
) -> list[HeaderHit]:
    hits: list[HeaderHit] = []
    for raw in paths:
        path = Path(raw)
        if _is_allowlisted(path, repo):
            continue
        if path.name in allowlist:
            continue
        if not is_key_shaped_filename(path):
            continue
        if file_has_private_key_header(path):
            hits.append(HeaderHit(path=str(path)))
    return hits


def git_tracked_files(repo: Path) -> list[Path]:
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=repo)
    names = [n for n in raw.split(b"\0") if n]
    return [repo / n.decode("utf-8", "surrogateescape") for n in names]


def scan_repo(repo: Path, *, allow_known: bool) -> tuple[list[HeaderHit], list[str]]:
    allow = set(KNOWN_UNTIL_LGW00_1) if allow_known else set()
    tracked = git_tracked_files(repo)
    known_present = [
        str(p.relative_to(repo))
        for p in tracked
        if p.name in FORBIDDEN_KEY_FILENAMES and file_has_private_key_header(p)
    ]
    return scan_paths(tracked, allow, repo=repo), known_present


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report private-key headers by path only.")
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument(
        "--fail-allowlist",
        action="store_true",
        default=True,
        help="Fail if a forbidden key filename is tracked (LGW00-1 closed).",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    hits, known = scan_repo(args.repo.resolve(), allow_known=False)
    for rel in known:
        print(
            "LGW00-1 CLOSED residual: forbidden private-key filename "
            f"tracked (body omitted): {rel}",
        )
    for hit in hits:
        try:
            rel = str(Path(hit.path).resolve().relative_to(args.repo.resolve()))
        except ValueError:
            rel = hit.path
        print(f"private-key header (body omitted): {rel}")
    if args.fail_allowlist and known:
        return 1
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
