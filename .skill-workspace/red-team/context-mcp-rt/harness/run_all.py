"""Run all 5 red-team lanes end-to-end against the REPLICA target, resetting
the accumulated findings ledger first so ALL_FINDINGS.json reflects exactly
one clean run (no duplicate/stale entries from iterative harness development).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import common

import lane1_context_minimization as lane1
import lane2_field_redaction as lane2
import lane3_cross_mcp_isolation as lane3
import lane4_compliance_bypass as lane4
import lane5_injection_exfil as lane5

LANES = [
    ("Lane 1: Context minimization / least-privilege bypass", lane1),
    ("Lane 2: Field-level redaction & PII leakage", lane2),
    ("Lane 3: Cross-tool / cross-MCP context leakage", lane3),
    ("Lane 4: Compliance tag bypass", lane4),
    ("Lane 5: Prompt injection / tool confusion / privilege escalation / exfil", lane5),
]


def main() -> None:
    if common.FINDINGS_PATH.exists():
        common.FINDINGS_PATH.unlink()

    all_findings = []
    print("=" * 100)
    print("EXTERNAL RED-TEAM ASSESSMENT — Context Assembly & MCP Guardrails")
    print(f"Target: {common.base_url_for('replica')} (replica-authenticated, org={common.ORG_SLUG})")
    print("=" * 100)

    for label, mod in LANES:
        print(f"\n--- {label} ---")
        results = mod.run()
        findings = mod.summarize(results)
        for f in findings:
            print(f"  [{f.verdict:12s}] {f.id:8s} sev={f.severity:8s} {f.title}")
        all_findings.extend(findings)

    # Tally by severity / verdict
    by_sev = {}
    by_verdict = {}
    for f in all_findings:
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
        by_verdict[f.verdict] = by_verdict.get(f.verdict, 0) + 1

    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)
    print("By severity:", json.dumps(by_sev, indent=2))
    print("By verdict:", json.dumps(by_verdict, indent=2))

    fails = [f for f in all_findings if f.verdict == "FAIL"]
    print(f"\nTotal findings: {len(all_findings)} | FAIL (bypass achieved): {len(fails)}")
    for f in fails:
        print(f"  ⚠ {f.id} [{f.severity}] {f.title}")

    overall = "FAIL" if any(f.severity in ("CRITICAL", "HIGH") for f in fails) else (
        "FAIL" if fails else "PASS"
    )
    print(f"\nOVERALL VERDICT: {overall}")

    summary_path = common.WORKDIR / "evidence" / "RUN_SUMMARY.json"
    summary_path.write_text(json.dumps({
        "by_severity": by_sev,
        "by_verdict": by_verdict,
        "total_findings": len(all_findings),
        "fail_count": len(fails),
        "overall_verdict": overall,
        "findings": [f.to_dict() for f in all_findings],
    }, indent=2, default=str))
    print(f"\nWrote run summary to {summary_path}")


if __name__ == "__main__":
    main()
