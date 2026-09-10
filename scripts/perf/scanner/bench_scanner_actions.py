"""Input-scanner throughput per vCPU, per action — allow / block / redact / flag.

The question this answers is NOT "how fast is a request" — that is `scripts/perf/e2e/load.py`,
and a request costs ~27.3 ms of CPU, most of it `redact_all` building the pipeline_trace.

This measures the **input scanner** alone: `policy_engine.evaluate` against the real
compiled bundle, which is what has to replace the Haiku Tier-2 model's allow/block/redact/
flag decision. It is single-threaded and CPU-bound by construction, so RPS/vCPU is
`1000 / ms_per_call`.

Actions are the compiled bundle's own, not synthetic: `monitor` IS the lattice's name for
flag (ACTION_ORDER: block 5 > redact 4 > rewrite 3 > model_downgrade 2 > monitor 1 >
allow 0).

    python bench_scanner_actions.py --bundle compiled.json [--prompt-chars 4096] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "gateway"))
sys.path.insert(0, str(ROOT / "gateway" / "ai_mesh_gateway"))
sys.path.insert(0, str(ROOT / "shared"))

import policy_engine as PE  # noqa: E402


def _filler(n: int, seed: str) -> str:
    base = (f"[{seed}] Summarise the deployment runbook and list the rollback steps. ")
    return (base * (n // len(base) + 1))[:n]


def build_cases(bundle: list[dict], prompt_chars: int) -> dict[str, str]:
    """One prompt per action, each drawn from the REAL rules so the trigger is genuine."""
    by_action: dict[str, list[str]] = {}
    for pol in bundle:
        for rule in pol.get("rules") or []:
            act = rule.get("action")
            cond = rule.get("condition") or {}
            kws = cond.get("keywords") or []
            if act and kws:
                by_action.setdefault(act, []).extend(k for k in kws if isinstance(k, str))

    cases = {"allow": _filler(prompt_chars, "benign")}
    for action, label in (("block", "block"), ("redact", "redact"), ("monitor", "flag")):
        kw = (by_action.get(action) or [""])[0]
        body = _filler(max(0, prompt_chars - len(kw) - 2), label)
        # trigger near the END, the worst case for a scanner that can short-circuit
        cases[label] = f"{body} {kw}"
    return cases


def bench(fn, n: int) -> tuple[float, float, float]:
    for _ in range(max(5, n // 20)):
        fn()
    samples = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t) * 1000)
    samples.sort()
    return (statistics.median(samples),
            samples[min(len(samples) - 1, int(0.95 * len(samples)))],
            samples[min(len(samples) - 1, int(0.99 * len(samples)))])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--prompt-chars", type=int, default=4096)
    ap.add_argument("-n", type=int, default=300)
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    raw = json.loads(Path(a.bundle).read_text())
    policies = raw["policies"] if isinstance(raw, dict) else raw
    rules = sum(len(p.get("rules") or []) for p in policies)

    print(f"host    : {platform.processor() or platform.machine()}  "
          f"python {platform.python_version()}")
    try:
        cpu = [l for l in open("/proc/cpuinfo") if l.startswith("model name")][0].split(":")[1].strip()
        print(f"cpu     : {cpu}")
    except Exception:  # noqa: BLE001
        pass
    print(f"bundle  : {len(policies)} policies, {rules} rules")
    print(f"prompt  : {a.prompt_chars} chars, trigger at the END (worst case)\n")

    cases = build_cases(policies, a.prompt_chars)
    print(f"{'action':<10}{'verdict':<10}{'p50 ms':>9}{'p95 ms':>9}{'p99 ms':>9}"
          f"{'RPS/vCPU':>11}{'  <20ms?':>9}")
    out = {}
    for label, prompt in cases.items():
        got = PE.evaluate(prompt, "", policies).action
        p50, p95, p99 = bench(lambda p=prompt: PE.evaluate(p, "", policies), a.n)
        rps = 1000.0 / p50 if p50 else 0.0
        out[label] = {"verdict": got, "p50_ms": p50, "p95_ms": p95, "p99_ms": p99,
                      "rps_per_vcpu": rps}
        print(f"{label:<10}{got:<10}{p50:>9.3f}{p95:>9.3f}{p99:>9.3f}{rps:>11.0f}"
              f"{'  yes' if p99 < 20 else '  NO':>9}")

    slowest = max(v["p99_ms"] for v in out.values())
    worst_rps = min(v["rps_per_vcpu"] for v in out.values())
    print(f"\nworst-case p99 across all four actions: {slowest:.3f} ms")
    print(f"worst-case throughput:                  {worst_rps:.0f} RPS per vCPU")
    print(f"\nThis is the SCANNER only. A full 9-stage request costs ~27.3 ms of CPU "
          f"(~37 RPS/vCPU),\ndominated by redact_all building the pipeline_trace — see "
          f"docs/perf/evidence/.")

    if a.json:
        Path(a.json).write_text(json.dumps(
            {"cpu_count": os.cpu_count(), "policies": len(policies), "rules": rules,
             "prompt_chars": a.prompt_chars, "results": out}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
