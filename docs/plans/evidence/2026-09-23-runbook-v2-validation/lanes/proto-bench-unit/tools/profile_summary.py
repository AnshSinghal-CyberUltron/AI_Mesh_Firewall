#!/usr/bin/env python3
"""Summarize py-spy raw (collapsed) profiles: where the worker / owner CPU goes.

  profile_summary.py prof-*.txt

Each line of a py-spy raw file is "frame;frame;...;frame COUNT" (root first). Samples were taken with
--nonblocking and without --idle, so only threads that were running Python (or native code called from
it) are counted. Two views per file:
  * by pipeline stage: the stage owning the deepest rvproto frame (edge/admit/detect/egress/...),
    with named sub-buckets for the known hot functions;
  * by leaf library (what the innermost frame is: aiohttp, uvicorn/httptools, asyncio, tokenizers, ...).
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

STAGE_RULES = (  # (bucket, regex on the whole stack text) first match wins, most specific first
    ("detect: PG2 tokenization (tokenizers.encode)", r"semantic\.py.*windows|tokenizers"),
    ("detect: canonicalize", r"detect/canon\.py"),
    ("detect: guard IPC client", r"detect/guard/ipc_client\.py"),
    ("egress SSE: holdback", r"detect/holdback\.py"),
    ("egress SSE: output scan / resolve per piece", r"egress/stream\.py.*(inspect\.py|matcher\.py|resolver\.py|deterministic\.py)"),
    ("egress SSE: parse/re-frame/emit", r"egress/stream\.py|egress/sse\.py"),
    ("egress JSON", r"egress/jsonout\.py"),
    ("detect: deterministic scan (hyperscan)", r"detect/deterministic\.py|detect/matcher\.py"),
    ("dispatch: provider client (aiohttp)", r"dispatch/provider\.py"),
    ("admit", r"rvproto/admit/"),
    ("audit", r"rvproto/audit/"),
    ("plan", r"rvproto/plan/"),
    ("metrics", r"runtime/metrics\.py"),
    ("edge (request parse, respond, errors)", r"rvproto/edge/"),
    ("guard owner: batcher / onnx", r"detect/guard/(batcher|owner|local_onnx)\.py"),
    ("other rvproto", r"/rvproto/"),
)
LIBS = (("aiohttp", "aiohttp"), ("uvicorn/httptools", "uvicorn|httptools"), ("asyncio/uvloop", "asyncio|uvloop"),
        ("tokenizers", "tokenizers"), ("orjson", "orjson"), ("hyperscan", "hyperscan"), ("numpy", "numpy"),
        ("onnxruntime", "onnxruntime"), ("redis", "redis"), ("json", r"/json/"))


def main() -> int:
    for f in sys.argv[1:]:
        stages: Counter = Counter()
        leaf: Counter = Counter()
        total = 0
        for line in Path(f).read_text().splitlines():
            if not line.strip() or " " not in line:
                continue
            stack, n = line.rsplit(" ", 1)
            try:
                n = int(n)
            except ValueError:
                continue
            total += n
            for name, rx in STAGE_RULES:
                if re.search(rx, stack):
                    stages[name] += n
                    break
            else:
                stages["outside rvproto frames (event loop, libs)"] += n
            innermost = stack.split(";")[-1]
            lname = next((ln for ln, rx in LIBS if re.search(rx, innermost)), None)
            if lname is None:
                m = re.search(r"\(([^)]*)\)", innermost)
                lname = (m.group(1).split(":")[0].split("/")[-1] if m else innermost[:40])
            leaf[lname] += n
        print(f"== {Path(f).name}: {total} samples (100 Hz, non-idle)")
        for k, v in stages.most_common():
            print(f"   {100 * v / total:5.1f}%  {k}")
        print("   leaf frames (top 12):")
        for k, v in leaf.most_common(12):
            print(f"   {100 * v / total:5.1f}%  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
