"""R2 gate for task 7: does a Hyperscan prefilter ever UNDER-report vs `re`?

The hybrid design runs `re.sub` only for patterns the prefilter says matched. That is safe
only if the prefilter never misses a match `re` would have made.

**An under-report is not a slow path — it is redaction silently not happening.** It fails
open, on one input, while every output diff on benign text still looks perfect. This gate
exists to hunt that specific failure, which is why it compares MATCHING PATTERN SETS rather
than diffing redacted output.

Over-reporting is harmless: the `re` pass still decides whether to substitute.

    PYTHONPATH=<hyperscan> python scripts/detection/hyperscan_prefilter_gate.py

Exit 0 = R2 holds. Exit 1 = the hybrid would leak.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gateway"))
sys.path.insert(0, str(ROOT / "gateway" / "ai_mesh_gateway"))
sys.path.insert(0, str(ROOT / "shared"))

import patterns as P  # noqa: E402

try:
    import hyperscan as hs
except ImportError:
    print("hyperscan not importable — gate cannot run (this is not a pass)")
    raise SystemExit(2)

# Inputs that must never lose their redaction. Each is a class the guard exists to catch.
ADVERSARIAL = [
    "alex.morgan@corp.example.com",
    "4111-1111-1111-1111",
    "123-45-6789",
    "sk-" + "Az9Kp" * 20,
    "Bearer " + "x" * 40,
    "aws_secret_access_key = " + "A" * 40,
    "password: hunter2",
    "MRN 1234567",
    "+1 (555) 123-4567",
    "NPI 1234567890",
    "mongodb://user:pw@host/db",
    "-----BEGIN PRIVATE KEY-----",
    "​sk​-live​-key",                                   # zero-width
    "".join(chr(0xE0000 + ord(c)) for c in "sk-live-AB"),              # Unicode tag block
]


def main() -> int:
    allp = []
    for d in (P.PII_PATTERNS, P.PHI_PATTERNS, P.PCI_PATTERNS,
              P.SECRET_PATTERNS, P.CREDENTIAL_EXPOSURE_PATTERNS):
        allp += list(d.items())

    usable, rejected = [], []
    for k, p in allp:
        try:
            probe = hs.Database()
            probe.compile(expressions=[p.encode()], ids=[0], elements=1,
                          flags=[hs.HS_FLAG_CASELESS])
            usable.append((k, p))
        except Exception:                       # noqa: BLE001
            rejected.append(k)

    db = hs.Database()
    db.compile(expressions=[p.encode() for _, p in usable], ids=list(range(len(usable))),
               elements=len(usable), flags=[hs.HS_FLAG_CASELESS] * len(usable))
    scratch = hs.Scratch(db)
    compiled = [(k, re.compile(p, re.IGNORECASE)) for k, p in usable]

    texts = []
    d = ROOT / "tests" / "detection_corpus"
    for name in ("benign.jsonl", "malicious.jsonl"):
        for line in (d / name).read_text().splitlines():
            if line.strip():
                texts.append(json.loads(line)["text"])
    corpus_n = len(texts)
    texts += ADVERSARIAL

    under, over = [], 0
    for t in texts:
        re_hits = {k for k, c in compiled if c.search(t)}
        hs_hits: set[str] = set()

        def cb(i, frm, to, flags, ctx):          # noqa: ANN001
            hs_hits.add(compiled[i][0])

        try:
            db.scan(t.encode(), match_event_handler=cb, scratch=scratch)
        except Exception as exc:                 # noqa: BLE001
            under.append((t[:60], f"SCAN ERROR {exc}"))
            continue
        missing = re_hits - hs_hits
        if missing:
            under.append((t[:60], sorted(missing)))
        over += len(hs_hits - re_hits)

    print(f"patterns: {len(usable)} via Hyperscan, {len(rejected)} rejected "
          f"(zero-width assertions): {rejected}")
    print(f"inputs:   {corpus_n} corpus + {len(ADVERSARIAL)} adversarial\n")
    print(f"OVER-reports  (harmless, the re pass still decides): {over}")
    print(f"UNDER-reports (a leak: re would substitute, prefilter would not): {len(under)}")
    for t, m in under[:15]:
        print(f"   {t!r:<62} missing={m}")

    if under:
        print("\nR2 VIOLATED — the hybrid would silently stop redacting. Do not ship it.")
        return 1
    print("\nR2 HOLDS — no under-report on this corpus.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
