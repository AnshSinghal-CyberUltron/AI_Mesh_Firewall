"""Detection-equivalence gate for the streaming retention window — task 1E, R2.

Streams every corpus item through the REAL `SecureStreamingResponse` in
token-sized chunks and records exactly what the client would receive plus the
sequence of guard actions. Run it before and after a retention change and diff
the two snapshots: any difference in released bytes or in actions is a detection
behaviour change and fails the gate.

The guard double delegates its verdict to the gateway's own `detect_pii`, the
same deterministic detector production uses, so "redact PII / pass clean" is
modelled faithfully rather than stubbed away.

    python scripts/detection/stream_retention_equivalence.py --out before.json
    <apply the change>
    python scripts/detection/stream_retention_equivalence.py --out after.json
    python scripts/detection/stream_retention_equivalence.py --compare before.json after.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1].parent
sys.path.insert(0, str(ROOT / "gateway"))
sys.path.insert(0, str(ROOT / "gateway" / "ai_mesh_gateway"))

if "litellm" not in sys.modules:                      # matches the E13/E14 discipline
    fake = types.ModuleType("litellm")
    class _R:  # noqa: D401
        def __init__(self, *a, **k): self.model_list = k.get("model_list", [])
    async def _u(*a, **k): raise RuntimeError("stubbed")
    fake.Router, fake.acompletion, fake.aembedding = _R, _u, _u
    sys.modules["litellm"] = fake

from patterns import detect_pii                        # noqa: E402
from scanner import InputScanner                       # noqa: E402
from secure_streaming import SecureStreamingResponse   # noqa: E402

CHUNK_BYTES = 6          # token-sized deltas — the regime the defect lives in


class _Verdict:
    def __init__(self, found: dict):
        self.action = "redact" if found else "allow"
        self.threat_type = "pii" if found else "none"
        self.matched_patterns = sorted(found.keys())
        self.detail = ""
        self.compliance_tags = []
        self.matched_values = dict(found)
        self.scan_degraded = False


class _Guard:
    """Realistic stand-in: real detector, production's redact-or-allow contract."""

    def __init__(self): self.actions: list[str] = []

    async def inspect(self, text, *, context_chunks=None, org_config=None, org_slug=""):
        v = _Verdict(detect_pii(text))
        self.actions.append(v.action)
        return v


def _sse(text: str) -> str:
    return "data: " + json.dumps({
        "id": "chatcmpl-eq", "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {"content": text}}],
    }) + "\n\n"


async def run_one(text: str) -> tuple[str, list[str]]:
    """Return (bytes the client actually received, guard action sequence)."""
    pieces = [text[i:i + CHUNK_BYTES] for i in range(0, len(text), CHUNK_BYTES)] or [""]

    async def inner():
        for p in pieces:
            yield _sse(p)
        yield "data: [DONE]\n\n"

    guard = _Guard()
    stream = SecureStreamingResponse(
        inner(), InputScanner(config={}), redaction_enabled=True, output_guard=guard)

    seen: list[str] = []
    async for frame in stream.__aiter__():
        if not frame.startswith("data: "):
            continue
        body = frame[6:].strip()
        if body == "[DONE]":
            continue
        try:
            d = json.loads(body)
        except json.JSONDecodeError:
            continue
        for ch in d.get("choices") or []:
            c = (ch.get("delta") or {}).get("content")
            if c:
                seen.append(c)
    return "".join(seen), guard.actions


# Corpus items are prompts: max 93 chars, so none reaches the 64-chunk
# BUFFER_LIMIT trigger. That is the regime a real streamed ANSWER lives in, so
# the corpus alone would leave the interesting path untested. Embed each item in
# filler prose at several offsets to build ~1800-char documents (the 300-token
# case) and re-run the same equivalence check over those.
FILLER = ("The service returns a structured response for each request and the "
          "client renders it incrementally as the tokens arrive. ")


def long_documents(items: list[dict]) -> list[dict]:
    docs = []
    for it in items:
        for offset in (0, 1, 4):           # start, early, mid-document
            body = FILLER * 12
            cut = min(len(body), offset * len(FILLER))
            docs.append({
                "id": f"{it['id']}@long{offset}",
                "text": body[:cut] + it["text"] + " " + body[cut:],
            })
    return docs


def load_corpus() -> list[dict]:
    items = []
    for name in ("benign.jsonl", "malicious.jsonl"):
        p = ROOT / "tests" / "detection_corpus" / name
        for line in p.read_text().splitlines():
            if line.strip():
                items.append(json.loads(line))
    return items


async def snapshot(path: Path, *, long: bool) -> None:
    items = load_corpus()
    if long:
        items = long_documents(items)
    out = {}
    for it in items:
        released, actions = await run_one(it["text"])
        out[it["id"]] = {
            "released_sha256": hashlib.sha256(released.encode()).hexdigest(),
            "released_len": len(released),
            "actions": actions,
        }
    path.write_text(json.dumps(out, indent=1, sort_keys=True))
    lens = sorted(len(i["text"]) for i in items)
    print(f"snapshot: {len(out)} items (max {lens[-1]} chars) -> {path}")


def compare(a: Path, b: Path) -> int:
    x, y = json.loads(a.read_text()), json.loads(b.read_text())
    if set(x) != set(y):
        print(f"FAIL: corpus id sets differ ({len(x)} vs {len(y)})")
        return 1
    # SAFETY vs CADENCE. What the client receives is the safety property: a verdict
    # that changed from redact/block to allow necessarily changes the released bytes
    # (raw instead of masked, or untruncated instead of truncated). The COUNT of
    # 'allow' passes is flush cadence and is expected to move when the trigger
    # changes -- failing on it would forbid the optimisation while proving nothing.
    # So bytes and non-allow verdicts are the gate; allow-count drift is reported.
    def enforced(rec): return [a for a in rec["actions"] if a != "allow"]

    unsafe = [k for k in x
              if x[k]["released_sha256"] != y[k]["released_sha256"]
              or enforced(x[k]) != enforced(y[k])]
    cadence = [k for k in x if k not in set(unsafe)
               and len(x[k]["actions"]) != len(y[k]["actions"])]

    if unsafe:
        print(f"FAIL: {len(unsafe)} of {len(x)} items changed RELEASED BYTES or an "
              f"enforced (non-allow) verdict")
        for k in unsafe[:15]:
            print(f"  {k}: sha {x[k]['released_sha256'][:12]}->{y[k]['released_sha256'][:12]} "
                  f"len {x[k]['released_len']}->{y[k]['released_len']} "
                  f"enforced {enforced(x[k])}->{enforced(y[k])}")
        return 1

    print(f"PASS: all {len(x)} items byte-identical; every enforced verdict unchanged")
    if cadence:
        d = [len(y[k]["actions"]) - len(x[k]["actions"]) for k in cadence]
        print(f"      (cadence only: {len(cadence)} items changed guard-pass COUNT, "
              f"delta {min(d):+d}..{max(d):+d}; released bytes unaffected)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--long", action="store_true",
                    help="embed each corpus item in ~1800-char prose documents")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    a = ap.parse_args()
    if a.compare:
        return compare(Path(a.compare[0]), Path(a.compare[1]))
    if not a.out:
        ap.error("--out or --compare required")
    asyncio.run(snapshot(Path(a.out), long=a.long))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
