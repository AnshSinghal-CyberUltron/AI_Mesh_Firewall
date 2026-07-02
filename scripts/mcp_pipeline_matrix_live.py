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

cases-json: a JSON list of {"name","tool","args","expect"[,"pii"]} where expect
is one of: block | allow | error | pass_pii (allow AND raw PII present) | redacted
(allow AND PII masked). ``pii`` (optional) is the explicit list of sensitive
values the case sends; when omitted it is inferred from ``args`` (see
``case_sensitive_values``).

BACKSTOP CHG-0029 (G5 item 20 / harness oracle): the previous oracle was narrow
and leak-blind — ``pii = _SSN in text`` checked only ONE hardcoded SSN in ONLY
``result.content[0].text``, so a redact-but-forward in a later content item, in
``structuredContent``, in a nested field, or with any non-SSN sensitive value
passed as ``redacted``/``allow`` (a silent leak — the same class of hole CHG-0012
fixed for mcp_live_matrix_harness). It now checks the FULL serialized response
bytes for the case's ACTUAL sensitive values (egress bytes are the only source of
truth). The module is also import-safe (no env/argv access at import) so the
oracle can be unit-tested (CHG-0009 pattern).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any

import httpx

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300").rstrip("/")
ORG = os.environ.get("ORG_SLUG", "zeroshield")
SERVER = os.environ.get("SERVER_SLUG", "stub-presidio-test")
REPEAT = int(os.environ.get("REPEAT", "5"))
_SSN = "123-45-6789"


def find_pii_in_body(body: Any, sensitive_values) -> list[str]:
    """Return the sensitive values that appear RAW anywhere in ``body``.

    Independent byte oracle: substring over the ENTIRE serialized response (not
    just ``content[0]``), so a leak in any content item / structuredContent /
    nested field is caught. A non-empty return = a real leak.
    """
    blob = json.dumps(body, default=str)
    return [v for v in (sensitive_values or []) if v and str(v) in blob]


def case_sensitive_values(case: dict) -> list[str]:
    """The sensitive values a case sends, to be checked against the egress.

    Explicit ``case['pii']`` wins; otherwise fall back to the canonical SSN when
    the args carry it (backward-compat with pre-CHG-0029 cases that relied on the
    hardcoded SSN)."""
    pii = case.get("pii")
    if pii:
        return [str(v) for v in pii if v]
    args_blob = json.dumps(case.get("args", {}), default=str)
    return [_SSN] if _SSN in args_blob else []


def has_redaction_marker(body: Any) -> bool:
    """True if a redaction placeholder appears anywhere in the response."""
    blob = json.dumps(body, default=str)
    return "[REDACT" in blob or "***" in blob


def classify(body: dict) -> tuple[str, str]:
    r = (body or {}).get("result") or {}
    content = r.get("content") or []
    text = content[0].get("text", "") if content else ""
    if r.get("isError"):
        return ("block" if "[BLOCKED" in text else "error"), text
    if (body or {}).get("error"):
        return "error", json.dumps(body.get("error"))
    return "allow", text


def matches(cls: str, body: dict, expect: str, sensitive) -> bool:
    """Assert the response against ``expect`` using the full-body byte oracle."""
    leaked = find_pii_in_body(body, sensitive)
    if expect == "pass_pii":
        # A deliberate pass-through case: allowed AND at least one raw value present.
        return cls == "allow" and bool(leaked)
    if expect == "redacted":
        # Byte-truth: allowed, NO raw sensitive value anywhere in the response,
        # AND a redaction marker present (so a tool that simply didn't echo the
        # value is not mistaken for a successful redaction).
        return cls == "allow" and not leaked and has_redaction_marker(body)
    return cls == expect


async def one(client: httpx.AsyncClient, url: str, key: str, case: dict, i: int) -> dict:
    body = {
        "jsonrpc": "2.0", "id": f"{case['name']}-{i}", "method": "tools/call",
        "params": {"name": case["tool"], "arguments": case["args"]},
    }
    sensitive = case_sensitive_values(case)
    t0 = time.time()
    try:
        resp = await client.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        rbody = resp.json()
        cls, _text = classify(rbody)
    except Exception as exc:  # noqa: BLE001
        return {"name": case["name"], "ok": False, "cls": "exc", "err": str(exc)[:80]}
    return {
        "name": case["name"], "cls": cls, "expect": case["expect"],
        "ok": matches(cls, rbody, case["expect"], sensitive),
        "ms": int((time.time() - t0) * 1000),
    }


async def run_matrix(url: str, key: str, cases: list[dict], repeat: int) -> bool:
    async with httpx.AsyncClient(timeout=40) as client:
        tasks = [one(client, url, key, c, i) for c in cases for i in range(repeat)]
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
    return all_ok


def main() -> int:
    key = os.environ["GATEWAY_KEY"]
    cases = json.loads(sys.argv[1])
    url = f"{GATEWAY_URL}/gateway/{ORG}/mcp/{SERVER}"
    ok = asyncio.run(run_matrix(url, key, cases, REPEAT))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
