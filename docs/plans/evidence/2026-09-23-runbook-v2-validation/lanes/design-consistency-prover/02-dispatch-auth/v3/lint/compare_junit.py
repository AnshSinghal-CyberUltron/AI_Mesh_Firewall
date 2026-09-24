"""Compare two pytest junit XML files; fail on outcome drift (LGW01-5)."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _outcomes(path: Path) -> dict[str, str]:
    root = ET.parse(path).getroot()
    found: dict[str, str] = {}
    for case in root.iter("testcase"):
        name = f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}"
        status = "passed"
        for child in list(case):
            tag = child.tag.split("}")[-1]
            if tag in {"failure", "error", "skipped"}:
                status = tag
                break
        found[name] = status
    return found


def main(argv: list[str]) -> int:
    left = Path(argv[1])
    right = Path(argv[2])
    a = _outcomes(left)
    b = _outcomes(right)
    if a != b:
        keys = sorted(set(a) | set(b))
        for key in keys:
            if a.get(key) != b.get(key):
                print(f"drift {key} {a.get(key)!r} -> {b.get(key)!r}")
        return 1
    print(f"lgw01_5_identical cases={len(a)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
