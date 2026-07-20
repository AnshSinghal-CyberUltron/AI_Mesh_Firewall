#!/usr/bin/env python3
"""LIVE red-team: all connected MCPs via gateway HTTP + OpenAI SDK."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    DEFAULT_ORG_SLUG,
    EVIDENCE_DIR,
    GATEWAY_BASE_URL,
    INJECTION_SAMPLE,
    MCP_URL_TEMPLATE,
    OPENAI_BASE_URL,
    PII_SAMPLES,
    Evidence,
    contains_canary,
    contains_raw_pii,
    new_canary,
    redact_headers,
    redact_key,
    require_api_key,
    response_text_blob,
    save_evidence,
)

SERVERS = [
    {"slug": "semgrep-mcp", "transport": "stdio"},
    {"slug": "playwright-mcp", "transport": "stdio"},
    {"slug": "cp09-ens8do", "transport": "stdio"},
    {"slug": "cp09-verify", "transport": "stdio"},
    {"slug": "playwright", "transport": "stdio"},
    {"slug": "ws-everything-stub", "transport": "websocket"},
    {"slug": "sse-everything-stub", "transport": "sse"},
    {"slug": "http-everything-stub", "transport": "streamable-http"},
    {"slug": "linear-manual-oauth", "transport": "streamable-http"},
    {"slug": "everything-5", "transport": "stdio"},
    {"slug": "everything-4", "transport": "stdio"},
    {"slug": "everything-3", "transport": "stdio"},
    {"slug": "everything-2", "transport": "stdio"},
    {"slug": "everything-1", "transport": "stdio"},
    {"slug": "filesystem-canary", "transport": "stdio"},
    {"slug": "everything-mcp", "transport": "stdio"},
]

TIMEOUT = 90.0
RPC_ID = 1

ROOT = Path(__file__).resolve().parent.parent


def mcp_rpc(client: httpx.Client, key: str, slug: str, method: str, params: dict, label: str) -> dict:
    url = MCP_URL_TEMPLATE.format(org=DEFAULT_ORG_SLUG, server_slug=slug)
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    body = {"jsonrpc": "2.0", "id": RPC_ID, "method": method, "params": params}
    req = {"method": "POST", "url": url, "headers": redact_headers(headers), "body": body}
    try:
        r = client.post(url, headers=headers, json=body, timeout=TIMEOUT)
        try:
            rb = r.json()
        except Exception:
            rb = {"_raw": r.text[:8000]}
        resp = {"status_code": r.status_code, "headers": dict(r.headers), "body": rb}
    except Exception as e:
        resp = {"status_code": None, "error": f"{type(e).__name__}: {e}"}
    ev = Evidence(slug, label, req, resp)
    save_evidence(ev)
    return resp


def pick_tools(tools: list[dict], slug: str) -> list[dict]:
    prefs = {
        "semgrep-mcp": ["scan", "semgrep"],
        "filesystem-canary": ["list_allowed_directories", "read", "write"],
        "linear-manual-oauth": ["list", "search", "get"],
        "playwright": ["navigate", "snapshot"],
        "playwright-mcp": ["navigate", "snapshot"],
    }
    keys = prefs.get(slug, ["echo", "add", "get-sum", "get_sum"])
    picked = [t for t in tools if any(k in t.get("name", "").lower() for k in keys)]
    return (picked or tools)[:3]


def build_args(tool: dict, payload: str) -> dict:
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    name = tool.get("name", "").lower()
    if "echo" in name:
        return {"message": payload}
    if name == "add" or "get-sum" in name or "get_sum" in name:
        return {"a": 2, "b": 3}
    if "list_allowed" in name:
        return {}
    if "read" in name and "file" in props:
        return {"path": "/tmp/rt-live-probe.txt"}
    if required:
        k = required[0]
        t = (props.get(k) or {}).get("type")
        if t == "object":
            return {k: {"value": payload}}
        if t == "array":
            return {k: [payload]}
        return {k: payload}
    if props:
        k = next(iter(props))
        return {k: payload}
    return {"input": payload}


def analyze_enforcement(resp: dict, canary: str) -> dict:
    blob = response_text_blob(resp)
    body = resp.get("body") or {}
    inner = body.get("body") if isinstance(body, dict) else body
    if isinstance(inner, dict):
        err = inner.get("error")
        if err:
            return {"decision": "error_or_block", "error": err}
        result = inner.get("result")
    else:
        result = None
    pii_hits = contains_raw_pii(blob)
    blocked = resp.get("status_code") in (403, 429) or (
        isinstance(inner, dict) and inner.get("error")
    )
    redacted_markers = any(
        m in blob
        for m in (
            "***", "[REDACTED]", "[PII_REDACTED]", "[BLOCKED]",
            "AKIA****", "b***@", "[INTERNAL_IPV4_REDACTED]",
        )
    )
    tags = []
    if isinstance(result, dict):
        tags = result.get("compliance_tags") or result.get("_compliance_tags") or []
    return {
        "http_status": resp.get("status_code"),
        "blocked": blocked,
        "raw_pii_in_response": pii_hits,
        "redaction_markers": redacted_markers,
        "canary_present": contains_canary(blob, canary),
        "compliance_tags": tags,
    }


def run_openai_sdk(key: str) -> list[dict]:
    results = []
    try:
        from openai import OpenAI
    except ImportError:
        return [{"error": "openai package not installed"}]

    client = OpenAI(api_key=key, base_url=OPENAI_BASE_URL, timeout=TIMEOUT)
    canary = new_canary("sdk-chat")

    cases = [
        ("sdk_chat_benign", "Say OK only.", {}),
        (
            "sdk_chat_pii_mcp_context",
            "Summarize the customer.",
            {
                "mcp_context": {
                    "customer_id": canary,
                    "ssn": PII_SAMPLES["ssn"],
                    "email": PII_SAMPLES["email"],
                    "aws_key": PII_SAMPLES["aws_key"],
                }
            },
        ),
        (
            "sdk_chat_injection",
            INJECTION_SAMPLE,
            {"mcp_context": {"note": canary}},
        ),
    ]

    for label, prompt, extra in cases:
        req_rec = {
            "sdk": "openai.OpenAI.chat.completions.create",
            "base_url": OPENAI_BASE_URL,
            "model": "auto",
            "messages": [{"role": "user", "content": prompt}],
            "extra_body": extra or None,
        }
        try:
            kwargs = {"model": "auto", "messages": [{"role": "user", "content": prompt}], "max_tokens": 80}
            if extra:
                kwargs["extra_body"] = extra
            r = client.chat.completions.create(**kwargs)
            resp_rec = {
                "status": "ok",
                "id": r.id,
                "content": (r.choices[0].message.content or "")[:2000] if r.choices else "",
                "model": r.model,
            }
        except Exception as e:
            body = getattr(e, "body", None)
            resp_rec = {
                "status": "error",
                "type": type(e).__name__,
                "message": str(e)[:2000],
                "status_code": getattr(e, "status_code", None),
                "body": body,
            }
        ev = Evidence("_openai_sdk", label, req_rec, resp_rec, notes=f"canary={canary}")
        save_evidence(ev)
        results.append({"label": label, "status": resp_rec.get("status"), "analysis": analyze_enforcement({"body": resp_rec}, canary)})
    return results


def run_cross_org(key: str, client: httpx.Client) -> dict:
    url = MCP_URL_TEMPLATE.format(org="other-org-probe", server_slug="everything-mcp")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    req = {"url": url, "headers": redact_headers(headers), "body": body}
    try:
        r = client.post(url, headers=headers, json=body, timeout=TIMEOUT)
        resp = {"status_code": r.status_code, "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:4000]}
    except Exception as e:
        resp = {"error": str(e)}
    ev = Evidence("_isolation", "cross_org_tools_list", req, resp)
    save_evidence(ev)
    return {"blocked": resp.get("status_code") in (401, 403), "response": resp}


def main() -> int:
    key = require_api_key()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    all_canaries: dict[str, str] = {}
    per_server: list[dict] = []
    all_blobs: dict[str, str] = {}

    with httpx.Client() as client:
        # health
        try:
            h = client.get(f"{GATEWAY_BASE_URL}/health", timeout=10)
            save_evidence(Evidence("_gateway", "health", {"url": f"{GATEWAY_BASE_URL}/health"}, {"status_code": h.status_code, "body": h.json()}))
        except Exception as e:
            save_evidence(Evidence("_gateway", "health", {}, {"error": str(e)}))

        cross_org = run_cross_org(key, client)

        for srv in SERVERS:
            slug = srv["slug"]
            canary = new_canary(slug)
            all_canaries[slug] = canary
            row = {"slug": slug, "transport": srv["transport"], "canary": canary, "tools_list": None, "tool_calls": [], "status": "UNKNOWN"}

            list_resp = mcp_rpc(client, key, slug, "tools/list", {}, "tools_list")
            row["tools_list"] = analyze_enforcement(list_resp, canary)
            blob = response_text_blob(list_resp)
            all_blobs[f"{slug}:list"] = blob

            if list_resp.get("status_code") != 200:
                row["status"] = "BLOCKED_LIST"
                row["reason"] = f"tools/list HTTP {list_resp.get('status_code')}"
                per_server.append(row)
                continue

            body = (list_resp.get("body") or {})
            if body.get("error"):
                row["status"] = "ERROR_LIST"
                row["reason"] = body.get("error")
                per_server.append(row)
                continue

            tools = (body.get("result") or {}).get("tools") or []
            row["tool_count"] = len(tools)
            if not tools:
                row["status"] = "NO_TOOLS"
                per_server.append(row)
                continue

            for tool in pick_tools(tools, slug):
                tname = tool.get("name", "")
                for variant, payload in [
                    ("benign", f"benign {canary}"),
                    ("pii", f"SSN={PII_SAMPLES['ssn']} email={PII_SAMPLES['email']} key={PII_SAMPLES['aws_key']} {canary}"),
                    ("injection", f"{INJECTION_SAMPLE} {canary}"),
                ]:
                    args = build_args(tool, payload)
                    label = f"tools_call_{tname}_{variant}"
                    resp = mcp_rpc(client, key, slug, "tools/call", {"name": tname, "arguments": args}, label)
                    analysis = analyze_enforcement(resp, canary)
                    row["tool_calls"].append({"tool": tname, "variant": variant, "analysis": analysis})
                    all_blobs[f"{slug}:{tname}:{variant}"] = response_text_blob(resp)

            row["status"] = "TESTED"
            per_server.append(row)

    leaks = []
    for key_name, blob in all_blobs.items():
        owner = key_name.split(":", 1)[0]
        for slug, canary in all_canaries.items():
            if slug != owner and contains_canary(blob, canary):
                leaks.append({"found_in": key_name, "canary_owner": slug, "canary": canary})

    sdk_results = run_openai_sdk(key)

    summary = {
        "target": GATEWAY_BASE_URL,
        "openai_base_url": OPENAI_BASE_URL,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "api_key_redacted": redact_key(key),
        "servers_tested": len([r for r in per_server if r["status"] == "TESTED"]),
        "servers_total": len(SERVERS),
        "per_server": per_server,
        "cross_org": cross_org,
        "cross_mcp_leaks": leaks,
        "openai_sdk": sdk_results,
    }
    out = ROOT / "evidence" / "_live_summary.json"
    out.write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps({"summary": str(out), "tested": summary["servers_tested"], "leaks": len(leaks)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
