"""Fail when the checked-in GW14b console inventory disagrees with this tree."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

from lint.console_inventory import (
    CONFIG_PATH,
    CONSUMERS_PATH,
    FIELDS_PATH,
    build,
    diff_reads,
    dump,
    repo_root,
)


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _as_dict(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} is not a JSON object")
    return cast(dict[str, object], value)


def _as_reads(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise TypeError("reads is not a list")
    rows: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            raise TypeError("reads entry is not an object")
        rows.append(cast(dict[str, object], item))
    return rows


def compare(repo: Path) -> list[str]:
    fresh = build(repo)
    messages: list[str] = []
    pairs = (
        ("fields", FIELDS_PATH, fresh["fields"]),
        ("cp-consumers", CONSUMERS_PATH, fresh["cp_consumers"]),
        ("config-keys", CONFIG_PATH, fresh["config_keys"]),
    )
    for label, relative, document in pairs:
        path = repo / relative
        if not path.is_file():
            messages.append(f"{relative.as_posix()} is missing; regenerate with --write")
            continue
        committed = _load(path)
        if dump(committed) == dump(document):
            continue
        messages.append(
            f"{relative.as_posix()} does not match a fresh scan; regenerate with --write",
        )
        if label == "fields":
            committed_doc = _as_dict(committed, label)
            fresh_doc = _as_dict(document, label)
            messages.extend(
                diff_reads(_as_reads(committed_doc.get("reads")), _as_reads(fresh_doc["reads"])),
            )
    return messages


def write(repo: Path) -> None:
    document = build(repo)
    targets = (
        (FIELDS_PATH, document["fields"]),
        (CONSUMERS_PATH, document["cp_consumers"]),
        (CONFIG_PATH, document["config_keys"]),
    )
    for relative, payload in targets:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump(payload), encoding="utf-8")
        print(path.relative_to(repo).as_posix())


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    repo = repo_root()
    if "--write" in args:
        write(repo)
        return 0
    messages = compare(repo)
    for message in messages:
        print(message)
    return 1 if messages else 0


if __name__ == "__main__":
    raise SystemExit(main())
