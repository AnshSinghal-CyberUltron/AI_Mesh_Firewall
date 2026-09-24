#!/usr/bin/env python3
"""poisson_check.py RUN [RUN ...]: are the arrivals at each guard owner Poisson-like?

Each owner is one FIFO queue with ONE server (the B=1 inference thread). From the owner's own histograms over the
measurement window (snap-meas_start -> snap-meas_end deltas): lambda = requests/s, service S = guard_exec_ns per
request (all of its windows, run back to back), measured wait = guard_queue_ns (receipt -> exec start).
Pollaczek-Khinchine gives the mean wait of an M/G/1 queue (Poisson arrivals, same S distribution):
    W_pk = lambda * E[S^2] / (2 * (1 - rho)),  rho = lambda * E[S]      and, by PASTA, P(wait > 0) = rho.
measured/W_pk ~ 1 and P(wait) ~ rho  => the owner sees Poisson-like arrivals;
measured/W_pk << 1                  => smoother than Poisson (e.g. one owner fed by a constant-rate generator).
The wait histogram has a ~0.09 ms floor (frame receipt -> dequeue), subtracted as the p25 of the wait histogram.
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pbu_analyze_ref import bucket_value  # noqa: E402

RAW = Path("/home/contact_cyberultron_com/rv-evidence-raw/proto-bench-split/runs")


def delta(a: dict, b: dict, name: str) -> tuple[Counter, int, float]:
    ha, hb = a["hist"].get(name, {"b": {}, "n": 0, "sum": 0}), b["hist"].get(name, {"b": {}, "n": 0, "sum": 0})
    c = Counter()
    for k, v in hb["b"].items():
        d = v - ha["b"].get(k, 0)
        if d:
            c[int(k)] += d
    return c, hb["n"] - ha["n"], hb["sum"] - ha["sum"]


def q(c: Counter, n: int, p: float) -> float:
    acc = 0
    for k in sorted(c):
        acc += c[k]
        if acc >= p * n:
            return bucket_value(k) / 1e6
    return 0.0


def one(run: str) -> list[dict]:
    out = []
    step = json.loads((RAW / run / "step.json").read_text())
    arrivals = "poisson" if "poisson" in step.get("olg_flags", "") else "const"
    for g in sorted((RAW / run).glob("rv-split-guard-*")):
        s0, s1 = g / "sut" / "snap-meas_start", g / "sut" / "snap-meas_end"
        sched = json.loads((g / "sut" / "schedule.json").read_text())
        dur = sched["meas_end"] - sched["meas_start"]
        for f in sorted(s1.glob("owner-*.json")):
            a, b = json.loads((s0 / f.name).read_text()), json.loads(f.read_text())
            ce, ne, se = delta(a, b, "guard_exec_ns")
            cq, nq, sq = delta(a, b, "guard_queue_ns")
            if ne <= 0 or nq <= 0:
                continue
            lam = ne / dur
            es = se / ne / 1e9
            es2 = sum(bucket_value(k) ** 2 * v for k, v in ce.items()) / ne / 1e18
            rho = lam * es
            wpk = lam * es2 / (2 * (1 - rho)) * 1e3 if rho < 1 else float("inf")
            floor = q(cq, nq, 0.25)
            wmeas = max(sq / nq / 1e6 - floor, 0.0)
            pwait = sum(v for k, v in cq.items() if bucket_value(k) / 1e6 > floor + 0.25) / nq
            out.append({"run": run, "arrivals": arrivals, "guards": len(list((RAW / run).glob("rv-split-guard-*"))),
                        "owner": g.name, "lambda": round(lam, 1), "E_S_ms": round(es * 1e3, 2),
                        "cv_S": round(((es2 - es * es) ** 0.5) / es, 2), "rho": round(rho, 3),
                        "W_meas_ms": round(wmeas, 3), "W_mg1_ms": round(wpk, 3), "ratio": round(wmeas / wpk, 2) if wpk else None,
                        "P_wait": round(pwait, 3), "q_p99_ms": round(q(cq, nq, 0.99) - floor, 2)})
    return out


def main() -> int:
    print("| run | arrivals (client) | guards | owner | req/s | E[S] ms | cv(S) | rho | mean wait measured ms | M/G/1 mean wait ms | measured / M/G/1 | P(wait) measured (M/G/1: = rho) | wait p99 ms |")
    print("|" + "---|" * 13)
    for run in sys.argv[1:]:
        try:
            rows = one(run)
        except (OSError, ValueError, KeyError) as e:
            print(f"| {run} | error {e} |")
            continue
        for r in rows:
            print(f"| {r['run']} | {r['arrivals']} | {r['guards']} | {r['owner'][-7:]} | {r['lambda']} | {r['E_S_ms']} | {r['cv_S']} | {r['rho']} | "
                  f"{r['W_meas_ms']} | {r['W_mg1_ms']} | {r['ratio']} | {r['P_wait']} | {r['q_p99_ms']} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
