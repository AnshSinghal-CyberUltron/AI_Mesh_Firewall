"""Confirm each provider base-URL points at the capture proxy (READ-ONLY, prod-safe).

This script NEVER mutates config. The provider base-URLs live in the control-plane
DB (LLMModelConfig.api_base, VectorProviderConfig embedding base, Pinecone host,
MCPServerRegistration.url), so they cannot be confirmed offline — confirming
requires a live, authenticated query against the control plane.

Repointing PROD provider URLs through a MITM proxy is an outward-facing,
hard-to-reverse change that would route real customer traffic/secrets through a
new sink. This harness is LOCAL-ONLY by default and refuses to touch prod.

Usage:
    # offline: print the required wiring + the exact live-check commands
    python verify_wiring.py

    # live check against a LOCAL control plane (needs a superuser token):
    CONTROL_URL=http://127.0.0.1:8100 CONTROL_TOKEN=<jwt> python verify_wiring.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "capture_config.json").read_text())

LIVE_ENDPOINTS = {
    "embedding-egress": "/api/vector-providers/",
    "pinecone": "/api/vector-providers/",
    "llm-inference": "/api/firewall/models/",
    "mcp": "/api/mcp-connector/servers/",
}


def print_required():
    print("Required wiring (set each provider base-URL to the proxy):\n")
    for name, ch in CONFIG["channels"].items():
        print(f"  [{name}]")
        print(f"    knob   : {ch['config_knob']}")
        print(f"    set_to : {ch['set_to']}")
        print(f"    tests  : {ch['tests']}")
        print(f"    verify : GET {{CONTROL_URL}}{LIVE_ENDPOINTS[name]}  (compare api_base/url vs set_to)\n")


def live_check(control_url: str, token: str) -> int:
    import httpx  # local import so the offline path needs no deps

    headers = {"Authorization": f"Bearer {token}"}
    if not control_url.startswith(("http://127.", "http://localhost", "http://control")):
        print(f"REFUSING live check against non-local control plane: {control_url}", file=sys.stderr)
        print("Prod repointing must be a deliberate, separately-authorized step.", file=sys.stderr)
        return 2
    proxy_base = CONFIG["proxy_base_url"]
    mismatches = 0
    with httpx.Client(timeout=15) as client:
        for name, ch in CONFIG["channels"].items():
            ep = LIVE_ENDPOINTS[name]
            try:
                r = client.get(control_url.rstrip("/") + ep, headers=headers)
                rows = r.json() if r.status_code == 200 else []
            except Exception as exc:
                print(f"  [{name}] LIVE QUERY FAILED: {exc}")
                mismatches += 1
                continue
            blob = json.dumps(rows)
            pointed = proxy_base in blob
            status = "POINTS AT PROXY" if pointed else "NOT pointed at proxy"
            print(f"  [{name}] {ep} -> {len(rows) if isinstance(rows, list) else '?'} rows :: {status}")
            if not pointed:
                mismatches += 1
    return 1 if mismatches else 0


def main() -> int:
    print_required()
    control_url = os.environ.get("CONTROL_URL")
    token = os.environ.get("CONTROL_TOKEN")
    if control_url and token:
        print("Live check:\n")
        return live_check(control_url, token)
    print("No CONTROL_URL/CONTROL_TOKEN set — offline mode (printed required wiring only).")
    print("The base-URLs are DB-stored; run the GETs above with a superuser token to confirm.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
