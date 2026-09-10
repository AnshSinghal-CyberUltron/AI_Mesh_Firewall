"""Write scorecard.md + REPORT.md from cell JSON. No secrets."""
from __future__ import annotations

import json
from pathlib import Path

EVIDENCE = Path("/home/contact_cyberultron_com/aimesh-p0-task0/docs/perf/evidence/2026-09-10-p0-task0-honesty")


def _load(name: str) -> dict:
    p = EVIDENCE / name
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def main() -> None:
    pre = _load("preflight.json")
    a = _load("cells/A.json")
    tax = (a.get("firewall_tax_ms") or {})
    wall = (a.get("wall_ms") or {})
    stages = a.get("stages") or {}
    ins = (stages.get("input_scan") or {})
    pol = (stages.get("policy") or {})
    og = (stages.get("output_guardrail") or {})
    score = f"""# P0.0 scorecard (SHA a67337fb / aimf_p0)

| Claim | Result |
|---|---|
| (a) Cell_A Firewall_Tax p99 vs 20 ms | {a.get("20ms_tax", "missing")} (p50={tax.get("p50")} p99={tax.get("p99")} n={tax.get("n")}) |
| (b) Cell_A Wall | N/A-not-tax (p50={wall.get("p50")} p99={wall.get("p99")}) |
| (c) MASTER ≤12 ms latency (SLO F / T_addon_pre) | compare p50 tax {tax.get("p50")} to 12 ms — **latency**, not CPU% |
| (d) stub ≠ capacity | PASS if capacity_eligible is false: {a.get("capacity_eligible")} |
| (e) T2-on | N/A-measured (Cell_A ENABLE_TIER2=false) |
| (f) PG2 4.1/5.9 ms | N/A-off-SHA |
| (g) 100k RPS | N/A |

Cell_A stage p50/p99 ms: policy={pol.get("p50")}/{pol.get("p99")} input_scan={ins.get("p50")}/{ins.get("p99")} output_guardrail={og.get("p50")}/{og.get("p99")}
preflight={pre.get("preflight")} excluded_A={a.get("excluded")} cpu={a.get("cpu_mid_window")} container={a.get("gateway_container")}
capacity_eligible must stay false on stub (row d).
"""
    (EVIDENCE / "scorecard.md").write_text(score)
    report = """# P0.0 REPORT

This pack overwrites on each `p0_run.sh` invocation.

## Instrument notes (this SHA)

- Firewall_Tax is non-stream `pipeline_trace.total_latency_ms − model_output_ms`.
- Streaming was **not** run. On this SHA stream `total_latency_ms` is `now − provider_start_ts` (pre-model stages excluded). Stream `total − model_output` **aliases** `ttft_ms` (rounding / setdefault). That is not Firewall_Tax.
- `stage_sum` may exceed `total_latency_ms`. Neither stage-sum nor the TTFT alias is the 20 ms comparator.
- `honesty.full_nine_stages` is two timers (`input_scan` p50>0 and `output_guardrail` p50>0), not proof all nine stages ran. Counted_Samples require each of nine `action != skip`.
- Wall is `pipeline_trace.total_latency_ms`, labelled N/A-not-tax. Not httpx wall_ms.
- Grounding is **pinned off** (`GATEWAY_OUTPUT_GROUNDING_ENABLED=false`).
- Stub RPS is invalid for capacity (`capacity_eligible=false`).
- Do **not** quote aimesh-dev 13.6 RPS / p99 17.60 ms as this SHA’s result.
- Block / redact / size / negative tax (if present) is **N/A-not-20ms**.
- Size cell must be 4096 non-repetitive letters (digit runs → PCI; repeated tokens → input_scan DoS).
- Org inference catalog is harness-seeded (`gpt-4o-mini` dummy key). Stub still intercepts LiteLLM; empty routing is 422 `no_provider_configured`.
- Negative cell (scans-off / empty policies) is **not run** in this pack.
"""
    (EVIDENCE / "REPORT.md").write_text(report)
    print("wrote scorecard.md REPORT.md")


if __name__ == "__main__":
    main()
