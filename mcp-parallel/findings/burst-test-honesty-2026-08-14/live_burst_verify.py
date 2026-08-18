#!/usr/bin/env python3
"""Live proof: uncapped scan-only burst, PII no-LLM, TPM estimated_tokens 429."""
from __future__ import annotations

import asyncio
import collections
import json
import os
import time
from pathlib import Path

import httpx

CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100")
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8300")
EMAIL = os.environ.get("TEST_EMAIL", "admin@zeroshield.io")
PASSWORD = os.environ.get("TEST_PASSWORD", "Adm1n!Pass#2024")
OUT = Path(__file__).with_name("live_burst_verify.json")

PII = "My SSN is 123-45-6789 and email is bob.jones@corp.example card 4111111111111111"
CLEAN = "Rate-limit probe. This is a clean availability check with no sensitive data."


def mint_key() -> str:
    with httpx.Client(timeout=60) as c:
        tok = c.post(f"{CONTROL}/api/auth/token/", json={"email": EMAIL, "password": PASSWORD})
        tok.raise_for_status()
        jwt = tok.json().get("access") or tok.json().get("access_token")
        r = c.post(
            f"{CONTROL}/api/gateways/simulator-default/",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        r.raise_for_status()
        key = r.json().get("key")
        if not key:
            raise RuntimeError(f"no key in {r.text[:300]}")
        return key


def pick_model(key: str) -> str:
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{GATEWAY}/v1/models", headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        ids = [m.get("id") for m in (r.json().get("data") or []) if m.get("id")]
    if not ids:
        return "gpt-4o-mini"
    for want in ids:
        if "gemini" in want or "flash" in want:
            return want
    return ids[0]


async def one(client, key, model, prompt, *, max_tokens, estimated_tokens=None):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    if estimated_tokens is not None:
        body["estimated_tokens"] = estimated_tokens
    t0 = time.perf_counter()
    r = await client.post(
        f"{GATEWAY}/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json=body,
    )
    ms = round((time.perf_counter() - t0) * 1000, 1)
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:400]}
    zs = data.get("zeroshield") if isinstance(data, dict) else {}
    return {
        "status": r.status_code,
        "ms": ms,
        "scan_only_header": r.headers.get("x-zeroshield-scan-only"),
        "scan_only_top": data.get("scan_only") if isinstance(data, dict) else None,
        "zs_scan_only": (zs or {}).get("scan_only") if isinstance(zs, dict) else None,
        "zs_action": (zs or {}).get("action") if isinstance(zs, dict) else None,
        "code": data.get("code") if isinstance(data, dict) else None,
        "error": data.get("error") if isinstance(data, dict) else None,
        "message": (data.get("message") or (data.get("error") or {}).get("message")
                    if isinstance(data, dict) else None),
        "has_assistant_text": bool(
            isinstance(data, dict)
            and (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        ),
    }


async def burst(key, model, n, conc, prompt, *, max_tokens, estimated_tokens=None):
    sem = asyncio.Semaphore(conc)
    results = []

    async with httpx.AsyncClient(timeout=60) as client:
        async def run():
            async with sem:
                results.append(await one(
                    client, key, model, prompt,
                    max_tokens=max_tokens, estimated_tokens=estimated_tokens,
                ))

        t0 = time.perf_counter()
        await asyncio.gather(*[run() for _ in range(n)])
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    statuses = collections.Counter(r["status"] for r in results)
    return {
        "n": n,
        "concurrency": conc,
        "elapsed_ms": elapsed_ms,
        "statuses": dict(statuses),
        "scan_only_headers": sum(1 for r in results if str(r.get("scan_only_header")).lower() == "true"),
        "zs_scan_only": sum(1 for r in results if r.get("zs_scan_only") is True),
        "assistant_text": sum(1 for r in results if r.get("has_assistant_text")),
        "actions": dict(collections.Counter(r.get("zs_action") or r.get("code") or str(r["status"]) for r in results)),
        "errors_sample": [r for r in results if r["status"] >= 400][:5],
        "ok_sample": [r for r in results if r["status"] < 400][:2],
    }


async def main():
    report = {"ok": False, "steps": {}}
    key = mint_key()
    model = pick_model(key)
    report["model"] = model
    report["key_prefix"] = key[:10]

    # 1) Explicit scan-only (max_tokens=0) must skip the model.
    report["steps"]["scan_only_20x20"] = await burst(
        key, model, 20, 20, CLEAN, max_tokens=0,
    )
    # 2) Operator-typed 500/100 scan-only (no cap).
    report["steps"]["scan_only_500x100"] = await burst(
        key, model, 500, 100, CLEAN, max_tokens=0,
    )
    # 3) PII scan-only: redact/block, still no model.
    report["steps"]["pii_scan_only_10"] = await burst(
        key, model, 10, 10, PII, max_tokens=0,
    )
    # 4) Rate-limit probe: 20 × 8000 against ~100k TPM.
    report["steps"]["rate_limit_20x8000"] = await burst(
        key, model, 20, 20, CLEAN, max_tokens=0, estimated_tokens=8000,
    )
    # 5) Omitted max_tokens still infers (OpenAI compat) — ONE request only.
    async with httpx.AsyncClient(timeout=60) as client:
        infer = await one(client, key, model, "Say hi in one word.", max_tokens=16)
    report["steps"]["inference_max_tokens_16"] = infer

    s500 = report["steps"]["scan_only_500x100"]
    s20 = report["steps"]["scan_only_20x20"]
    pii = report["steps"]["pii_scan_only_10"]
    rl = report["steps"]["rate_limit_20x8000"]
    checks = {
        "sent_500": s500["n"] == 500,
        "concurrency_100": s500["concurrency"] == 100,
        "scan_only_500_no_assistant": s500["assistant_text"] == 0,
        "scan_only_500_header": s500["scan_only_headers"] >= 400,
        "scan_only_20_header": s20["scan_only_headers"] == 20,
        "pii_no_assistant": pii["assistant_text"] == 0,
        "pii_not_all_5xx": sum(pii["statuses"].get(k, 0) for k in pii["statuses"] if k >= 500) < 10,
        "rate_limit_has_429": rl["statuses"].get(429, 0) >= 1,
        "inference_not_scan_only": infer.get("zs_scan_only") is not True and infer["status"] in (200, 403, 422),
    }
    report["checks"] = checks
    report["ok"] = all(checks.values())
    OUT.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({"ok": report["ok"], "checks": checks, "out": str(OUT)}, indent=2))
    print(json.dumps({k: {sk: sv for sk, sv in v.items() if sk != "ok_sample"}
                      if isinstance(v, dict) else v
                      for k, v in report["steps"].items()}, indent=2, default=str)[:4000])


if __name__ == "__main__":
    asyncio.run(main())
