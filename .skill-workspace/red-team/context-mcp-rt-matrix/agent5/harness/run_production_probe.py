#!/usr/bin/env python3
"""Agent 5 — own LIVE probe of the production gateway with the supplied key.

Per instructions: document 401 if it fails, do NOT substitute a different key
or environment. This exercises /health, /v1/models, MCP tools/list (all 16
slugs), and /v1/chat/completions against https://aimeshgateway.zeroshield.ai
using the exact GATEWAY_API_KEY provided for this assessment.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import Evidence, redact_headers, redact_key, require_api_key, save_evidence  # noqa: E402

PROD_BASE = "https://aimeshgateway.zeroshield.ai"
ORG_SLUG = "zeroshield"
TIMEOUT = 20.0

SERVER_SLUGS = [
    "semgrep-mcp", "playwright-mcp", "cp09-ens8do", "cp09-verify", "playwright",
    "ws-everything-stub", "sse-everything-stub", "http-everything-stub",
    "linear-manual-oauth", "everything-5", "everything-4", "everything-3",
    "everything-2", "everything-1", "filesystem-canary", "everything-mcp",
]


def probe(client: httpx.Client, key: str) -> dict:
    results = {"target": PROD_BASE, "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "api_key_redacted": redact_key(key)}

    # 1) health (unauthenticated)
    try:
        r = client.get(f"{PROD_BASE}/health", timeout=TIMEOUT)
        body = r.json()
    except Exception as e:
        r = None
        body = {"error": str(e)}
    save_evidence(Evidence("_production", "health", {"url": f"{PROD_BASE}/health"}, {"status_code": getattr(r, "status_code", None), "body": body}))
    results["health"] = {"status_code": getattr(r, "status_code", None), "body": body}

    # 2) /v1/models with supplied key
    headers = {"Authorization": f"Bearer {key}"}
    try:
        r = client.get(f"{PROD_BASE}/v1/models", headers=headers, timeout=TIMEOUT)
        body = r.json()
        sc = r.status_code
    except Exception as e:
        sc, body = None, {"error": str(e)}
    save_evidence(Evidence("_production", "v1_models_with_key", {"url": f"{PROD_BASE}/v1/models", "headers": redact_headers(headers)}, {"status_code": sc, "body": body}))
    results["v1_models"] = {"status_code": sc, "body": body}

    # 3) MCP tools/list for ALL 16 server slugs with supplied key
    mcp_results = []
    for slug in SERVER_SLUGS:
        url = f"{PROD_BASE}/gateway/{ORG_SLUG}/mcp/{slug}"
        body_req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        try:
            r = client.post(url, headers={**headers, "Content-Type": "application/json"}, json=body_req, timeout=TIMEOUT)
            try:
                rb = r.json()
            except Exception:
                rb = {"_raw": r.text[:1000]}
            sc = r.status_code
        except Exception as e:
            sc, rb = None, {"error": str(e)}
        save_evidence(Evidence("_production", f"mcp_tools_list_{slug}", {"url": url, "headers": redact_headers({**headers, "Content-Type": "application/json"}), "body": body_req}, {"status_code": sc, "body": rb}))
        mcp_results.append({"slug": slug, "status_code": sc, "body": rb})
    results["mcp_tools_list_all_16"] = mcp_results

    # 4) /v1/chat/completions with supplied key
    chat_body = {"model": "openrouter/free", "messages": [{"role": "user", "content": "Reply with exactly: OK"}], "max_tokens": 20}
    try:
        r = client.post(f"{PROD_BASE}/v1/chat/completions", headers={**headers, "Content-Type": "application/json"}, json=chat_body, timeout=TIMEOUT)
        try:
            rb = r.json()
        except Exception:
            rb = {"_raw": r.text[:1000]}
        sc = r.status_code
    except Exception as e:
        sc, rb = None, {"error": str(e)}
    save_evidence(Evidence("_production", "chat_completions_with_key", {"url": f"{PROD_BASE}/v1/chat/completions", "headers": redact_headers({**headers, "Content-Type": "application/json"}), "body": chat_body}, {"status_code": sc, "body": rb}))
    results["chat_completions"] = {"status_code": sc, "body": rb}

    return results


def main() -> int:
    key = require_api_key()
    with httpx.Client() as client:
        results = probe(client, key)
    ROOT = Path(__file__).resolve().parent.parent
    out = ROOT / "evidence" / "_production_probe_summary.json"
    out.write_text(json.dumps(results, indent=2, default=str))
    print(json.dumps({
        "health": results["health"]["status_code"],
        "v1_models": results["v1_models"]["status_code"],
        "mcp_all_401": all(m["status_code"] == 401 for m in results["mcp_tools_list_all_16"]),
        "chat_completions": results["chat_completions"]["status_code"],
        "summary": str(out),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
