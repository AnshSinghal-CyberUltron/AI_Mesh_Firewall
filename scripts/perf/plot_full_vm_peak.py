#!/usr/bin/env python3
"""Plot full-VM peak table from mcp-parallel/findings/full-vm-peak-2026-08-14/verdict.json."""
from __future__ import annotations

import json
from pathlib import Path

OUTDIR = Path("mcp-parallel/findings/full-vm-peak-2026-08-14")


def main() -> int:
    v = json.loads((OUTDIR / "verdict.json").read_text(encoding="utf-8"))
    rows = v.get("table") or []
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib missing — skip plot")
        return 0

    def _series(prefix: str):
        xs, ys, er = [], [], []
        for r in rows:
            lab = str(r.get("label") or "")
            if not lab.startswith(prefix):
                continue
            xs.append(int(r.get("inflight") or 0))
            ys.append(float(r.get("ok_rps") or 0))
            er.append(float(r.get("error_rate") or 0))
        return xs, ys, er

    fig, ax = plt.subplots(2, 1, figsize=(10, 8), sharex=False)
    hx, hy, he = _series("health_")
    cx, cy, ce = _series("chat_")
    if hx:
        ax[0].plot(hx, hy, "o-", label="ok RPS")
        ax[0].plot(hx, [e * max(hy or [1]) for e in he], "s--", label="error_rate×peak")
        ax[0].set_title("/health HTTP ceiling (direct or NLB)")
        ax[0].set_xlabel("in-flight")
        ax[0].set_ylabel("ok RPS")
        ax[0].legend()
        ax[0].grid(True, alpha=0.3)
    if cx:
        ax[1].plot(cx, cy, "o-", color="tab:orange", label="ok RPS unique 9-stage")
        ax[1].set_title("unique-prompt 9-stage (live T2 + routing + BYOK + output guard)")
        ax[1].set_xlabel("in-flight")
        ax[1].set_ylabel("ok RPS")
        ax[1].legend()
        ax[1].grid(True, alpha=0.3)
    fig.tight_layout()
    out = OUTDIR / "peak_plot.png"
    fig.savefig(out, dpi=120)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
