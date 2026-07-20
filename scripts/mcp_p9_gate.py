#!/usr/bin/env python3
"""P10 item #32 — consolidated P9 gate: run the 15-MCP concurrency + load +
leakage (+ oauth/transport) harnesses as ONE suite, N consecutive rounds, and
require EVERY harness to PASS in EVERY round.

This is the completion gate: "the 15-MCP concurrency/load/leakage tests pass 3×
consecutive (in-process + live)". Each child harness is itself a live end-to-end
check against the running gateway/control/broker/sandboxes; this driver just
sequences them and enforces the 3×-all-green requirement with a single verdict.

Env:
  PYBIN   python used to run child harnesses (must have httpx) — default: this python
  ROUNDS_GATE  consecutive all-green rounds required (default 3)
  INCLUDE_OAUTH  1 to include the oauth/transport harness (default 1)
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PYBIN = os.environ.get("PYBIN", sys.executable)
ROUNDS_GATE = int(os.environ.get("ROUNDS_GATE", "3"))
INCLUDE_OAUTH = os.environ.get("INCLUDE_OAUTH", "1") == "1"

# (label, script, env-overrides, PASS-marker)
HARNESSES = [
    ("concurrency", "mcp_concurrency_live.py", {"ROUNDS": "4", "CONCURRENCY": "8"}, "CONCURRENCY: PASS"),
    ("load", "mcp_load_live.py", {"ROUNDS": "12", "CONCURRENCY": "4", "RETRIES": "3"}, "LOAD: PASS"),
    ("leakage", "mcp_leakage_live.py", {"VICTIM": "org-b"}, "LEAKAGE: PASS"),
]

# Settle between harnesses so the concurrency burst's residual control-plane pressure
# (the -32000 saturation boundary) drains before the load harness measures — the
# harnesses each pass standalone; back-to-back they need a brief cooldown.
_SETTLE_SECONDS = float(os.environ.get("GATE_SETTLE_SECONDS", "12"))
if INCLUDE_OAUTH:
    HARNESSES.append(("oauth", "mcp_oauth_transport_live.py", {}, "OAUTH_TRANSPORT: PASS"))


def run_one(label, script, env_over, marker) -> tuple[bool, str]:
    env = dict(os.environ)
    env.update(env_over)
    path = os.path.join(HERE, script)
    if not os.path.exists(path):
        return False, f"{label}: MISSING {script}"
    try:
        p = subprocess.run([PYBIN, "-u", path], env=env, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return False, f"{label}: TIMEOUT"
    out = p.stdout + p.stderr
    tail = next((ln for ln in reversed(out.splitlines()) if marker.split(":")[0] in ln), out.splitlines()[-1] if out.splitlines() else "")
    return (marker in out), f"{label}: {tail.strip()[:120]}"


def main() -> int:
    print(f"P9 GATE: {len(HARNESSES)} harnesses × {ROUNDS_GATE} consecutive rounds (all must PASS)")
    all_green = True
    for rnd in range(1, ROUNDS_GATE + 1):
        print(f"\n===== GATE ROUND {rnd}/{ROUNDS_GATE} =====")
        round_ok = True
        for i, (label, script, env_over, marker) in enumerate(HARNESSES):
            if i > 0 and _SETTLE_SECONDS > 0:
                time.sleep(_SETTLE_SECONDS)  # drain residual pressure between harnesses
            ok, summary = run_one(label, script, env_over, marker)
            print(f"  [{'PASS' if ok else 'FAIL'}] {summary}")
            round_ok = round_ok and ok
        if not round_ok:
            all_green = False
            print(f"  round {rnd}: NOT all green → gate fails")
            break
        print(f"  round {rnd}: ALL GREEN")
    print("\nP9 GATE:", "PASS (3× all-green)" if all_green else "FAIL")
    return 0 if all_green else 1


if __name__ == "__main__":
    raise SystemExit(main())
