#!/usr/bin/env python3
"""Finish MCP seed + report Module 2 dual-metric counters after RAG already fired."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("BASE_URL", "http://control:8000").rstrip("/")
EMAIL = os.environ.get("TEST_EMAIL", "admin@zeroshield.io")
PASSWORD = os.environ.get("TEST_PASSWORD", "Adm1n!Pass#2024")


def req(method, url, *, headers=None, body=None):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(request, timeout=180) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"raw": raw[:400]}
        return exc.code, parsed


def main() -> int:
    st, tok = req("POST", f"{BASE}/api/auth/token/", body={"email": EMAIL, "password": PASSWORD})
    print("login", st)
    auth = {"Authorization": f"Bearer {tok['access']}"}

    st, health = req("GET", f"{BASE}/api/module2/rag/health/?period=24h", headers=auth)
    stages = (
        ((health.get("module1_aligned") or {}).get("stages"))
        or ((health.get("rag_pipeline_kpis") or {}).get("stages"))
        or {}
    )
    print("RAG query", (stages.get("query") or {}).get("total"), "blocked", (stages.get("query") or {}).get("blocked"))
    print(
        "RAG retriever",
        (stages.get("retriever") or {}).get("total"),
        "blocked",
        (stages.get("retriever") or {}).get("blocked"),
    )
    print("RAG extras", (health.get("module2_extra") or {}).get("rag_query_in_query_stage"))
    print(
        "RAG denials",
        (
            (health.get("module2_extra") or {}).get("pre_pipeline_denials")
            or health.get("rag_pre_pipeline_denials")
            or {}
        ).get("total"),
    )

    # Prefer mcp-stub over broken stdio everything.
    st, servers = req("GET", f"{BASE}/api/mcp-connector/servers/", headers=auth)
    items = servers if isinstance(servers, list) else (servers.get("results") or [])
    stub = next((s for s in items if (s.get("server_slug") or "").startswith("mcp-stub")), None)
    if not stub:
        for url in ("http://mcp-stub:9999/mcp", "http://mcp-stub:9999/", "http://host.docker.internal:9999/mcp"):
            body = {
                "name": "mcp-stub-seed",
                "transport": "streamable-http",
                "url": url,
                "description": "Mod2 seed stub",
            }
            st, created = req("POST", f"{BASE}/api/mcp-connector/servers/", headers=auth, body=body)
            print("stub_reg", url, st, str(created)[:180])
            if st in (200, 201) or created.get("existing_server"):
                stub = created.get("existing_server") or created
                break
    if not stub:
        print("NO_STUB")
        return 2

    sid = stub.get("id")
    slug = stub.get("server_slug") or "mcp-stub-seed"
    tlist = []
    for i in range(6):
        req("POST", f"{BASE}/api/mcp-connector/servers/{sid}/tools/", headers=auth, body={})
        st, tools = req("GET", f"{BASE}/api/mcp-connector/servers/{sid}/tools/", headers=auth)
        tlist = tools if isinstance(tools, list) else (tools.get("results") or tools.get("tools") or [])
        err = stub.get("last_sync_error")
        st2, servers2 = req("GET", f"{BASE}/api/mcp-connector/servers/", headers=auth)
        for s in servers2 if isinstance(servers2, list) else []:
            if s.get("id") == sid:
                err = s.get("last_sync_error")
                print(
                    "server_state",
                    s.get("connection_status"),
                    "tools_count",
                    s.get("tools_count"),
                    "err",
                    (err or "")[:120],
                )
        print("stub_sync", i + 1, "tools", len(tlist) if isinstance(tlist, list) else tlist)
        if isinstance(tlist, list) and tlist:
            break
        time.sleep(3)

    if not tlist:
        # Last resort: write MCPEvent + EF rows via Django so UI has MCP dual metrics.
        print("FALLBACK_ORM_MCP")
        import django

        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "main_app.settings")
        django.setup()
        from django.utils import timezone
        from auth.models import Organization
        from mcp_connector.models import MCPEvent
        from policy.models import EnforcementEvent

        org = Organization.objects.filter(slug="zeroshield").first() or Organization.objects.first()
        now = timezone.now()
        rid = f"seed-mcp-{int(now.timestamp())}"
        MCPEvent.objects.create(
            organization=org,
            server_slug="seed-mcp",
            tool_name="echo",
            decision="redact",
            request_id=rid + "-extra",
            metadata={"seed": "mod2-dual"},
        )
        EnforcementEvent.objects.create(
            organization=org,
            action="block",
            metadata={
                "source": "mcp_scan",
                "event_type": "mcp_tool_call",
                "request_id": rid,
                "tools_invoked": ["echo"],
                "server_slug": "seed-mcp",
                "mcp_direction": "inbound",
            },
            created_at=now,
        )
        print("orm_seeded", rid)
    else:
        tool_name = None
        for tool in tlist:
            name = tool.get("name") or tool.get("tool_name")
            if name in ("echo", "add", "get-sum", "sum"):
                tool_name = name
                break
        if not tool_name:
            tool_name = tlist[0].get("name") or tlist[0].get("tool_name")
        args = {"message": "module2-dual-metrics-canary"} if tool_name == "echo" else {"a": 1, "b": 2}
        if tool_name not in ("echo", "add", "get-sum", "sum"):
            args = {}
        st, live = req(
            "POST",
            f"{BASE}/api/mcp-connector/tools/call/",
            headers=auth,
            body={"name": tool_name, "server_slug": slug, "arguments": args},
        )
        print("mcp_live", st, str(live)[:200])

    time.sleep(5)
    st, mcp = req("GET", f"{BASE}/api/module2/mcp/risk/?period=24h", headers=auth)
    print(
        "MCP aligned",
        ((mcp.get("module1_aligned") or {}).get("summary") or mcp.get("summary") or {}).get("total_events"),
    )
    print("MCP extra", ((mcp.get("module2_extra") or {}).get("summary") or {}).get("total_events"))
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
