#!/usr/bin/env python3
"""Agent 5 (E2E/regression/coverage) — FULL TOOL COVERAGE live red-team sweep.

Extends the prior run_live.py harness (which sampled 2-3 tools/server) to invoke
EVERY tool returned by tools/list for every server that responds, with 3 payload
variants each (benign / PII / prompt-injection). Pure black-box HTTP client
against the live gateway — no backend/control-plane/docker access.
"""
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

TIMEOUT = 45.0
RPC_ID = 1
ROOT = Path(__file__).resolve().parent.parent


def mcp_rpc(client: httpx.Client, key: str, slug: str, method: str, params: dict, label: str) -> dict:
    url = MCP_URL_TEMPLATE.format(org=DEFAULT_ORG_SLUG, server_slug=slug)
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    body = {"jsonrpc": "2.0", "id": RPC_ID, "method": method, "params": params}
    req = {"method": "POST", "url": url, "headers": redact_headers(headers), "body": body}
    t0 = time.time()
    try:
        r = client.post(url, headers=headers, json=body, timeout=TIMEOUT)
        try:
            rb = r.json()
        except Exception:
            rb = {"_raw": r.text[:4000]}
        resp = {"status_code": r.status_code, "headers": dict(r.headers), "body": rb}
    except Exception as e:
        resp = {"status_code": None, "error": f"{type(e).__name__}: {e}"}
    resp["_latency_ms"] = round((time.time() - t0) * 1000, 1)
    ev = Evidence(slug, label, req, resp)
    save_evidence(ev)
    return resp


# ---------------------------------------------------------------------------
# Generic argument builder — covers ALL tools across all connected servers,
# not just the 2-3 previously sampled. Falls back to name/description-based
# heuristics when inputSchema.properties is empty (observed on filesystem-
# canary tools, whose upstream schema is stripped to just $schema).
# ---------------------------------------------------------------------------
def build_args_for_tool(tool: dict, slug: str, payload: str, variant: str) -> dict:
    name = (tool.get("name") or "").lower()
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties") or {}
    required = schema.get("required") or list(props.keys())

    # --- name-based heuristics (also needed when schema has no properties) ---
    if name == "echo":
        return {"message": payload}
    if name in ("add", "get-sum", "get_sum"):
        return {"a": 2, "b": 3}
    if name == "get-annotated-message":
        return {"messageType": "success", "includeImage": False}
    if name == "get-env":
        return {}
    if name == "get-resource-links":
        return {"count": 2}
    if name == "get-resource-reference":
        return {"resourceType": "text", "resourceId": payload[:40]}
    if name == "get-structured-content":
        return {"location": payload[:40]}
    if name == "get-tiny-image":
        return {}
    if name == "gzip-file-as-resource":
        return {"name": "rt-probe.txt", "data": payload[:200], "outputType": "resource"}
    if name in ("toggle-simulated-logging", "toggle-subscriber-updates"):
        return {}
    if name == "trigger-long-running-operation":
        return {"duration": 1, "steps": 1}
    if name == "simulate-research-query":
        return {"topic": payload[:60], "ambiguous": False}
    if name in ("list_allowed_directories", "list-allowed-directories"):
        return {}
    if name == "read_file":
        return {"path": "/tmp/rt-matrix-probe.txt"}
    if name == "read_multiple_files":
        return {"paths": ["/tmp/rt-matrix-probe.txt", "/tmp/rt-matrix-probe2.txt"]}
    if name == "write_file":
        return {"path": "/tmp/rt-matrix-write-probe.txt", "content": payload[:500]}
    if name == "edit_file":
        return {"path": "/tmp/rt-matrix-probe.txt", "edits": [{"oldText": "x", "newText": payload[:100]}]}
    if name == "create_directory":
        return {"path": "/tmp/rt-matrix-dir-probe"}
    if name == "list_directory":
        return {"path": "/tmp"}
    if name == "directory_tree":
        return {"path": "/tmp"}
    if name == "move_file":
        return {"source": "/tmp/rt-matrix-probe.txt", "destination": "/tmp/rt-matrix-probe-moved.txt"}
    if name == "search_files":
        return {"path": "/tmp", "pattern": "rt-matrix"}
    if name == "get_file_info":
        return {"path": "/tmp/rt-matrix-probe.txt"}
    if name == "scan_directory":
        return {"path": "/tmp", "config": "auto"}
    if name == "list_rules":
        return {"language": "python"}
    if name == "analyze_results":
        return {"results_file": f"/tmp/{payload[:20]}.json"}
    if name == "create_rule":
        return {
            "output_path": "/tmp/rt-matrix-rule.yaml",
            "pattern": payload[:60],
            "language": "python",
            "message": payload[:60],
            "severity": "INFO",
            "id": "rt-matrix-rule",
        }
    if name == "filter_results":
        return {"results_file": "/tmp/results.json", "severity": "INFO"}
    if name == "export_results":
        return {"results_file": "/tmp/results.json", "output_file": "/tmp/out.json", "format": "json"}
    if name == "compare_results":
        return {"old_results": "/tmp/old.json", "new_results": "/tmp/new.json"}
    if name.startswith("browser_"):
        # Playwright — deterministic, harmless per-tool argument shapes.
        pw = {
            "browser_close": {},
            "browser_resize": {"width": 1280, "height": 800},
            "browser_console_messages": {"level": "log"},
            "browser_handle_dialog": {"accept": True, "promptText": payload[:60]},
            "browser_evaluate": {"function": f"() => '{payload[:40]}'"},
            "browser_file_upload": {"paths": []},
            "browser_drop": {"target": "body", "data": payload[:40]},
            "browser_fill_form": {"fields": [{"name": "q", "type": "textbox", "ref": "e1", "value": payload[:60]}]},
            "browser_press_key": {"key": "Escape"},
            "browser_type": {"target": "search box", "text": payload[:60]},
            "browser_navigate": {"url": "about:blank"},
            "browser_navigate_back": {},
            "browser_network_requests": {"static": True},
            "browser_network_request": {"index": 0},
            "browser_run_code_unsafe": {"code": f"return '{payload[:30]}'"},
            "browser_take_screenshot": {"type": "png", "scale": "css"},
            "browser_snapshot": {},
            "browser_click": {"target": "body"},
            "browser_drag": {"startTarget": "body", "endTarget": "body"},
            "browser_hover": {"target": "body"},
            "browser_select_option": {"target": "select", "values": [payload[:20]]},
            "browser_tabs": {"action": "list"},
            "browser_wait_for": {"time": 1},
        }
        return pw.get(name, {})

    # --- generic schema-driven fallback for anything not enumerated above ---
    if required:
        out = {}
        for k in required:
            t = (props.get(k) or {}).get("type")
            if t == "object":
                out[k] = {"value": payload}
            elif t == "array":
                out[k] = [payload]
            elif t in ("integer", "number"):
                out[k] = 1
            elif t == "boolean":
                out[k] = False
            else:
                out[k] = payload
        return out
    if props:
        k = next(iter(props))
        return {k: payload}
    return {}


