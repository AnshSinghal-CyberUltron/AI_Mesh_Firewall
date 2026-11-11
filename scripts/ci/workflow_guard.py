#!/usr/bin/env python3
"""Fail when a GitHub Actions workflow is not pinned or not least-privilege.

Rules, checked in every ``*.yml``/``*.yaml`` file under the given paths
(default ``.github/workflows``) and in every local action those files use:

  unpinned-uses   every ``uses:`` is in this repository (``./path``, or GitHub's ``$/path``
                  self-repository form), ``docker://image@sha256:<64 hex>``, or
                  ``owner/repo[/path]@<40-hex commit SHA>``. A tag or branch can be moved to
                  other code after review; a commit SHA cannot.
  version-comment a SHA pin carries its release as a trailing comment (``# v4.2.1``), so a
                  reviewer can read it and Dependabot keeps the comment in step with the SHA.
  local-ref       a ``./path`` / ``$/path`` reference resolves inside the repository to a workflow
                  file or a directory with ``action.yml``/``action.yaml``. That file is checked
                  too, so a local composite action cannot hide an unpinned ``uses:``.
  permissions     a workflow (not a composite ``action.yml``) has a top-level ``permissions:``
                  key. Without it every job gets the repository's default token permissions.
  top-level-write the top-level ``permissions:`` grants only ``read`` or ``none`` (no ``write``,
                  ``write-all`` or ``read-all``); a job that needs more asks for it itself.
  unpinned-image  a literal ``jobs.<id>.container`` / ``container.image`` /
                  ``services.<id>.image``, and a local action's ``runs.image: docker://...``, is
                  pinned by digest (``name:tag@sha256:<64 hex>``). ``${{ ... }}`` expressions
                  are reported as a notice, not checked.
  unverifiable    YAML the line scanner cannot read the way a YAML parser does. The guard fails
                  rather than skip it.

Standard library only, so it runs on any runner without an install step. It reads YAML line
by line and skips block scalars (``run: |``), so text inside a script is never mistaken for a
key. A line scanner can be led astray by YAML that a parser reads differently, so every such
form is an ``unverifiable`` error:
  - control characters and the Unicode line separators U+0085, U+2028 and U+2029;
  - a second YAML document (``---``/``...`` after content);
  - an explicit key (``? key`` / ``: value``);
  - a double-quoted key containing an escape (``"use\\x73":``);
  - a quoted scalar or a flow collection (``[...]``, ``{...}``) that does not close on its line;
  - flow-style ``uses:``, an alias or a block scalar as the ``uses:`` value, a ``uses:`` value on
    the next line.

Exit status: 0 clean, 1 findings, 2 usage error (no workflow files found).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
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
    r"(?P<key>\"[^\"]*\"|'[^']*'|[^\s\"'#{\[\]}&*!|>%@`,?:][^:#]*?)"
    r"\s*:(?:\s+(?P<value>.*)|\s*)$"
)
LEAD_RE = re.compile(r"^\s*(?:-\s+)*")
FLOW_USES_RE = re.compile(r"[{,]\s*[\"']?uses[\"']?\s*:")
# YAML line breaks or non-printables that str.splitlines() or a YAML parser treat differently.
BAD_CHAR_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x85  ]")
EXPLICIT_KEY_RE = re.compile(r"^\s*(?:-\s+)*[?:](?:\s|$)")
FLOW_EXPLICIT_KEY_RE = re.compile(r"[{\[,]\s*\?(?:\s|$)")
ESCAPED_KEY_RE = re.compile(r'"(?:[^"\\]|\\.)*\\(?:[^"\\]|\\.)*"\s*:(?:\s|$)')
DOC_MARKER_RE = re.compile(r"^(---|\.\.\.)(?:\s|$)")
TOP_LEVEL_READS = ("read", "none")


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
    local_refs: list[tuple[int, str]] = field(default_factory=list)  # (line, ./path or $/path)


def _quoted_end(raw: str) -> int:
    """Index just past the quoted scalar that starts at raw[0], or -1 if it does not close."""
    quote, i = raw[0], 1
    while i < len(raw):
        char = raw[i]
        if quote == '"' and char == "\\":
            i += 2
            continue
        if char == quote:
            if quote == "'" and raw[i + 1 : i + 2] == "'":  # '' is an escaped quote
                i += 2
                continue
            return i + 1
        i += 1
    return -1


def _flow_closes(raw: str) -> bool:
    """True when the flow collection starting raw[0] closes on this line."""
    depth, i, previous = 0, 0, ""
    while i < len(raw):
        char = raw[i]
        if char in "\"'" and previous in ("", "[", "{", ",", ":"):
            end = _quoted_end(raw[i:])
            if end == -1:
                return False
            i += end
            previous = "x"
            continue
        if char == "#" and i and raw[i - 1] in " \t":
            break
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth == 0:
                return True
        if not char.isspace():
            previous = char
        i += 1
    return depth == 0


def _strip_value(raw: str) -> tuple[str, str]:
    """Split a one-line scalar into (value, trailing comment); quotes are removed."""
    raw = raw.strip()
    if raw.startswith("#"):
        return "", raw
    if raw[:1] in ("'", '"'):
        end = _quoted_end(raw)
        if end == -1:
            return raw, ""  # unclosed: check_file reports it as unverifiable
        rest = raw[end:].strip()
        return raw[1 : end - 1], rest if rest.startswith("#") else ""
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


def _unverifiable(line: str) -> str | None:
    """A reason the line scanner cannot read this (non-block-scalar) line reliably, or None."""
    if EXPLICIT_KEY_RE.match(line) or FLOW_EXPLICIT_KEY_RE.search(line):
        return "explicit-key syntax (`? key` / `: value`); write `key: value`"
    if ESCAPED_KEY_RE.search(line):
        return "a double-quoted key with an escape sequence; write the key plainly"
    lead = LEAD_RE.match(line)
    rest = line[lead.end():] if lead else line
    match = KEY_RE.match(line)
    candidates = [rest]
    if match and match.group("value"):
        candidates.append(match.group("value").strip())
    for text in candidates:
        if text[:1] in ("'", '"') and _quoted_end(text) == -1:
            return "a quoted scalar that continues on the next line; keep it on one line"
        if text[:1] in ("[", "{") and not _flow_closes(text):
            return "a flow collection that continues on the next line; keep it on one line"
    return None


def check_file(path: Path, shown: str | None = None) -> Result:
    """Check one workflow or composite-action file."""
    shown = shown or str(path)
    errors: list[Finding] = []
    notices: list[Finding] = []
    local_refs: list[tuple[int, str]] = []
    uses_count = 0
    is_action = path.name in ("action.yml", "action.yaml")
    text = path.read_text(encoding="utf-8").removeprefix("﻿")
    for bad in BAD_CHAR_RE.finditer(text):
        number = text.count("\n", 0, bad.start()) + 1
        errors.append(Finding(shown, number, "unverifiable",
                              f"character U+{ord(bad.group()):04X}: YAML and line-based tools "
                              "split or fold lines differently there; remove it"))
    lines = [line.removesuffix("\r") for line in text.split("\n")]

    stack: list[tuple[int, str]] = []  # (column of key, key) for the current mapping path
    scalar_parent: int | None = None  # column of the key that opened a block scalar
    top_permissions_line: int | None = None
    in_top_permissions = False
    seen_content = False

    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if scalar_parent is not None:
            if not stripped or indent > scalar_parent:
                continue  # inside a block scalar: script text, never keys
            scalar_parent = None
        if not stripped or stripped.startswith("#"):
            continue
        if DOC_MARKER_RE.match(line):
            if seen_content or line[3:].strip() and not line[3:].strip().startswith("#"):
                errors.append(Finding(shown, number, "unverifiable",
                                      "more than one YAML document, or content on a document marker; "
                                      "keep one document per file"))
            continue
        if line.startswith("%"):  # %YAML / %TAG directive before the document
            continue
        seen_content = True
        seq_scalar = SEQ_BLOCK_SCALAR_RE.match(line)
        if seq_scalar:
            scalar_parent = len(seq_scalar.group(1))
            continue

        reason = _unverifiable(line)
        if reason:
            errors.append(Finding(shown, number, "unverifiable", f"cannot verify {reason}"))
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
        elif in_top_permissions and len(keys) == 2:
            errors.extend(_check_top_permission_level(shown, number, key, value))

        if key == "uses":
            uses_count += 1
            problem = _check_uses(value, comment)
            if problem:
                errors.append(Finding(shown, number, *problem))
            elif value.startswith(("./", "$/")):
                local_refs.append((number, value))
        elif _is_image_key(keys, is_action) and value:
            if raw_value.strip().startswith(("|", ">")):
                errors.append(Finding(shown, number, "unpinned-image", "cannot verify a block-scalar image"))
            elif "${{" in value:
                notices.append(Finding(shown, number, "unpinned-image",
                                       f"image `{value}` is an expression; pin its source by digest"))
            elif is_action and not value.startswith("docker://"):
                pass  # runs.image: Dockerfile (built from this repository)
            elif not DIGEST_RE.search(value):
                errors.append(Finding(shown, number, "unpinned-image",
                                      f"image `{value}` is not pinned by digest (name:tag@sha256:<64 hex>)"))

    if not is_action and top_permissions_line is None:
        errors.append(Finding(shown, 1, "permissions",
                              "no top-level `permissions:`; add `permissions: contents: read` "
                              "and grant more only on the jobs that need it"))
    return Result(errors, notices, uses_count, local_refs)


def _check_top_permission_level(shown: str, number: int, scope: str, level: str) -> list[Finding]:
    if level.lower() == "write":
        return [Finding(shown, number, "top-level-write",
                        f"top-level `permissions` grants `{scope}: write`; grant it on the job that needs it")]
    if level not in TOP_LEVEL_READS:
        return [Finding(shown, number, "unverifiable",
                        f"cannot verify top-level `{scope}: {level}`; write `read` or `none` plainly")]
    return []


def _check_top_permissions_value(shown: str, number: int, raw: str, value: str) -> list[Finding]:
    if not raw or value == "{}":
        return []
    if raw.lstrip().startswith("{"):
        body = raw.strip()
        if any(c in body for c in "\"'\\!&*?"):
            return [Finding(shown, number, "unverifiable",
                            "cannot verify a quoted, tagged or escaped flow `permissions`; use block style")]
        grants = re.findall(r"([\w-]+)\s*:\s*([\w-]+)", body)
        return [f for scope, level in grants for f in _check_top_permission_level(shown, number, scope, level)]
    if value.lower() in ("write-all", "read-all"):
        return [Finding(shown, number, "top-level-write",
                        f"top-level `permissions: {value}` is not least privilege; use `contents: read`")]
    return [Finding(shown, number, "permissions", f"cannot verify top-level `permissions: {value}`")]


def _is_image_key(keys: list[str], is_action: bool) -> bool:
    if is_action:
        return keys == ["runs", "image"]
    if len(keys) < 3 or keys[0] != "jobs":
        return False
    tail = keys[2:]
    return tail == ["container"] or tail == ["container", "image"] or (
        len(tail) == 3 and tail[0] == "services" and tail[2] == "image"
    )


def resolve_local(root: Path, ref: str) -> tuple[Path | None, str]:
    """Map `./path` or `$/path` to the file GitHub would load, or (None, reason)."""
    target = (root / ref[2:]).resolve()
    if target != root and root not in target.parents:
        return None, f"`{ref}` points outside the repository"
    if target.is_file() and target.suffix in (".yml", ".yaml"):
        return target, ""
    if target.is_dir():
        for name in ("action.yml", "action.yaml"):
            if (target / name).is_file():
                return target / name, ""
    return None, (f"`{ref}` has no workflow file or action.yml/action.yaml under the repository root "
                  f"({root}); run the guard from the repository root or pass --root")


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


def _display(path: Path) -> str:
    try:
        return os.path.relpath(path)
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="*", default=[".github/workflows"],
                        help="workflow files or directories (default: .github/workflows)")
    parser.add_argument("--root", default=".",
                        help="repository root that ./path references resolve against (default: .)")
    args = parser.parse_args(argv)
    try:
        files = collect(args.paths)
    except FileNotFoundError as missing:
        print(f"workflow_guard: no such file or directory: {missing}", file=sys.stderr)
        return 2
    if not files:
        print(f"workflow_guard: no *.yml/*.yaml files under {' '.join(args.paths)}", file=sys.stderr)
        return 2

    root = Path(args.root).resolve()
    gha = os.environ.get("GITHUB_ACTIONS") == "true"
    all_errors: list[Finding] = []
    uses_count = followed = 0
    queue = list(files)
    seen = {f.resolve() for f in files}
    while queue:
        current = queue.pop(0)
        shown = str(current) if current in files else _display(current)
        result = check_file(current, shown)
        all_errors.extend(result.errors)
        uses_count += result.uses
        for n in result.notices:
            print(_annotation("notice", n) if gha else f"{n.path}:{n.line}: notice [{n.rule}] {n.message}")
        for number, ref in result.local_refs:
            target, reason = resolve_local(root, ref)
            if target is None:
                all_errors.append(Finding(shown, number, "local-ref", reason))
            elif target not in seen:
                seen.add(target)
                queue.append(target)
                followed += 1
    for e in all_errors:
        print(_annotation("error", e) if gha else f"{e.path}:{e.line}: error [{e.rule}] {e.message}")
    verdict = "FAIL" if all_errors else "OK"
    print(f"workflow_guard: {verdict}: {len(files)} files (+{followed} local actions followed), "
          f"{uses_count} `uses:` references, {len(all_errors)} findings")
    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main())
