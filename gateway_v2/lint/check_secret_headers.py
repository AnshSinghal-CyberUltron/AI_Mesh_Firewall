"""Scan for OpenSSH/PEM private-key headers. Report paths only — never bodies."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

KNOWN_UNTIL_LGW00_1 = frozenset({"ai-mesh-firewall"})
_OPENSSH = b"BEGIN OPEN" + b"SSH PRIVATE KEY"
_PEM = re.compile(rb"BEGIN [A-Z0-9][A-Z0-9 ]{0,64} PRIVATE KEY")
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
    """LGW00-2: fail closed on extensionless / *.pem|*.key files, not on docs/tests."""
    name = path.name
    suffix = path.suffix.lower()
    if suffix in {".pem", ".key", ".secret"}:
        return True
    if suffix:
        return False
    if name.startswith("."):
        return False
    if name in _NON_KEY_NAMES or name.startswith("Dockerfile") or name.startswith("Makefile"):
        return False
    return True


def scan_paths(paths: list[Path], allowlist: set[str] | frozenset[str]) -> list[HeaderHit]:
    hits: list[HeaderHit] = []
    for raw in paths:
        path = Path(raw)
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
        if p.name in KNOWN_UNTIL_LGW00_1 and file_has_private_key_header(p)
    ]
    return scan_paths(tracked, allow), known_present


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report private-key headers by path only.")
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument(
        "--fail-allowlist",
        action="store_true",
        help="Also fail on the known LGW00-1 path (human removal only).",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    hits, known = scan_repo(args.repo.resolve(), allow_known=not args.fail_allowlist)
    for rel in known:
        print(f"LGW00-1 OPEN: known tracked private-key header (body omitted): {rel}")
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
