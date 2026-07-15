"""Module 3 mesh agent — heartbeat + network-drop probes to ZeroShield control."""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request

CONTROL_URL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
AGENT_API_KEY = os.environ.get("AGENT_API_KEY", "")
ORG_SLUG = os.environ.get("ORGANIZATION_SLUG", "zeroshield")
CLUSTER_NAME = os.environ.get("CLUSTER_NAME", "kind-module3")
HEARTBEAT_SEC = int(os.environ.get("HEARTBEAT_SECONDS", "30"))
PROBE_UNAUTHORIZED = os.environ.get("PROBE_UNAUTHORIZED", "1") not in ("0", "false", "False")
UNAUTHORIZED_HOST = os.environ.get("UNAUTHORIZED_HOST", "vector-db.ai-mesh-m3.svc.cluster.local")
UNAUTHORIZED_PORT = int(os.environ.get("UNAUTHORIZED_PORT", "8080"))


def post(path: str, body: dict) -> None:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{CONTROL_URL}{path}",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {AGENT_API_KEY}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def heartbeat() -> None:
    hostname = socket.gethostname()
    body = {
        "organization_slug": ORG_SLUG,
        "cluster_name": CLUSTER_NAME,
        "k8s_version": "1.29",
        "cilium_enabled": True,
        "status": "healthy",
        "pods": [
            {
                "namespace": "ai-mesh-m3",
                "pod_name": "ai-model",
                "workload_type": "model",
                "sidecar_attached": True,
                "mtls_status": "healthy",
            },
            {
                "namespace": "ai-mesh-m3",
                "pod_name": "vector-db",
                "workload_type": "vector-db",
                "sidecar_attached": True,
                "mtls_status": "healthy",
            },
            {
                "namespace": "ai-mesh-m3",
                "pod_name": f"agent-{hostname}",
                "workload_type": "agent",
                "sidecar_attached": False,
                "mtls_status": "n/a",
            },
        ],
    }
    post("/api/module3/ingest/cluster-heartbeat/", body)
    print("heartbeat ok", flush=True)


def probe_drop() -> None:
    """Attempt unauthorized connect; report Cilium/policy drop to control."""
    dropped = False
    reason = "Unauthorized probe to vector-db without AI labels"
    try:
        with socket.create_connection((UNAUTHORIZED_HOST, UNAUTHORIZED_PORT), timeout=3):
            dropped = False
            reason = "Unexpected allow — policy may be missing"
    except OSError as exc:
        dropped = True
        reason = f"Cilium/policy drop: {exc}"

    if dropped or PROBE_UNAUTHORIZED:
        post(
            "/api/module3/ingest/network-event/",
            {
                "organization_slug": ORG_SLUG,
                "cluster_name": CLUSTER_NAME,
                "layer": "ebpf",
                "action": "drop",
                "source_ref": f"agent/{socket.gethostname()}",
                "dest_ref": f"vector-db:{UNAUTHORIZED_PORT}",
                "reason": reason,
            },
        )
        print("network drop reported", reason, flush=True)


def main() -> None:
    if not AGENT_API_KEY:
        raise SystemExit("AGENT_API_KEY required")
    while True:
        try:
            heartbeat()
            if PROBE_UNAUTHORIZED:
                probe_drop()
        except urllib.error.HTTPError as exc:
            print("http error", exc.code, exc.read()[:200], flush=True)
        except Exception as exc:  # noqa: BLE001
            print("agent error", exc, flush=True)
        time.sleep(HEARTBEAT_SEC)


if __name__ == "__main__":
    main()
