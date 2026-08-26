#!/usr/bin/env python3
"""T-C4: sum compose memory limits from files. Never hard-code the total."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LIMIT_RE = re.compile(
    r"^\s+(?:mem_limit|memory):\s*['\"]?([0-9]+(?:\.[0-9]+)?)\s*([KMG])i?B?['\"]?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def parse_limit_mib(token_num: str, unit: str) -> float:
    n = float(token_num)
    u = unit.upper()
    if u == "K":
        return n / 1024.0
    if u == "M":
        return n
    if u == "G":
        return n * 1024.0
    raise ValueError(f"unknown unit {unit}")


def limits_from_compose(path: Path) -> list[tuple[str, float]]:
    text = path.read_text()
    found = []
    for match in LIMIT_RE.finditer(text):
        mib = parse_limit_mib(match.group(1), match.group(2))
        found.append((match.group(0).strip(), mib))
    return found


def sum_mib(path: Path) -> float:
    return sum(mib for _raw, mib in limits_from_compose(path))


def host_ram_mib(meminfo: Path | None = None) -> float:
    info = (meminfo or Path("/proc/meminfo")).read_text()
    for line in info.splitlines():
        if line.startswith("MemTotal:"):
            kb = float(line.split()[1])
            return kb / 1024.0
    raise RuntimeError("MemTotal not found")


def evaluate(compose_path: Path, host_mib: float | None = None) -> dict:
    host = host_ram_mib() if host_mib is None else host_mib
    total = sum_mib(compose_path)
    cap = 0.8 * host
    return {
        "file": str(compose_path),
        "sum_mib": round(total, 2),
        "host_ram_mib": round(host, 2),
        "cap_80pct_mib": round(cap, 2),
        "ok": total <= cap,
        "ratio": round(total / host, 4) if host else None,
        "limits": limits_from_compose(compose_path),
    }


def main() -> int:
    import json
    import sys

    live = evaluate(REPO / "docker-compose.yml")
    prod = evaluate(REPO / "docker-compose.prod.yml")
    report = {"live": live, "prod": prod, "hardcoded_forbidden": True}
    json.dump(report, sys.stdout, indent=2, default=str)
    print()
    return 0 if live["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