def analyze_enforcement(resp: dict, canary: str) -> dict:
    blob = response_text_blob(resp)
    body = resp.get("body") or {}
    inner = body.get("body") if isinstance(body, dict) else body
    result = None
    err = None
    if isinstance(inner, dict):
        err = inner.get("error")
        result = inner.get("result")
    pii_hits = contains_raw_pii(blob)
    is_error_result = False
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            for c in content:
                txt = (c or {}).get("text", "") if isinstance(c, dict) else ""
                if result.get("isError") or "[BLOCKED]" in txt or "[RESOURCE_LIMIT]" in txt:
                    is_error_result = True
    blocked = (
        resp.get("status_code") in (403, 429)
        or bool(err)
        or is_error_result
    )
    redacted_markers = any(
        m in blob
        for m in (
            "***", "[REDACTED]", "[PII_REDACTED]", "[BLOCKED]",
            "AKIA****", "b***@", "[INTERNAL_IPV4_REDACTED]", "[RESOURCE_LIMIT]",
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
        "latency_ms": resp.get("_latency_ms"),
    }


def run_cross_org(key: str, client: httpx.Client) -> dict:
    url = MCP_URL_TEMPLATE.format(org="agent5-cross-org-probe", server_slug="everything-mcp")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    req = {"url": url, "headers": redact_headers(headers), "body": body}
    try:
        r = client.post(url, headers=headers, json=body, timeout=TIMEOUT)
        try:
            rb = r.json()
        except Exception:
            rb = {"_raw": r.text[:2000]}
        resp = {"status_code": r.status_code, "body": rb}
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
    coverage_rows: list[dict] = []
    t_start = time.time()

    with httpx.Client() as client:
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
            all_blobs[f"{slug}:list"] = response_text_blob(list_resp)

            if list_resp.get("status_code") != 200:
                row["status"] = "BLOCKED_LIST"
                row["reason"] = f"tools/list HTTP {list_resp.get('status_code')}: {list_resp.get('error') or list_resp.get('body')}"
                per_server.append(row)
                coverage_rows.append({
                    "mcp_server": slug, "tool": "*", "input_type": "*", "expected_action": "n/a",
                    "result": "SKIP", "http_status": list_resp.get("status_code"),
                    "reason": row["reason"], "evidence": f"agent5/evidence/{slug}/tools_list.json",
                })
                continue

            body = (list_resp.get("body") or {})
            if body.get("error"):
                row["status"] = "ERROR_LIST"
                row["reason"] = json.dumps(body.get("error"))
                per_server.append(row)
                coverage_rows.append({
                    "mcp_server": slug, "tool": "*", "input_type": "*", "expected_action": "n/a",
                    "result": "SKIP", "http_status": 200,
                    "reason": row["reason"], "evidence": f"agent5/evidence/{slug}/tools_list.json",
                })
                continue

            tools = (body.get("result") or {}).get("tools") or []
            row["tool_count"] = len(tools)
            if not tools:
                row["status"] = "NO_TOOLS"
                per_server.append(row)
                continue

            # FULL COVERAGE: every tool, not a 2-3 sample.
            for tool in tools:
                tname = tool.get("name", "")
                for variant, payload, expected in [
                    ("benign", f"benign {canary}", "ALLOW (route to upstream)"),
                    ("pii", f"SSN={PII_SAMPLES['ssn']} email={PII_SAMPLES['email']} key={PII_SAMPLES['aws_key']} {canary}", "BLOCK (compliance tags)"),
                    ("injection", f"{INJECTION_SAMPLE} {canary}", "ALLOW-ROUTE or BLOCK, but NO raw PII/secret egress + no cross-canary leak"),
                ]:
                    args = build_args_for_tool(tool, slug, payload, variant)
                    label = f"tools_call_{tname}_{variant}"
                    resp = mcp_rpc(client, key, slug, "tools/call", {"name": tname, "arguments": args}, label)
                    analysis = analyze_enforcement(resp, canary)
                    row["tool_calls"].append({"tool": tname, "variant": variant, "analysis": analysis})
                    all_blobs[f"{slug}:{tname}:{variant}"] = response_text_blob(resp)

                    if variant == "pii":
                        passed = analysis["blocked"] and not analysis["raw_pii_in_response"]
                    else:
                        passed = not analysis["raw_pii_in_response"] and analysis["http_status"] in (200, 400, 403, 429)
                    coverage_rows.append({
                        "mcp_server": slug,
                        "tool": tname,
                        "input_type": variant,
                        "expected_action": expected,
                        "result": "PASS" if passed else "FAIL",
                        "http_status": analysis["http_status"],
                        "blocked": analysis["blocked"],
                        "raw_pii_leak": bool(analysis["raw_pii_in_response"]),
                        "canary_leak_self": analysis["canary_present"],
                        "evidence": f"agent5/evidence/{slug}/{label}.json",
                    })

            row["status"] = "TESTED_FULL_COVERAGE"
            per_server.append(row)
            print(f"[+] {slug}: {len(tools)} tools x3 variants = {len(tools)*3} calls done ({time.time()-t_start:.0f}s elapsed)", flush=True)

    leaks = []
    for key_name, blob in all_blobs.items():
        owner = key_name.split(":", 1)[0]
        for slug, canary in all_canaries.items():
            if slug != owner and contains_canary(blob, canary):
                leaks.append({"found_in": key_name, "canary_owner": slug, "canary": canary})

    summary = {
        "target": GATEWAY_BASE_URL,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "api_key_redacted": redact_key(key),
        "servers_tested_full_coverage": len([r for r in per_server if r["status"] == "TESTED_FULL_COVERAGE"]),
        "servers_total": len(SERVERS),
        "total_tool_calls": sum(len(r.get("tool_calls", [])) for r in per_server),
        "per_server": per_server,
        "cross_org": cross_org,
        "cross_mcp_leaks": leaks,
        "elapsed_seconds": round(time.time() - t_start, 1),
    }
    out = ROOT / "evidence" / "_full_coverage_summary.json"
    out.write_text(json.dumps(summary, indent=2, default=str))

    cov_out = ROOT / "evidence" / "_coverage_rows.json"
    cov_out.write_text(json.dumps(coverage_rows, indent=2, default=str))

    print(json.dumps({
        "summary": str(out),
        "coverage_rows_file": str(cov_out),
        "servers_full_coverage": summary["servers_tested_full_coverage"],
        "total_tool_calls": summary["total_tool_calls"],
        "cross_mcp_leaks": len(leaks),
        "elapsed_seconds": summary["elapsed_seconds"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
