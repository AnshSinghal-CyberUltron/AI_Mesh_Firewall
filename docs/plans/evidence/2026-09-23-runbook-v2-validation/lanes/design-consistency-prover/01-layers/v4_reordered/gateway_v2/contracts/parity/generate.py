"""Write a C2 coverage manifest. Corpus itself is generated, not committed."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from gateway_v2.contracts.parity.c2 import coverage_gaps, coverage_report, generate_c2
from gateway_v2.contracts.parity.clock import FrozenClock
from gateway_v2.contracts.parity.types import C2_TARGET


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    n = C2_TARGET
    out = Path("c2-coverage.json")
    if args:
        n = int(args[0])
    if len(args) > 1:
        out = Path(args[1])
    records = generate_c2(n, FrozenClock(epoch=1_704_067_200))
    report = coverage_report(records)
    gaps = list(coverage_gaps(records))
    count = len(records)
    payload = {
        "n": count,
        "target": C2_TARGET,
        "cells": report,
        "gaps": gaps,
        "sanitized_sample": records[4].prompt,
    }
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"n": count, "gaps": gaps, "path": str(out)}))
    return 0 if not gaps and count >= C2_TARGET else 1


if __name__ == "__main__":
    raise SystemExit(main())
