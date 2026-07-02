#!/usr/bin/env python3
"""P10 item #32 — recursive verification of P3–P9 (in-process + live).

Runs Cursor-owned gates in sequence, requiring ROUNDS_GATE consecutive all-green
rounds. Each round includes:

  In-process (broker + sandbox-agent unit suites)
  Live P8  — multi-org harness (15 MCPs, cross-tenant matrix)
  Live P9  — concurrency, load, leakage, oauth/transport (via child harnesses)
  UI P5/P6 — Playwright B1+B2+B4 combined E2E

Frontend ``npm run build`` runs once before the round loop (compile gate).

Env:
  ROUNDS_GATE     consecutive all-green rounds (default 3)
  PYBIN           python for httpx harnesses (default gateway venv)
  SKIP_INPROCESS  1 to skip pytest (not recommended)
  SKIP_PLAYWRIGHT 1 to skip UI gate
  INCLUDE_SUSTAINED 1 to run SUSTAINED=1 phase in multi_org harness (slow)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ROUNDS_GATE = int(os.environ.get("ROUNDS_GATE", "3"))
PYBIN = os.environ.get(
    "PYBIN",
    os.path.join(REPO, "gateway", ".venv", "bin", "python"),
)
SKIP_INPROCESS = os.environ.get("SKIP_INPROCESS", "0") == "1"
SKIP_PLAYWRIGHT = os.environ.get("SKIP_PLAYWRIGHT", "0") == "1"
INCLUDE_SUSTAINED = os.environ.get("INCLUDE_SUSTAINED", "0") == "1"
FINDINGS = os.path.join(REPO, "mcp-parallel", "findings", "p10-32")

P9_HARNESSES = [
    ("concurrency", "mcp_concurrency_live.py", {"ROUNDS": "4", "CONCURRENCY": "8"}, "CONCURRENCY: PASS"),
    ("load", "mcp_load_live.py", {"ROUNDS": "12", "CONCURRENCY": "4", "RETRIES": "2"}, "LOAD: PASS"),
    ("leakage", "mcp_leakage_live.py", {"VICTIM": "org-b"}, "LEAKAGE: PASS"),
    ("oauth", "mcp_oauth_transport_live.py", {}, "OAUTH_TRANSPORT: PASS"),
]


INTER_HARNESS_SLEEP = float(os.environ.get("INTER_HARNESS_SLEEP", "8"))


def _run(cmd, *, cwd=REPO, env=None, timeout=900, marker=None, label="") -> tuple[bool, str]:
    try:
        p = subprocess.run(
            cmd,
            cwd=cwd,
            env=env or os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    out = (p.stdout or "") + (p.stderr or "")
    ok = p.returncode == 0
    if marker:
        ok = ok and marker in out
    if not ok and label:
        fail_path = os.path.join(FINDINGS, f"{label}_fail.log")
        with open(fail_path, "w", encoding="utf-8") as fh:
            fh.write(out[-12000:])
    verdict_line = next(
        (ln for ln in reversed(out.splitlines()) if marker and marker.split(":")[0] in ln),
        out.strip().splitlines()[-1] if out.strip() else f"rc={p.returncode}",
    )
    return ok, verdict_line[:160]


def inprocess_gates() -> list[tuple[str, bool, str]]:
    if SKIP_INPROCESS:
        return [("inprocess", True, "skipped")]
    results = []
    broker_py = os.path.join(REPO, "services", "mcp-broker", ".venv", "bin", "python")
    if not os.path.exists(broker_py):
        broker_py = PYBIN
    ok, tail = _run(
        [broker_py, "-m", "pytest", "tests", "-q", "--tb=no"],
        cwd=os.path.join(REPO, "services", "mcp-broker"),
        timeout=300,
    )
    results.append(("broker-pytest", ok, tail))
    ok, tail = _run(
        [broker_py, "-m", "pytest", "tests", "-q", "--tb=no"],
        cwd=os.path.join(REPO, "services", "mcp-broker", "sandbox-image", "agent"),
        timeout=180,
    )
    results.append(("agent-pytest", ok, tail))
    return results


def frontend_build() -> tuple[bool, str]:
    return _run(["npm", "run", "build"], cwd=os.path.join(REPO, "frontend"), timeout=300)


def multi_org_harness() -> tuple[bool, str]:
    env = dict(os.environ)
    env["ROUNDS"] = "3"
    if INCLUDE_SUSTAINED:
        env["SUSTAINED"] = "1"
    return _run(
        [PYBIN, "-u", os.path.join(HERE, "mcp_multi_org_harness.py")],
        env=env,
        timeout=900,
        marker="HARNESS: GREEN",
        label="multi_org",
    )


def p9_harness(label: str, script: str, env_over: dict, marker: str) -> tuple[bool, str]:
    time.sleep(INTER_HARNESS_SLEEP)
    env = dict(os.environ)
    env.update(env_over)
    return _run(
        [PYBIN, "-u", os.path.join(HERE, script)],
        env=env,
        timeout=900,
        marker=marker,
        label=label,
    )


def playwright_b1_b2_b4() -> tuple[bool, str]:
    if SKIP_PLAYWRIGHT:
        return True, "skipped"
    env = dict(os.environ)
    env["NODE_PATH"] = os.path.join(REPO, "tests", "e2e", "node_modules")
    env["BASE_URL"] = os.environ.get("BASE_URL", "http://127.0.0.1:8180")
    env["SHOT_DIR"] = os.path.join(FINDINGS, "playwright")
    os.makedirs(env["SHOT_DIR"], exist_ok=True)
    return _run(
        ["node", os.path.join(HERE, "playwright_mcp_b1_b2_b4_e2e.mjs")],
        env=env,
        timeout=300,
        marker="ALL PASS",
    )


def run_round(rnd: int) -> tuple[bool, list[dict]]:
    print(f"\n===== P10 RECURSIVE ROUND {rnd}/{ROUNDS_GATE} =====")
    entries: list[dict] = []
    round_ok = True

    for label, ok, tail in inprocess_gates():
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {tail}")
        entries.append({"gate": label, "ok": ok, "summary": tail})
        round_ok = round_ok and ok

    ok, tail = multi_org_harness()
    print(f"  [{'PASS' if ok else 'FAIL'}] multi-org: {tail}")
    entries.append({"gate": "multi-org", "ok": ok, "summary": tail})
    round_ok = round_ok and ok
    time.sleep(INTER_HARNESS_SLEEP)

    for label, script, env_over, marker in P9_HARNESSES:
        ok, tail = p9_harness(label, script, env_over, marker)
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {tail}")
        entries.append({"gate": label, "ok": ok, "summary": tail})
        round_ok = round_ok and ok

    ok, tail = playwright_b1_b2_b4()
    print(f"  [{'PASS' if ok else 'FAIL'}] playwright-b1-b2-b4: {tail}")
    entries.append({"gate": "playwright", "ok": ok, "summary": tail})
    round_ok = round_ok and ok

    print(f"  round {rnd}: {'ALL GREEN' if round_ok else 'NOT all green'}")
    return round_ok, entries


def main() -> int:
    os.makedirs(FINDINGS, exist_ok=True)
    t0 = time.time()
    print(f"P10 RECURSIVE GATE: {ROUNDS_GATE} consecutive all-green rounds")
    print(f"  PYBIN={PYBIN}")

    ok, tail = frontend_build()
    print(f"[{'PASS' if ok else 'FAIL'}] frontend-build: {tail}")
    if not ok:
        print("P10 RECURSIVE GATE: FAIL (frontend build)")
        return 1

    all_green = True
    report_rounds = []
    for rnd in range(1, ROUNDS_GATE + 1):
        round_ok, entries = run_round(rnd)
        report_rounds.append({"round": rnd, "ok": round_ok, "gates": entries})
        if not round_ok:
            all_green = False
            break

    report = {
        "rounds_required": ROUNDS_GATE,
        "all_green": all_green,
        "elapsed_s": round(time.time() - t0, 1),
        "rounds": report_rounds,
        "blocked_items": ["P4.13", "P6.18", "P6.19-P4"],
    }
    report_path = os.path.join(FINDINGS, "recursive_gate_report.json")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    verdict = "PASS (3× all-green)" if all_green else "FAIL"
    print(f"\nP10 RECURSIVE GATE: {verdict}")
    print(f"report -> {report_path}")
    return 0 if all_green else 1


if __name__ == "__main__":
    raise SystemExit(main())
