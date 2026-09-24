#!/usr/bin/env python3
"""Add the EXPERIMENT flag RV_EXP_OUTPUT_SCAN (on|off, default on) to an rvproto tree.

  apply_exp_flag.py ROOT      (ROOT contains rvproto/runtime/config.py etc.)

off = the output phase scans nothing and holds nothing (NoScanInspector: hits() -> [],
hold_start() -> len(buf)); SSE frames are still parsed and re-serialized; the stage is reported
out:S. Used only by the proto-bench-unit holdback-attribution measurement. Additive: with the
flag unset the code path is the previous behaviour. Idempotent. Each edit anchors on one whole
line that must occur exactly once.
"""

from __future__ import annotations

import sys
from pathlib import Path


def line_with(text: str, prefix: str, path: Path) -> str:
    hits = [ln for ln in text.splitlines(keepends=True) if ln.startswith(prefix)]
    if len(hits) != 1:
        raise SystemExit(f"{path}: expected exactly one line starting with {prefix!r}, found {len(hits)}")
    return hits[0]


def after(text: str, prefix: str, new: str, path: Path) -> str:
    ln = line_with(text, prefix, path)
    return text.replace(ln, ln + new, 1)


def before(text: str, prefix: str, new: str, path: Path) -> str:
    ln = line_with(text, prefix, path)
    return text.replace(ln, new + ln, 1)


def swap(text: str, old: str, new: str, path: Path) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"{path}: expected exactly one {old!r}")
    return text.replace(old, new, 1)


def main() -> int:
    root = Path(sys.argv[1])
    p = root / "rvproto/runtime/config.py"
    s = p.read_text()
    if "RV_EXP_OUTPUT_SCAN" not in s:
        s = after(s, '    ("RV_GUARD_SOCKET_DIR", ""),',
                  "    # EXPERIMENT (proto-bench-unit holdback attribution only): off = the output phase\n"
                  "    # scans and holds nothing (stage reported out:S). Never a production posture.\n"
                  '    ("RV_EXP_OUTPUT_SCAN", "on"),\n', p)
        s = after(s, "    guard_socket_dir: str", "    exp_output_scan: bool\n", p)
        s = after(s, '        guard_socket_dir=get["RV_GUARD_SOCKET_DIR"]',
                  '        exp_output_scan=get["RV_EXP_OUTPUT_SCAN"].strip().lower() != "off",\n', p)
        s = before(s, "    if s.guard_topology not in ",
                   '    if get["RV_EXP_OUTPUT_SCAN"].strip().lower() not in ("on", "off"):\n'
                   "        raise ValueError(f\"RV_EXP_OUTPUT_SCAN={get['RV_EXP_OUTPUT_SCAN']!r} (on | off)\")\n", p)
        p.write_text(s)
    p = root / "rvproto/edge/inspect.py"
    s = p.read_text()
    if "NoScanInspector" not in s:
        s = before(s, "class Verifier:",
                   "class NoScanInspector(PlanInspector):\n"
                   '    """EXPERIMENT ONLY (RV_EXP_OUTPUT_SCAN=off; proto-bench-unit holdback attribution): the\n'
                   "    output phase detects nothing and holds nothing, so every upstream piece is released as it\n"
                   '    arrives. Responses report the stage as out:S (skipped). Never a production posture."""\n'
                   "\n"
                   "    __slots__ = ()\n"
                   "\n"
                   "    def hits(self, text: str) -> list[Hit]:\n"
                   "        return []\n"
                   "\n"
                   "    def hold_start(self, buf: str) -> int:\n"
                   "        return len(buf)\n"
                   "\n"
                   "\n", p)
        p.write_text(s)
    p = root / "rvproto/edge/state.py"
    s = p.read_text()
    if "NoScanInspector" not in s:
        s = swap(s, "from rvproto.edge.inspect import PlanInspector, Verifier\n",
                 "from rvproto.edge.inspect import NoScanInspector, PlanInspector, Verifier\n", p)
        ln = line_with(s, "            ins = self.inspectors[key] = PlanInspector(self.matcher, self.det, plan)", p)
        s = s.replace(ln, "            cls = PlanInspector if self.s.exp_output_scan else NoScanInspector  # experiment flag\n"
                          + ln.replace("PlanInspector(self.matcher", "cls(self.matcher"), 1)
        p.write_text(s)
    p = root / "rvproto/edge/respond.py"
    s = p.read_text()
    if "exp_output_scan" not in s:
        s = swap(s, 'stages = f"{res.stages},dispatch:E,out:E"\n',
                 "stages = f\"{res.stages},dispatch:E,out:{'E' if st.s.exp_output_scan else 'S'}\"\n", p)
        s = swap(s, 'stages = f"{res.stages},dispatch:E,out:E,audit:',
                 "stages = f\"{res.stages},dispatch:E,out:{'E' if st.s.exp_output_scan else 'S'},audit:", p)
        p.write_text(s)
    print(f"flag applied to {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
