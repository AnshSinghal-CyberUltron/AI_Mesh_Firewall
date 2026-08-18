#!/usr/bin/env python3
"""Three unique-prompt chat calls against a live gateway. Saves traces (no secrets)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

GATEWAY = os.environ.get("GATEWAY", "https://aimeshgateway.zeroshield.ai").rstrip("/")
API_KEY = os.environ.get("API_KEY", "")
MODEL = os.environ.get("MODEL", "auto")
OUT = Path(os.environ.get(
    "OUT",
    "mcp-parallel/findings/bedrock-hotpath-2026-08-14/unique_prompt_probe.json",
))
N = int(os.environ.get("N", "3"))
TIMEOUT_S = float(os.environ.get("TIMEOUT_S", "180"))
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "16"))


def _strip_text(obj):
    """Drop long prompt/output strings from saved evidence."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("content", "input_text", "output_text", "prompt_submitted",
                     "prompt_in", "prompt_out", "final_response", "choices"):
                if isinstance(v, str):
                    out[k] = f"<redacted len={len(v)}>"
                elif isinstance(v, list):
                    out[k] = f"<redacted list n={len(v)}>"
                else:
                    out[k] = _strip_text(v)
            else:
                out[k] = _strip_text(v)
        return out
    if isinstance(obj, list):
        return [_strip_text(x) for x in obj[:20]]
    return obj


def main() -> int:
    if not API_KEY:
        raise SystemExit("API_KEY required")
    recs = []
    with httpx.Client(timeout=TIMEOUT_S, http2=False) as client:
        for i in range(N):
            nonce = format(time.time_ns(), "x")
            t0 = time.perf_counter()
            r = client.post(
                f"{GATEWAY}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": MODEL,
                    "messages": [{
                        "role": "user",
                        "content": (
                            f"What is the capital of France? Reply with the city name only. Marker TOK-{nonce}."
                        ),
                    }],
                    "max_tokens": MAX_TOKENS,
                    "stream": False,
                },
            )
            wall_ms = (time.perf_counter() - t0) * 1000
            try:
                body = r.json()
            except Exception:
                body = {"_raw": (r.text or "")[:400]}
            zs = body.get("zeroshield") if isinstance(body, dict) else None
            trace = None
            if isinstance(body, dict):
                trace = body.get("pipeline_trace")
                if trace is None and isinstance(zs, dict):
                    trace = zs.get("pipeline_trace")
            stages = []
            if isinstance(trace, dict):
                for s in trace.get("stages") or []:
                    if not isinstance(s, dict):
                        continue
                    stages.append({
                        "stage": s.get("stage") or s.get("name"),
                        "action": s.get("action"),
                        "latency_ms": s.get("latency_ms"),
                    })
            err = None
            if not (200 <= r.status_code < 300) and isinstance(body, dict):
                err = body.get("error") or body.get("code")
            recs.append({
                "i": i,
                "nonce_suffix": nonce[-8:],
                "status": r.status_code,
                "wall_ms": round(wall_ms, 2),
                "error": _strip_text(err) if err else None,
                "scan_only": bool(isinstance(zs, dict) and zs.get("scan_only")),
                "total_latency_ms": (trace or {}).get("total_latency_ms") if isinstance(trace, dict) else None,
                "stages": stages,
                "metrics": (trace or {}).get("metrics") if isinstance(trace, dict) else None,
                "model": (body.get("model") if isinstance(body, dict) else None),
                "id": (body.get("id") if isinstance(body, dict) else None),
                "pipeline_trace": _strip_text(trace) if isinstance(trace, dict) else None,
            })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "gateway": GATEWAY,
        "model": MODEL,
        "n": N,
        "unique_prompt": True,
        "records": recs,
    }
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    oks = [x for x in recs if 200 <= x["status"] < 300]
    return 0 if oks else 1


if __name__ == "__main__":
    raise SystemExit(main())
