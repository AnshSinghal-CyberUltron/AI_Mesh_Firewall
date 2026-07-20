"""Leak-hunt runtime-truth watchers (the 4 the user asked for as `worker run --name X`).

These are NOT ruflo workers (ruflo has only 12 built-in trigger workers); they are
realized here as runnable reconcilers over the LIVE local gateway + the §2 capture
logs. Usage mirrors the requested interface:

    python watchers.py --name egress-reconciler-fleet
    python watchers.py --name embed-vector-watcher
    python watchers.py --name mcp-context-watcher
    python watchers.py --name aidefence-allchannels

Each watches the RUNTIME (deployed) gateway — so it reports the system as it actually
runs today (the E9-E14 fixes are NOT rebuilt into the baked image yet), which is the
whole point of a runtime-truth watcher. Reads the temp key from /tmp.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

GW = "http://127.0.0.1:8300"
KEY = Path("/tmp/leakhunt_watchers_key").read_text().strip()
HERE = Path(__file__).resolve().parent
CAPTURES = HERE / "captures"
EGRESS_LOG = Path("/tmp/watcher_egress.jsonl")

# Deterministic PII/secret oracle (PRIMARY — aidefence_has_pii misses bare phones, E9).
PII = {
    "ssn": re.compile(r"\b9?\d{2}-\d{2}-\d{4}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "phone10": re.compile(r"(?<!\d)\d{10}(?!\d)"),
    "card": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}
CHAT_MODELS = ["gemma-free", "gpt4o-mini", "haiku-cheap"]
FIXTURES = [
    {"id": "phone_email", "text": "call me at 8929554991 and email evance.maps@mail.com asap",
     "raw": ["8929554991", "evance.maps@mail.com"]},
    {"id": "ssn", "text": "my ssn is 123-45-6789 please update", "raw": ["123-45-6789"]},
    {"id": "cred", "text": "deploy key sk-proj-TESTabc123DEF456 and AKIAIOSFODNN7EXAMPLE", "raw": ["sk-proj-TESTabc123DEF456", "AKIAIOSFODNN7EXAMPLE"]},
]


def _scan(text: str) -> list[str]:
    return sorted(k for k, rx in PII.items() if rx.search(text or ""))


def _post(path: str, body: dict, timeout: int = 90):
    req = urllib.request.Request(GW + path, json.dumps(body).encode(),
                                 {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.getcode(), json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as exc:
        return 0, {"_err": str(exc)}


def _prompt_out(d: dict) -> str:
    po = ""
    for st in (d.get("pipeline_trace", {}) or {}).get("stages", []):
        if st.get("name") in ("policy", "input_scan") and st.get("prompt_out"):
            po = st["prompt_out"]
    return po


# ---------------------------------------------------------------- watchers ---

def egress_reconciler_fleet():
    """verdict vs captured egress, per model: action=='redact' must imply the
    forwarded prompt is PII-clean (no phantom; no leaked value)."""
    print("=== egress-reconciler-fleet (verdict vs egress, per model) ===")
    rows = []
    with EGRESS_LOG.open("w") as log:
        for fx in FIXTURES:
            for m in CHAT_MODELS:
                code, d = _post("/v1/chat/completions",
                                {"model": m, "messages": [{"role": "user", "content": fx["text"]}],
                                 "enable_routing": False, "max_tokens": 8})
                zs = d.get("zeroshield", {}) or {}
                action = zs.get("action")
                po = _prompt_out(d)
                leaked = [t for t in fx["raw"] if t in po]
                phantom = (action == "redact") and bool(leaked)
                rows.append({"fixture": fx["id"], "model": m, "http": code, "action": action,
                             "leaked_on_egress": leaked, "phantom": phantom})
                log.write(json.dumps({"channel": "chat", "model": m, "fixture": fx["id"],
                                      "egress": po, "pii": _scan(po)}) + "\n")
                print(f'  {fx["id"]:11s} {m:12s} http={code} action={str(action):7s} '
                      f'leaked_on_egress={leaked} phantom={phantom}')
    leaks = [r for r in rows if r["leaked_on_egress"] and r["http"] == 200]
    phantoms = [r for r in rows if r["phantom"]]
    print(f"  RECONCILE: {len(rows)} probes | egress-leaks={len(leaks)} | phantom-redactions={len(phantoms)}")
    if leaks:
        print("  LEAK (raw value on forwarded egress despite serving): " +
              ", ".join(f'{r["model"]}/{r["fixture"]}:{r["leaked_on_egress"]}' for r in leaks[:6]))
    return rows


def embed_vector_watcher():
    """/v1/embeddings input scan (deployed behavior) — a credential payload chat
    BLOCKS should not be embedded raw."""
    print("=== embed-vector-watcher (/v1/embeddings + vector at-rest) ===")
    pii = "deploy key sk-proj-TESTabc123DEF456 and ssn 123-45-6789"
    ec, _ = _post("/v1/embeddings", {"model": "zs-embed", "input": pii})
    cc, _ = _post("/v1/chat/completions",
                  {"model": "gpt4o-mini", "messages": [{"role": "user", "content": pii}],
                   "enable_routing": False, "max_tokens": 8})
    verdict = "LEAK (embedded raw, chat blocks same)" if (ec == 200 and cc in (403, 400)) else \
              ("clean (scanned/blocked)" if ec in (400, 403) else f"chat={cc} embed={ec}")
    print(f"  /v1/embeddings http={ec} | same payload to chat http={cc} -> {verdict}")
    print("  NOTE: Pinecone at-rest watch needs live ingest+ChromaDB capture (E10 infra).")
    return {"embed_http": ec, "chat_http": cc, "verdict": verdict}


def mcp_context_watcher():
    """MCP both directions — requires live MCP tool traffic through the gateway
    /gateway/{org}/mcp/{slug} endpoint to reconcile args-out + results-in."""
    print("=== mcp-context-watcher (MCP both directions) ===")
    print("  STATUS: needs live MCP tool-call traffic to capture. The enforcement is")
    print("  proven in E12 (tool-arg credential block + result-redaction floor); this")
    print("  watcher reconciles real captures once MCP tool calls flow through the")
    print("  gateway MCP endpoint. No live MCP traffic captured in this run.")
    return {"status": "needs-live-mcp-traffic"}


def aidefence_allchannels():
    """Deterministic PII/secret oracle over EVERY captured egress (the §2 capture
    logs + this run's chat egress). aidefence_has_pii (MCP) is the secondary oracle,
    run separately — it misses bare phones (E9), so deterministic is primary."""
    print("=== aidefence-allchannels (oracle over every capture) ===")
    files = sorted(CAPTURES.glob("*.jsonl")) + ([EGRESS_LOG] if EGRESS_LOG.exists() else [])
    total, flagged = 0, 0
    for f in files:
        try:
            recs = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        except Exception:
            continue
        for r in recs:
            total += 1
            blob = r.get("egress") or r.get("body_preview") or json.dumps(r)
            hits = _scan(blob)
            if hits:
                flagged += 1
                print(f"  PII on capture [{f.name}] {r.get('channel', r.get('model',''))}: {hits}")
    print(f"  SCANNED {total} capture records across {len(files)} channels | PII-bearing={flagged}")
    return {"scanned": total, "flagged": flagged}


WATCHERS = {
    "egress-reconciler-fleet": egress_reconciler_fleet,
    "embed-vector-watcher": embed_vector_watcher,
    "mcp-context-watcher": mcp_context_watcher,
    "aidefence-allchannels": aidefence_allchannels,
}

if __name__ == "__main__":
    name = None
    if "--name" in sys.argv:
        name = sys.argv[sys.argv.index("--name") + 1]
    if name not in WATCHERS:
        print("usage: python watchers.py --name {" + "|".join(WATCHERS) + "}")
        sys.exit(2)
    WATCHERS[name]()
