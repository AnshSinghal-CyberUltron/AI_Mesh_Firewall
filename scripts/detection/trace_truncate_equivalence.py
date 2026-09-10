"""R1 gate: redacting a WINDOW must give byte-identical trace text to redacting everything.

The change exists because `_truncate` redacted the whole prompt to keep 1200 characters —
39.6% of firewall CPU, unbounded in prompt length. The risk it must not introduce is a
secret straddling the window cut whose visible half survives into the operator trace.

So the adversarial inputs are not decoration: every secret shape is planted at EVERY offset
across the cut, which is precisely where a windowing bug would show.

    docker exec aimeshperf-gateway-1 /app/gateway/.venv/bin/python /tmp/trunc_gate.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, "/app/gateway")
sys.path.insert(0, "/app")

import pipeline_trace as PT  # noqa: E402

LIMIT = 1200
CORPUS_DIR = os.environ.get("CORPUS_DIR", "/tmp/detection_corpus")


def old_truncate(text: str, limit: int = LIMIT) -> str:
    """The pre-change implementation, verbatim."""
    raw = (text or "").strip()
    if raw and PT._redact_all is not None:
        try:
            raw = PT._redact_all(raw)
        except Exception:
            pass
    if len(raw) <= limit:
        return raw
    return raw[:limit] + "…"


SECRETS = [
    "xoxb-1234567890-abcdefghijklmnopqrstuvwx",
    "glpat-ABCDEFGHIJKLMNOPQRSTU",
    "sk-ant-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r",
    "Bearer ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
    "password: hunter2hunter2hunter2",
    "mongodb://user:pass@host.example.com/db?opt=1",
    "ravi@example.com",
    "4111111111111111",
    "123-45-6789",
    "-----BEGIN RSA PRIVATE KEY-----",
    "AccountKey=AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHHIIIIJJJJKKKK",
    "https://hooks.slack.com/services/T00/B00/XXXXXXXXXXXXXXXXXXXXXXXX",
]

FILLER = "The deployment runbook lists the rollback steps for the platform team. "


def inputs():
    out = []
    # 1) corpus
    for fn in ("benign.jsonl", "malicious.jsonl"):
        p = os.path.join(CORPUS_DIR, fn)
        if not os.path.exists(p):
            continue
        with open(p) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for k in ("text", "prompt", "input", "content"):
                    v = rec.get(k)
                    if isinstance(v, str) and v:
                        out.append(v)
                        break
    # 2) EVERY secret at EVERY offset across the cut — where a windowing bug lives
    cut = LIMIT + PT._REDACT_WINDOW_OVERLAP
    base = (FILLER * (6000 // len(FILLER) + 1))
    for sec in SECRETS:
        for off in range(cut - len(sec) - 4, cut + 6):
            if off < 0:
                continue
            out.append(base[:off] + sec + base[off:6000])
    # 3) shapes that stress the window rule itself
    out += [
        "",
        "   ",
        "A" * 10000,                                   # one run, no whitespace at all
        "A" * 5000 + " xoxb-1234567890-abcdefghij",    # run > HARD_CAP then a secret
        ("tok " * 400) + "ravi@example.com",
        "café ☕ " * 900,
        FILLER * 200,
        (FILLER * 20) + "\n" * 3000 + "ravi@example.com",
    ]
    return out


def main() -> int:
    texts = inputs()
    print(f"inputs: {len(texts)}  (LIMIT={LIMIT}, OVERLAP={PT._REDACT_WINDOW_OVERLAP}, "
          f"HARD_CAP={PT._REDACT_WINDOW_HARD_CAP})")
    bad = 0
    for i, t in enumerate(texts):
        a = PT._truncate(t, LIMIT)
        b = old_truncate(t, LIMIT)
        if a != b:
            bad += 1
            if bad <= 3:
                # show the first divergent character, not two 1200-char blobs
                j = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]),
                         min(len(a), len(b)))
                print(f"\nDIVERGENCE #{i} at char {j} (len {len(t)}):")
                print(f"  window : ...{a[max(0,j-40):j+40]!r}")
                print(f"  full   : ...{b[max(0,j-40):j+40]!r}")
    if bad:
        print(f"\nFAIL: {bad} of {len(texts)} inputs differ — windowing CHANGED the trace")
        return 1
    print(f"\nPASS: {len(texts)} inputs, trace text byte-identical")
    return 0


raise SystemExit(main())
