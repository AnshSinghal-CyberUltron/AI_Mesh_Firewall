#!/usr/bin/env python3
"""Module 3 Phase 3 e2e — OPA + Envoy ext_authz under/over quota + Module 2 SOC."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

NS = os.environ.get("NAMESPACE", "ai-mesh-m3")
CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
EMAIL = os.environ.get("TEST_EMAIL", "admin@zeroshield.io")
PASS = os.environ.get("TEST_PASSWORD", "Adm1n!Pass#2024")
POLL = int(os.environ.get("POLL_SEC", "90"))
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def fail(msg: str) -> None:
    print("FAIL:", msg, file=sys.stderr)
    raise SystemExit(1)


def ok(msg: str) -> None:
    print("OK:", msg)


def kubectl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["kubectl", "-n", NS, *args], capture_output=True, text=True)


def http_json(method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{CONTROL}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}


def main() -> None:
    for deploy in ("module3-opa", "module3-authz-shim", "module3-state-sync", "llm-edge"):
        r = kubectl("get", "deploy", deploy)
        if r.returncode != 0:
            fail(f"missing deployment {deploy} — run scripts/module3_kind_up.sh")
    ok("phase3 deployments present")

    cm = kubectl("get", "cm", "module3-envoy-config", "-o", "yaml")
    if "ext_authz" not in (cm.stdout or ""):
        fail("Envoy config missing ext_authz")
    ok("envoy ext_authz configured")

    tok = http_json("POST", "/api/auth/token/", {"email": EMAIL, "password": PASS})["access"]
    # Ensure policies exist
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "module3_phase3_seed_quotas.py")], check=False)

    deadline = time.time() + POLL
    while time.time() < deadline:
        policies = http_json("GET", "/api/module3/api-governance/policies/", token=tok)
        if policies.get("count", 0) >= 1:
            break
        time.sleep(3)
    else:
        fail("no quota policies in control")
    ok("quota policies present")

    def curl_code(name: str, *curl_args: str) -> str:
        """Run curl in a one-shot pod; read status from pod logs (Windows-safe)."""
        kubectl("delete", "pod", name, "--ignore-not-found")
        create = subprocess.run(
            [
                "kubectl",
                "-n",
                NS,
                "run",
                name,
                "--restart=Never",
                "--image=curlimages/curl:8.5.0",
                "--command",
                "--",
                *curl_args,
            ],
            capture_output=True,
            text=True,
        )
        if create.returncode != 0:
            return f"create-fail:{(create.stderr or '')[:80]}"
        for _ in range(60):
            st = kubectl("get", "pod", name, "-o", "jsonpath={.status.phase}")
            if (st.stdout or "").strip() in ("Succeeded", "Failed"):
                break
            time.sleep(2)
        logs = kubectl("logs", name)
        kubectl("delete", "pod", name, "--ignore-not-found")
        out = ((logs.stdout or "") + "\n" + (logs.stderr or "")).strip()
        for tok in reversed(out.split()):
            if tok.isdigit() and len(tok) == 3:
                return tok
        return out[-40:]

    # Wait for OPA data (query from in-cluster python — reliable on Windows)
    deadline = time.time() + POLL
    while time.time() < deadline:
        probe = kubectl(
            "exec",
            "deploy/module3-state-sync",
            "--",
            "python",
            "-c",
            "import urllib.request; print(urllib.request.urlopen('http://module3-opa:8181/v1/data/module3/quotas', timeout=10).read().decode())",
        )
        blob = (probe.stdout or "") + (probe.stderr or "")
        if '"acme"' in blob:
            break
        time.sleep(4)
    else:
        fail("OPA data empty — state-sync not pushing quotas")
    ok("OPA quota data synced")

    # Under-limit via llm-edge Service (prod TPM=1000)
    under_code = curl_code(
        "phase3-under",
        "curl",
        "-s",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "-H",
        "x-tenant-id: acme",
        "-H",
        "x-environment: prod",
        "-H",
        "x-estimated-tokens: 5",
        "http://llm-edge:8080/v1/chat",
    )
    if under_code != "200":
        fail(f"under-limit expected 200 got {under_code!r}")
    ok("under-limit -> 200")

    # Over-limit via acme/dev TPM=10
    over_code = curl_code(
        "phase3-over",
        "curl",
        "-s",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "-H",
        "x-tenant-id: acme",
        "-H",
        "x-environment: dev",
        "-H",
        "x-estimated-tokens: 50",
        "http://llm-edge:8080/v1/chat",
    )
    if over_code != "403":
        fail(f"over-limit expected 403 got {over_code!r}")
    ok("over-limit -> 403 kill-switch")
    deadline = time.time() + POLL
    found_evt = False
    while time.time() < deadline:
        ev = http_json("GET", "/api/module3/api-governance/events/?action=deny&page_size=50", token=tok)
        if ev.get("results"):
            found_evt = True
            break
        time.sleep(3)
    if not found_evt:
        fail("no deny governance events in Module 3")
    ok("governance deny event recorded")

    deadline = time.time() + POLL
    found_inc = False
    while time.time() < deadline:
        inc = http_json("GET", "/api/module2/incidents/?page_size=50", token=tok)
        titles = " ".join(str(r.get("title") or "") for r in (inc.get("results") or []))
        if "kill-switch" in titles.lower() or "api governance" in titles.lower():
            found_inc = True
            break
        time.sleep(3)
    if not found_inc:
        fail("Module 2 missing API governance incident (workers up?)")
    ok("Module 2 SOC incident present")
    print("=== Module 3 Phase 3 e2e PASSED ===")


if __name__ == "__main__":
    main()
