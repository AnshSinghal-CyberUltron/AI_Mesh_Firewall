"""Agent 2 (of 5) — LIVE black-box red-team matrix runner.

Scope: Input Enforcement, Input PII, Input Redaction, Input Compliance, Input Policy.
Only talks to GATEWAY_BASE_URL (default http://127.0.0.1:8300) via Bearer key.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent2_common import (  # noqa: E402
    DEFAULT_ORG_SLUG,
    FULL_VARIANT_ORDER,
    LIGHT_VARIANT_ORDER,
    MCP_URL_TEMPLATE,
    Evidence,
    build_payload_classes,
    classify_action,
    extract_compliance_tags,
    find_raw_markers,
    new_canary,
    redact_headers,
    require_api_key,
    response_text_blob,
    save_evidence,
)

# Known slugs from the shared live environment (same day/session as prior red-team runs).
KNOWN_SERVERS = [
    "semgrep-mcp",
    "playwright-mcp",
    "playwright",
    "cp09-ens8do",
    "cp09-verify",
    "everything-1",
    "everything-2",
    "everything-3",
    "everything-4",
    "everything-5",
    "everything-mcp",
    "filesystem-canary",
    "ws-everything-stub",
    "sse-everything-stub",
    "http-everything-stub",
    "linear-manual-oauth",
]

CONCURRENCY = 4
TIMEOUT = 60.0

SUMMARY: dict = {
    "run_started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "gateway_base_url": None,
    "org_slug": DEFAULT_ORG_SLUG,
    "servers": {},
    "totals": {"tools_list": 0, "tools_call": 0, "blocked": 0, "allowed": 0, "redacted": 0, "other": 0},
    "cross_mcp_canaries": {},
    "findings": [],
}


# Some upstream MCP servers (observed: filesystem-canary) report an EMPTY inputSchema
# (`{"$schema": "..."}` with no `properties`/`required`) via tools/list even though the
# tool clearly requires arguments (confirmed via the invalid_type error on empty args).
# This is itself a black-box finding (schema/introspection gap) — documented separately.
# To still exercise the input-scan matrix on these tools we fall back to the well-known
# @modelcontextprotocol/server-filesystem argument shapes (public tool, stable names).
FALLBACK_TOOL_ARGS = {
    ("filesystem-canary", "read_file"): {"inject": "path", "fixed": {}},
    ("filesystem-canary", "read_multiple_files"): {"inject": "paths_list", "fixed": {}},
    ("filesystem-canary", "write_file"): {"inject": "content", "fixed": {"path": "/tmp/rt-agent2-canary.txt"}},
    ("filesystem-canary", "edit_file"): {
        "inject": "edit_newtext",
        "fixed": {"path": "/tmp/rt-agent2-canary.txt"},
    },
    ("filesystem-canary", "create_directory"): {"inject": "path", "fixed": {}},
    ("filesystem-canary", "list_directory"): {"inject": "path", "fixed": {}},
    ("filesystem-canary", "directory_tree"): {"inject": "path", "fixed": {}},
    ("filesystem-canary", "move_file"): {"inject": "source", "fixed": {"destination": "/tmp/rt-agent2-dst.txt"}},
    ("filesystem-canary", "search_files"): {"inject": "pattern", "fixed": {"path": "/tmp"}},
    ("filesystem-canary", "get_file_info"): {"inject": "path", "fixed": {}},
    ("filesystem-canary", "list_allowed_directories"): None,
}


def build_fallback_args(server_slug: str, tool_name: str, payload_text: str):
    spec = FALLBACK_TOOL_ARGS.get((server_slug, tool_name))
    if spec is None:
        return None
    inject = spec["inject"]
    args = dict(spec["fixed"])
    if inject == "path":
        args["path"] = payload_text
    elif inject == "content":
        args["content"] = payload_text
    elif inject == "paths_list":
        args["paths"] = [payload_text]
    elif inject == "edit_newtext":
        args["edits"] = [{"oldText": "placeholder", "newText": payload_text}]
    elif inject == "source":
        args["source"] = payload_text
    elif inject == "pattern":
        args["pattern"] = payload_text
    return args, inject


def sample_field_value(name: str, schema: dict):
    t = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if t == "string":
        return None  # signal: this is our injection point
    if t == "number" or t == "integer":
        return schema.get("minimum", 1)
    if t == "boolean":
        return False
    if t == "array":
        return []
    if t == "object":
        return {}
    return "x"


def build_args_for_tool(
    tool: dict, payload_text: str, server_slug: str | None = None
) -> tuple[dict, str | None]:
    """Return (arguments_dict, injected_field_name_or_None)."""
    if server_slug is not None:
        fb = build_fallback_args(server_slug, tool.get("name", ""), payload_text)
        if fb is not None:
            return fb
        if (server_slug, tool.get("name", "")) in FALLBACK_TOOL_ARGS:
            # explicit "no args" tool (e.g. list_allowed_directories)
            return {}, None
    schema = tool.get("inputSchema", {}) or {}
    props = schema.get("properties", {}) or {}
    required = schema.get("required", []) or []
    args: dict = {}
    injected_field = None
    # prefer required string fields, else any string field
    ordered_fields = list(required) + [k for k in props.keys() if k not in required]
    for field_name in ordered_fields:
        fschema = props.get(field_name, {})
        if fschema.get("type") == "string" and injected_field is None and "enum" not in fschema:
            args[field_name] = payload_text
            injected_field = field_name
        else:
            val = sample_field_value(field_name, fschema)
            if val is None:
                val = "placeholder"
            args[field_name] = val
    # ensure all required fields present even if not iterated (shouldn't happen)
    for r in required:
        if r not in args:
            args[r] = "placeholder"
    return args, injected_field


async def jsonrpc_call(client: httpx.AsyncClient, url: str, headers: dict, method: str, params: dict, req_id: int):
    body = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    t0 = time.time()
    try:
        resp = await client.post(url, headers=headers, json=body, timeout=TIMEOUT)
        elapsed = time.time() - t0
        try:
            resp_json = resp.json()
        except Exception:
            resp_json = {"_non_json_body": resp.text[:2000]}
        return resp.status_code, resp_json, elapsed, body
    except Exception as exc:  # noqa: BLE001
        elapsed = time.time() - t0
        return -1, {"_transport_error": str(exc)}, elapsed, body


async def discover_tools(client, url, headers, server_slug) -> tuple[list, dict]:
    status, resp, elapsed, req_body = await jsonrpc_call(client, url, headers, "tools/list", {}, 1)
    ev = Evidence(
        server_slug=server_slug,
        label="00_tools_list",
        request={"url": url, "headers": redact_headers(headers), "body": req_body},
        response={"http_status": status, "elapsed_s": round(elapsed, 3), "body": resp},
        notes="Discovery call",
    )
    save_evidence(ev)
    SUMMARY["totals"]["tools_list"] += 1
    tools = []
    if status == 200 and isinstance(resp, dict):
        result = resp.get("result")
        if isinstance(result, dict) and isinstance(result.get("tools"), list):
            tools = result["tools"]
    return tools, {"status": status, "error": resp.get("error") if isinstance(resp, dict) else None}


async def call_variant(
    client, url, headers, server_slug, tool_name, variant_name, args, canary, req_id, label
):
    status, resp, elapsed, req_body = await jsonrpc_call(
        client, url, headers, "tools/call", {"name": tool_name, "arguments": args}, req_id
    )
    blob = response_text_blob(resp)
    markers = find_raw_markers(blob)
    tags = extract_compliance_tags(blob)
    action = classify_action(blob, status)
    # refine: successful allow but response contains raw PII/secret markers => leak
    leak = status == 200 and action == "ALLOW" and bool(markers)
    canary_present = canary in blob
    ev = Evidence(
        server_slug=server_slug,
        label=label,
        request={"url": url, "headers": redact_headers(headers), "body": req_body},
        response={
            "http_status": status,
            "elapsed_s": round(elapsed, 3),
            "body": resp,
            "classified_action": action,
            "compliance_tags_found": tags,
            "raw_markers_in_response": markers,
            "canary_present_in_response": canary_present,
            "possible_leak": leak,
        },
        notes=f"tool={tool_name} variant={variant_name}",
    )
    path = save_evidence(ev)
    SUMMARY["totals"]["tools_call"] += 1
    if action == "BLOCK":
        SUMMARY["totals"]["blocked"] += 1
    elif action == "REDACT":
        SUMMARY["totals"]["redacted"] += 1
    elif action == "ALLOW":
        SUMMARY["totals"]["allowed"] += 1
    else:
        SUMMARY["totals"]["other"] += 1
    if leak:
        SUMMARY["findings"].append(
            {
                "severity": "HIGH",
                "server": server_slug,
                "tool": tool_name,
                "variant": variant_name,
                "issue": "raw PII/secret marker present in ALLOW response",
                "markers": markers,
                "evidence": str(path.relative_to(path.parents[3])),
            }
        )
    return {
        "server": server_slug,
        "tool": tool_name,
        "variant": variant_name,
        "http_status": status,
        "action": action,
        "tags": tags,
        "markers": markers,
        "canary_present": canary_present,
        "evidence": str(path.name),
    }


async def run_server(client, org, api_key, server_slug, full_matrix_tool: str | None, all_tools_light: bool):
    url = MCP_URL_TEMPLATE.format(org=org, server_slug=server_slug)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    tools, disc_meta = await discover_tools(client, url, headers, server_slug)
    server_result = {
        "url": url,
        "discovery": disc_meta,
        "tool_count": len(tools),
        "tool_names": [t.get("name") for t in tools],
        "calls": [],
    }
    if not tools:
        SUMMARY["servers"][server_slug] = server_result
        return

    canary = new_canary(server_slug)
    SUMMARY["cross_mcp_canaries"][server_slug] = canary
    req_id = 10
    sem = asyncio.Semaphore(CONCURRENCY)

    async def bounded_call(*a, **kw):
        async with sem:
            return await call_variant(*a, **kw)

    tasks = []
    for tool in tools:
        tool_name = tool.get("name", "unknown")
        is_full_target = full_matrix_tool is not None and tool_name == full_matrix_tool
        variants = FULL_VARIANT_ORDER if is_full_target else (LIGHT_VARIANT_ORDER if all_tools_light else ["clean"])
        payload_classes = build_payload_classes(canary)
        for variant_name in variants:
            payload_text = payload_classes[variant_name]
            args, injected_field = build_args_for_tool(tool, payload_text, server_slug)
            if injected_field is None and variant_name != "clean":
                # tool has no string field to inject PII into; skip non-clean variants
                continue
            req_id += 1
            label = f"{tool_name}__{variant_name}"
            tasks.append(
                bounded_call(
                    client, url, headers, server_slug, tool_name, variant_name, args, canary, req_id, label
                )
            )
    results = await asyncio.gather(*tasks) if tasks else []
    server_result["calls"] = results
    SUMMARY["servers"][server_slug] = server_result


async def main():
    api_key = require_api_key()
    org = DEFAULT_ORG_SLUG
    SUMMARY["gateway_base_url"] = httpx.URL(MCP_URL_TEMPLATE.format(org=org, server_slug="x")).host

    # (server_slug -> full-matrix tool name) for representative deep-dive per family
    full_matrix_targets = {
        "everything-mcp": "echo",
        "playwright-mcp": "browser_navigate",
        "semgrep-mcp": None,  # decided after discovery (first string-arg tool)
        "filesystem-canary": "write_file",
    }

    async with httpx.AsyncClient() as client:
        # Phase 1: discover tool names on semgrep-mcp to pick a full-matrix target tool
        semgrep_tools, _ = await discover_tools(
            client,
            MCP_URL_TEMPLATE.format(org=org, server_slug="semgrep-mcp"),
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            "semgrep-mcp",
        )
        SUMMARY["totals"]["tools_list"] -= 1  # avoid double count; will redo properly below in run_server
        semgrep_full_tool = None
        for t in semgrep_tools:
            props = (t.get("inputSchema", {}) or {}).get("properties", {}) or {}
            if any(v.get("type") == "string" for v in props.values()):
                semgrep_full_tool = t.get("name")
                break
        full_matrix_targets["semgrep-mcp"] = semgrep_full_tool

        import os as _os

        only = _os.environ.get("ONLY_SERVERS", "").strip()
        target_servers = [s.strip() for s in only.split(",") if s.strip()] if only else KNOWN_SERVERS

        server_jobs = []
        for slug in target_servers:
            full_tool = full_matrix_targets.get(slug)
            all_light = True  # every tool gets at least the light 4-variant pass
            server_jobs.append(run_server(client, org, api_key, slug, full_tool, all_light))

        # run servers themselves with limited concurrency to avoid overloading the shared gateway
        server_sem = asyncio.Semaphore(3)

        async def bounded_server(coro):
            async with server_sem:
                return await coro

        await asyncio.gather(*[bounded_server(job) for job in server_jobs])

    # cross-MCP canary leak scan
    leaks = []
    for slug, result in SUMMARY["servers"].items():
        for call in result.get("calls", []):
            for other_slug, other_canary in SUMMARY["cross_mcp_canaries"].items():
                if other_slug == slug:
                    continue
                # we only stored canary_present for the owning canary; do a raw text scan instead
                pass
    SUMMARY["cross_mcp_leak_scan_note"] = (
        "See evidence/_cross_check.json for a full byte-level scan of all evidence files for foreign canaries."
    )

    SUMMARY["run_finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    from agent2_common import RUN_ID as _run_id

    out_name = "_matrix_summary.json" if not only else f"_matrix_summary_partial_{_run_id}.json"
    out_path = Path(__file__).resolve().parent.parent / "evidence" / out_name
    out_path.write_text(json.dumps(SUMMARY, indent=2, default=str))
    print(f"Wrote summary to {out_path}")
    print(json.dumps(SUMMARY["totals"], indent=2))


if __name__ == "__main__":
    asyncio.run(main())
