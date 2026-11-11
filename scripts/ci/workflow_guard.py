#!/usr/bin/env python3
"""Fail when a GitHub Actions workflow is not pinned or not least-privilege.

Rules, checked in every ``*.yml``/``*.yaml`` file under the given paths
(default ``.github/workflows``):

  unpinned-uses   every ``uses:`` is in this repository (``./path``, or GitHub's ``$/path``
                  self-repository form), ``docker://image@sha256:<64 hex>``, or
                  ``owner/repo[/path]@<40-hex commit SHA>``. A tag or branch can be moved to
                  other code after review; a commit SHA cannot.
  version-comment a SHA pin carries its release as a trailing comment (``# v4.2.1``), so a
                  reviewer can read it and Dependabot keeps the comment in step with the SHA.
  permissions     a workflow (not a composite ``action.yml``) has a top-level ``permissions:``
                  key. Without it every job gets the repository's default token permissions.
  top-level-write the top-level ``permissions:`` grants no ``write`` scope and is not
                  ``write-all``/``read-all``; a job that needs more asks for it itself.
  unpinned-image  a literal ``jobs.<id>.container`` / ``container.image`` /
                  ``services.<id>.image`` is pinned by digest (``name:tag@sha256:<64 hex>``).
                  ``${{ ... }}`` expressions are reported as a notice, not checked.

Standard library only, so it runs on any runner without an install step. It reads YAML line
by line: block scalars (``run: |``) are skipped, so text inside a script is never mistaken for
a key. Forms it cannot verify (flow mappings such as ``{uses: x}``, aliases, a ``uses:`` value
on the next line) are errors, so the guard fails closed rather than skipping them.

Exit status: 0 clean, 1 findings, 2 usage error (no workflow files found).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SHA_RE = re.compile(r"[0-9a-fA-F]{40}")
DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")
VERSION_COMMENT_RE = re.compile(r"#\s*v?\d+(\.\d+)*\S*")
BLOCK_SCALAR_RE = re.compile(r"[|>][0-9+-]*\s*(#.*)?")
SEQ_BLOCK_SCALAR_RE = re.compile(r"^(\s*)-\s+[|>][0-9+-]*\s*(#.*)?$")  # `- |` list item
# `key:` or `- key:` (one level of sequence nesting per dash), optionally after an anchor or tag
# (`- &step uses:`); the key may be quoted.
KEY_RE = re.compile(
    r"^(?P<lead>\s*(?:-\s+)*)"
    r"(?:[&!]\S+\s+)*"
    r"(?P<key>\"[^\"]*\"|'[^']*'|[^\s\"'#{\[\]}&*!|>%@`,][^:#]*?)"
    r"\s*:(?:\s+(?P<value>.*)|\s*)$"
)
FLOW_USES_RE = re.compile(r"[{,]\s*[\"']?uses[\"']?\s*:")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    message: str


@dataclass
class Result:
    errors: list[Finding]
    notices: list[Finding]
    uses: int  # `uses:` keys checked (block-scalar text excluded)


def _strip_value(raw: str) -> tuple[str, str]:
    """Split a scalar value into (value, trailing comment); quotes are removed."""
    raw = raw.strip()
    if raw.startswith("#"):
        return "", raw
    if raw[:1] in ("'", '"'):
        quote = raw[0]
        end = raw.find(quote, 1)
        if end == -1:
            return raw, ""  # multi-line quoted scalar: callers treat it as unverifiable
        rest = raw[end + 1 :].strip()
        return raw[1:end], rest if rest.startswith("#") else ""
    match = re.search(r"\s#", raw)
    if match:
        return raw[: match.start()].rstrip(), raw[match.start() :].strip()
    return raw, ""


def _check_uses(value: str, comment: str) -> tuple[str, str] | None:
    """Return (rule, message) when a `uses:` value is not pinned, else None."""
    if not value or value.startswith(("|", ">", "&", "*", "{", "[")) or "${{" in value:
        return "unpinned-uses", f"cannot verify `uses: {value}`; write the reference on one line"
    if value.startswith(("./", "$/")):  # this repository, at the commit being run
        return None
    if value.startswith("docker://"):
        if DIGEST_RE.search(value):
            return None
        return "unpinned-uses", f"`{value}` is not pinned by digest (docker://name:tag@sha256:<64 hex>)"
    ref = value.rpartition("@")[2] if "@" in value else ""
    if not SHA_RE.fullmatch(ref):
        shown = ref or "no ref"
        message = (
            f"`{value}` is pinned to `{shown}`, not a full 40-character commit SHA; "
            "use `owner/repo@<sha> # vX.Y.Z`"
        )
        return "unpinned-uses", message
    if not VERSION_COMMENT_RE.match(comment):
        return "version-comment", f"`{value}` needs its release as a trailing comment, e.g. `# v1.2.3`"
    return None


def check_file(path: Path) -> Result:
    """Check one workflow or composite-action file."""
    shown = str(path)
    errors: list[Finding] = []
    notices: list[Finding] = []
    uses_count = 0
    is_action = path.name in ("action.yml", "action.yaml")
    lines = path.read_text(encoding="utf-8").splitlines()

    stack: list[tuple[int, str]] = []  # (column of key, key) for the current mapping path
    scalar_parent: int | None = None  # column of the key that opened a block scalar
    top_permissions_line: int | None = None
    in_top_permissions = False

    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if scalar_parent is not None:
            if not stripped or indent > scalar_parent:
                continue  # inside a block scalar: script text, never keys
            scalar_parent = None
        if not stripped or stripped.startswith("#") or stripped in ("---", "..."):
            continue
        seq_scalar = SEQ_BLOCK_SCALAR_RE.match(line)
        if seq_scalar:
            scalar_parent = len(seq_scalar.group(1))
            continue

        if FLOW_USES_RE.search(line):
            errors.append(Finding(shown, number, "unpinned-uses",
                                  "flow-style `uses:` cannot be verified; write the step in block style"))

        match = KEY_RE.match(line)
        if indent == 0 and not match:
            in_top_permissions = False
        if not match:
            continue
        column = len(match.group("lead"))
        key = match.group("key").strip("\"'")
        raw_value = match.group("value") or ""
        value, comment = _strip_value(raw_value) if raw_value else ("", "")

        while stack and stack[-1][0] >= column:
            stack.pop()
        keys = [k for _, k in stack] + [key]
        stack.append((column, key))
        if raw_value and BLOCK_SCALAR_RE.fullmatch(raw_value.strip()):
            scalar_parent = column

        if column == 0:
            in_top_permissions = key == "permissions"
            if in_top_permissions:
                top_permissions_line = number
                errors.extend(_check_top_permissions_value(shown, number, raw_value, value))
        elif in_top_permissions and len(keys) == 2 and value.lower() == "write":
            errors.append(Finding(shown, number, "top-level-write",
                                  f"top-level `permissions` grants `{key}: write`; grant it on the job that needs it"))

        if key == "uses":
            uses_count += 1
            problem = _check_uses(value, comment)
            if problem:
                errors.append(Finding(shown, number, *problem))
        elif _is_image_key(keys) and value:
            if raw_value.strip().startswith(("|", ">")):
                errors.append(Finding(shown, number, "unpinned-image", "cannot verify a block-scalar image"))
            elif "${{" in value:
                notices.append(Finding(shown, number, "unpinned-image",
                                       f"image `{value}` is an expression; pin its source by digest"))
            elif not DIGEST_RE.search(value):
                errors.append(Finding(shown, number, "unpinned-image",
                                      f"image `{value}` is not pinned by digest (name:tag@sha256:<64 hex>)"))

    if not is_action and top_permissions_line is None:
        errors.append(Finding(shown, 1, "permissions",
                              "no top-level `permissions:`; add `permissions: contents: read` "
                              "and grant more only on the jobs that need it"))
    return Result(errors, notices, uses_count)


def _check_top_permissions_value(shown: str, number: int, raw: str, value: str) -> list[Finding]:
    if not raw or value == "{}":
        return []
    if raw.lstrip().startswith("{"):
        grants = re.findall(r"([\w-]+)\s*:\s*([\w-]+)", raw)
        return [
            Finding(shown, number, "top-level-write",
                    f"top-level `permissions` grants `{scope}: write`; grant it on the job that needs it")
            for scope, level in grants
            if level.lower() == "write"
        ]
    if value.lower() in ("write-all", "read-all"):
        return [Finding(shown, number, "top-level-write",
                        f"top-level `permissions: {value}` is not least privilege; use `contents: read`")]
    return [Finding(shown, number, "permissions", f"cannot verify top-level `permissions: {value}`")]


def _is_image_key(keys: list[str]) -> bool:
    if len(keys) < 3 or keys[0] != "jobs":
        return False
    tail = keys[2:]
    return tail == ["container"] or tail == ["container", "image"] or (
        len(tail) == 3 and tail[0] == "services" and tail[2] == "image"
    )


def collect(paths: list[str]) -> list[Path]:
    """Expand directories (not recursive: GitHub reads only the top of .github/workflows)."""
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files.extend(sorted(f for f in p.iterdir() if f.suffix in (".yml", ".yaml") and f.is_file()))
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(raw)
    return files


def _annotation(kind: str, f: Finding) -> str:
    message = f.message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::{kind} file={f.path},line={f.line},title=workflow guard ({f.rule})::{message}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="*", default=[".github/workflows"],
                        help="workflow files or directories (default: .github/workflows)")
    args = parser.parse_args(argv)
    try:
        files = collect(args.paths)
    except FileNotFoundError as missing:
        print(f"workflow_guard: no such file or directory: {missing}", file=sys.stderr)
        return 2
    if not files:
        print(f"workflow_guard: no *.yml/*.yaml files under {' '.join(args.paths)}", file=sys.stderr)
        return 2

    gha = os.environ.get("GITHUB_ACTIONS") == "true"
    all_errors: list[Finding] = []
    uses_count = 0
    for f in files:
        result = check_file(f)
        all_errors.extend(result.errors)
        uses_count += result.uses
        for n in result.notices:
            print(_annotation("notice", n) if gha else f"{n.path}:{n.line}: notice [{n.rule}] {n.message}")
    for e in all_errors:
        print(_annotation("error", e) if gha else f"{e.path}:{e.line}: error [{e.rule}] {e.message}")
    verdict = "FAIL" if all_errors else "OK"
    print(f"workflow_guard: {verdict}: {len(files)} files, {uses_count} `uses:` references, "
          f"{len(all_errors)} findings")
    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main())
