#!/usr/bin/env python3
"""Module 3 Phase 2 e2e — Kind stack + control ingest assertions (Windows-friendly)."""
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


def kubectl(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kubectl", "-n", NS, *args],
        capture_output=True,
        text=True,
        check=check,
    )


def main() -> None:
    if not os.environ.get("AGENT_API_KEY"):
        # Not required for assert-only path if stack already up
        pass

    r = kubectl("get", "deploy", "ai-model", "vector-db", "module3-river")
    if r.returncode != 0:
        print("Stack not installed — run scripts/module3_kind_up.sh first")
        fail("workloads missing: " + (r.stderr or r.stdout))
    ok("deployments present")

    np = kubectl("get", "networkpolicies", "vector-db-allow-ai-model")
    cnp = kubectl("get", "ciliumnetworkpolicies", "vector-db-allow-ai-model")
    if np.returncode != 0 and cnp.returncode != 0:
        fail("NetworkPolicy/CiliumNetworkPolicy missing")
    ok("network policy present")

    svc = json.loads(
        subprocess.check_output(["kubectl", "-n", NS, "get", "svc", "vector-db", "-o", "json"], text=True)
    )
    ports = [p.get("port") for p in svc["spec"]["ports"]]
    if 8000 in ports:
        fail("vector-db Service still exposes 8000")
    if 8443 not in ports:
        fail("vector-db Service missing 8443")
    ok("localhost gauntlet: Service has no port 8000")

    login_body = json.dumps({"email": EMAIL, "password": PASS}).encode()
    tok = json.load(
        urllib.request.urlopen(
            urllib.request.Request(
                f"{CONTROL}/api/auth/token/",
                data=login_body,
                headers={"Content-Type": "application/json"},
            ),
            timeout=30,
        )
    )["access"]
    headers = {"Authorization": f"Bearer {tok}"}

    def get(path: str) -> dict:
        req = urllib.request.Request(f"{CONTROL}{path}", headers=headers)
        return json.load(urllib.request.urlopen(req, timeout=30))

    topo = None
    deadline = time.time() + POLL
    while time.time() < deadline:
        topo = get("/api/module3/k8s-firewall/topology/")
        if topo.get("clusters"):
            break
        time.sleep(5)
    if not (topo and topo.get("clusters")):
        fail(f"topology empty — agent heartbeats not reaching control ({CONTROL})")
    ok("topology non-empty")

    drops = get("/api/module3/k8s-firewall/network-events/?period=24h&action=drop")
    if not drops.get("results"):
        fail("no network drops")
    ok("network drops present")

    q = get("/api/module3/k8s-firewall/embedding-queue/?period=24h&status=quarantined")
    if not q.get("results"):
        kubectl("delete", "pod", "river-poke", "--ignore-not-found")
        payload = (
            '{"collection":"corp-docs","anomaly_score":0.99,'
            '"force_quarantine":true,"quarantine_reason":"phase2 e2e"}'
        )
        poke_cmd = (
            f"redis-cli -h module3-redis LPUSH module3:embeddings '{payload}' && echo SEEDED"
        )
        subprocess.run(
            [
                "kubectl",
                "-n",
                NS,
                "run",
                "river-poke",
                "--rm",
                "--restart=Never",
                "--image=redis:7-alpine",
                "--command",
                "--",
                "sh",
                "-c",
                poke_cmd,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        deadline = time.time() + POLL
        while time.time() < deadline:
            q = get("/api/module3/k8s-firewall/embedding-queue/?period=24h&status=quarantined")
            if q.get("results"):
                break
            time.sleep(5)
    if not q.get("results"):
        fail("no quarantined embeddings")
    ok("river quarantine present")

    inc = get("/api/module2/incidents/?page_size=50")
    titles = " ".join(str(r.get("title") or "") for r in (inc.get("results") or []))
    if not (
        ("network drop" in titles.lower())
        or ("Embedding quarantined" in titles)
        or ("quarantined" in titles.lower())
    ):
        fail("Module 2 missing Module 3 incidents (are workers up?): " + titles[:500])
    ok("Module 2 SOC incidents present")
    print("=== Module 3 Phase 2 e2e PASSED ===")


if __name__ == "__main__":
    main()
