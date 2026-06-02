#!/usr/bin/env python3
"""Parallel live-matrix tester for the two-tier MCP guardrail pipeline.

Fires a set of tool-call cases CONCURRENTLY against the live gateway and asserts
the response action. The active MCPScanControl config is set externally (per
"epoch") before running; this script only drives + asserts the response side.
Audit-row (MCPEvent.decision / scan_trace) assertions are done separately via
the control ORM by the caller.

Usage:
  GATEWAY_KEY=... [GATEWAY_URL=...] [ORG_SLUG=...] [SERVER_SLUG=...] [REPEAT=5] \
    python scripts/mcp_pipeline_matrix_live.py '<cases-json>'

cases-json: a JSON list of {"name","tool","args","expect"} where expect is one
of: block | allow | error | pass_pii (allow AND raw PII present) | redacted
(allow AND PII masked).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import httpx

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300").rstrip("/")
KEY = os.environ["GATEWAY_KEY"]
ORG = os.environ.get("ORG_SLUG", "zeroshield")
SERVER = os.environ.get("SERVER_SLUG", "stub-presidio-test")
REPEAT = int(os.environ.get("REPEAT", "5"))
URL = f"{GATEWAY_URL}/gateway/{ORG}/mcp/{SERVER}"
CASES = json.loads(sys.argv[1])
_SSN = "123-45-6789"


def classify(body: dict) -> tuple[str, str]:
    r = (body or {}).get("result") or {}
    content = r.get("content") or []
    text = content[0].get("text", "") if content else ""
    if r.get("isError"):
        return ("block" if "[BLOCKED" in text else "error"), text
    if (body or {}).get("error"):
        return "error", json.dumps(body.get("error"))
    return "allow", text


def matches(cls: str, text: str, expect: str) -> bool:
    pii = _SSN in text
    if expect == "pass_pii":
        return cls == "allow" and pii
    if expect == "redacted":
        return cls == "allow" and not pii and ("[REDACT" in text or "***" in text)
    return cls == expect


async def one(client: httpx.AsyncClient, case: dict, i: int) -> dict:
    body = {
        "jsonrpc": "2.0", "id": f"{case['name']}-{i}", "method": "tools/call",
        "params": {"name": case["tool"], "arguments": case["args"]},
    }
    t0 = time.time()
    try:
        resp = await client.post(URL, json=body, headers={"Authorization": f"Bearer {KEY}"})
        cls, text = classify(resp.json())
    except Exception as exc:  # noqa: BLE001
        return {"name": case["name"], "ok": False, "cls": "exc", "err": str(exc)[:80]}
    return {
        "name": case["name"], "cls": cls, "expect": case["expect"],
        "ok": matches(cls, text, case["expect"]),
        "ms": int((time.time() - t0) * 1000),
    }


async def main() -> None:
    async with httpx.AsyncClient(timeout=40) as client:
        tasks = [one(client, c, i) for c in CASES for i in range(REPEAT)]
        results = await asyncio.gather(*tasks)
    agg: dict[str, dict] = {}
    for r in results:
        a = agg.setdefault(r["name"], {"pass": 0, "fail": 0, "cls": r.get("cls"), "expect": r.get("expect")})
        a["pass" if r.get("ok") else "fail"] += 1
        a["cls"] = r.get("cls")
    all_ok = True
    for name, a in agg.items():
        ok = a["fail"] == 0
        all_ok &= ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {a['pass']}/{a['pass'] + a['fail']} got={a['cls']} expect={a['expect']}")
    print("OVERALL:", "PASS" if all_ok else "FAIL")
    sys.exit(0 if all_ok else 1)


asyncio.run(main())
