#!/usr/bin/env python3
"""Instrument-honesty report (HARNESS_SPEC.md §5): compare harness metrics against the delays the
rvproxy ACTUALLY injected (its own per-request log, same run).

Each case is a run directory produced by deploy/step.sh (with PROXY_VM set for proxy cases):
  RUN/analysis/summary.json  and  RUN/<proxy-vm>/proxy/proxy.jsonl[.zst]
The floor case (proxy, no delays) is the reference: expected shifts are measured against it.

Checks (tolerances are arguments):
  pre-delay case : median T_addon_total - floor median  ~= median injected pre-delay        (|err| <= --tol-ms)
                   median T_addon_first shifts the same way
  hold case      : T_release_lag_max median - floor median ~= median injected hold          (|err| <= --tol-ms)
                   T_addon_total median unchanged vs floor (|shift| <= --tol-ms): totals hide the hold
                   p99 T_fw_addon >= slo (a sub-20 ms claim must FAIL)
  TTFT sweep     : T_addon_total / T_fw_addon medians stay within --tol-ms of the floor while client TTFT
                   tracks provider TTFT (provider delay is never charged to the firewall)

Usage: honesty.py --floor RUN0 --pre RUN1 --hold RUN2 [--both RUN3] [--ttft RUNa RUNb ...] --out DIR
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze as A  # noqa: E402


def summary(run: str) -> dict:
    return json.loads((Path(run) / "analysis" / "summary.json").read_text())


def proxy_truth(run: str) -> dict:
    files = sorted(Path(run).glob("*/proxy/proxy.jsonl*"))
    if not files:
        return {}
    pre, hold, n, held = [], [], 0, 0
    for f in files:
        for r in A.read_jsonl(f):
            n += 1
            pre.append(r.get("pre_delay_actual_ns", 0) / 1e6)
            if r.get("held_event"):
                held += 1
                hold.append(r.get("hold_actual_ns", 0) / 1e6)
    med = lambda xs: round(statistics.median(xs), 4) if xs else 0.0
    return {"requests": n, "pre_delay_ms_median": med(pre), "pre_delay_ms_max": round(max(pre), 4) if pre else 0,
            "held_requests": held, "hold_ms_median": med(hold), "hold_ms_max": round(max(hold), 4) if hold else 0}


def m(s: dict, key: str, q: str = "p50"):
    return (s.get(key) or {}).get(q)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--floor", required=True)
    ap.add_argument("--direct", default=None, help="optional DIRECT run at the same rate (network floor)")
    ap.add_argument("--pre", default=None)
    ap.add_argument("--hold", default=None)
    ap.add_argument("--both", default=None)
    ap.add_argument("--ttft", nargs="*", default=[])
    ap.add_argument("--tol-ms", type=float, default=1.0)
    ap.add_argument("--slo-ms", type=float, default=20.0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    floor = summary(a.floor)
    rows, checks = [], {}

    def row(name, run):
        s = summary(run)
        t = proxy_truth(run)
        rows.append({"case": name, "run": run, "qualified": s["qualified"], "errors": s["errors"],
                     "addon_total_p50": m(s, "T_addon_total"), "addon_total_p99": m(s, "T_addon_total", "p99"),
                     "addon_first_p50": m(s, "T_addon_first"),
                     "release_lag_p50": m(s, "T_release_lag_max"), "release_lag_p99": m(s, "T_release_lag_max", "p99"),
                     "fw_addon_p50": m(s, "T_fw_addon"), "fw_addon_p99": m(s, "T_fw_addon", "p99"),
                     "client_ttft_p50": m(s, "client_ttft_sse"), "verdict_pass": s["verdict"]["pass"],
                     "proxy_truth": t})
        return s, t

    if a.direct:
        row("direct (network floor)", a.direct)
    fs, ft = row("proxy floor (no injected delay)", a.floor)
    f_tot, f_first, f_lag = m(fs, "T_addon_total"), m(fs, "T_addon_first"), m(fs, "T_release_lag_max")
    if a.pre:
        s, t = row("pre-dispatch delay", a.pre)
        exp = t.get("pre_delay_ms_median", 0)
        d_tot = m(s, "T_addon_total") - f_tot
        d_first = m(s, "T_addon_first") - f_first
        checks["pre: T_addon_total shift == injected pre-delay"] = {"shift_ms": round(d_tot, 4), "injected_ms": exp,
                                                                     "ok": abs(d_tot - exp) <= a.tol_ms}
        checks["pre: T_addon_first shift == injected pre-delay"] = {"shift_ms": round(d_first, 4), "injected_ms": exp,
                                                                     "ok": abs(d_first - exp) <= a.tol_ms}
    if a.hold:
        s, t = row("mid-stream hold", a.hold)
        exp = t.get("hold_ms_median", 0)
        d_lag = m(s, "T_release_lag_max") - f_lag
        d_tot = m(s, "T_addon_total") - f_tot
        checks["hold: T_release_lag_max shift == injected hold"] = {"shift_ms": round(d_lag, 4), "injected_ms": exp,
                                                                     "ok": abs(d_lag - exp) <= a.tol_ms}
        checks["hold: T_addon_total unchanged (totals hide holds)"] = {"shift_ms": round(d_tot, 4), "ok": abs(d_tot) <= a.tol_ms}
        checks["hold: sub-SLO claim fails (p99 T_fw_addon >= slo)"] = {"p99_ms": m(s, "T_fw_addon", "p99"),
                                                                       "ok": (m(s, "T_fw_addon", "p99") or 0) >= a.slo_ms and not s["verdict"]["pass"]}
    if a.both:
        s, t = row("pre-delay + hold", a.both)
        exp_tot = t.get("pre_delay_ms_median", 0)
        exp_lag = t.get("pre_delay_ms_median", 0) + t.get("hold_ms_median", 0)
        checks["both: T_addon_total shift == pre-delay"] = {"shift_ms": round(m(s, "T_addon_total") - f_tot, 4),
                                                            "injected_ms": exp_tot,
                                                            "ok": abs(m(s, "T_addon_total") - f_tot - exp_tot) <= a.tol_ms}
        checks["both: T_release_lag_max shift == pre-delay + hold"] = {"shift_ms": round(m(s, "T_release_lag_max") - f_lag, 4),
                                                                       "injected_ms": round(exp_lag, 4),
                                                                       "ok": abs(m(s, "T_release_lag_max") - f_lag - exp_lag) <= a.tol_ms}
    for run in a.ttft:
        s, _ = row("TTFT sweep " + Path(run).name, run)
        checks[f"ttft {Path(run).name}: T_addon_total within tol of floor"] = {
            "addon_total_p50": m(s, "T_addon_total"), "floor_p50": f_tot, "client_ttft_p50": m(s, "client_ttft_sse"),
            "ok": abs(m(s, "T_addon_total") - f_tot) <= a.tol_ms}
        checks[f"ttft {Path(run).name}: T_fw_addon within tol of floor"] = {
            "fw_addon_p50": m(s, "T_fw_addon"), "floor_p50": m(fs, "T_fw_addon"),
            "ok": abs(m(s, "T_fw_addon") - m(fs, "T_fw_addon")) <= a.tol_ms}
    ok = all(c["ok"] for c in checks.values())
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    doc = {"pass": ok, "tol_ms": a.tol_ms, "checks": checks, "cases": rows}
    (out / "honesty.json").write_text(json.dumps(doc, indent=1) + "\n")
    L = [f"# Instrument honesty — {'PASS' if ok else 'FAIL'} (tolerance {a.tol_ms} ms)", "",
         "| case | qualified | T_addon_total p50/p99 | T_addon_first p50 | T_release_lag_max p50/p99 | T_fw_addon p50/p99 | client TTFT p50 | proxy truth (median pre / hold ms) |",
         "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        t = r["proxy_truth"]
        truth = f"{t.get('pre_delay_ms_median', '-')} / {t.get('hold_ms_median', '-')} (held {t.get('held_requests', '-')})" if t else "-"
        L.append(f"| {r['case']} | {r['qualified']} | {r['addon_total_p50']} / {r['addon_total_p99']} | {r['addon_first_p50']} | "
                 f"{r['release_lag_p50']} / {r['release_lag_p99']} | {r['fw_addon_p50']} / {r['fw_addon_p99']} | {r['client_ttft_p50']} | {truth} |")
    L += ["", "| check | result | detail |", "|---|---|---|"]
    for k, c in checks.items():
        L.append(f"| {k} | {'PASS' if c['ok'] else 'FAIL'} | {json.dumps({x: y for x, y in c.items() if x != 'ok'})} |")
    (out / "honesty.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
