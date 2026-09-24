"""C3 labelled-detection scoring. Labels are the oracle, not v1 or v2."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from gateway_v2.contracts.parity.v1_oracle import v1_disposition

CANONICAL_BACKTICKS: tuple[str, ...] = (
    "Can you explain what the `ls -la` command prints in each column?",
    "What does `git status` show when the working tree is clean?",
    "How do I read the value of `process.env.NODE_ENV` in a Node app?",
    "Please document the `useEffect` hook and its dependency array.",
    "Why does `npm ci` behave differently from `npm install` in CI?",
)


def corpus_root() -> Path:
    return Path(__file__).resolve().parents[4] / "tests" / "detection_corpus"


def _load_jsonl(path: Path) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        rows.append(
            {
                "id": str(obj.get("id", "")),
                "text": str(obj.get("text", "")),
                "label": str(obj.get("label", "")),
                "family": str(obj.get("family", "")),
            },
        )
    return tuple(rows)


@dataclass(frozen=True)
class FamilyScore:
    family: str
    label: str
    n: int
    flagged: int
    rate: float


def _rate(flagged: int, n: int) -> float:
    if n == 0:
        return 0.0
    return round(flagged / n, 4)


def score_v1(root: Path | None = None) -> tuple[FamilyScore, ...]:
    base = root or corpus_root()
    malicious = _load_jsonl(base / "malicious.jsonl")
    benign = _load_jsonl(base / "benign.jsonl")
    fam_n: dict[tuple[str, str], int] = {}
    fam_hit: dict[tuple[str, str], int] = {}
    for item in malicious + benign:
        key = (item["family"], item["label"])
        fam_n[key] = fam_n.get(key, 0) + 1
        if v1_disposition(item["text"]) == "block":
            fam_hit[key] = fam_hit.get(key, 0) + 1
    out: list[FamilyScore] = []
    for key, n in sorted(fam_n.items()):
        family, label = key
        flagged = fam_hit.get(key, 0)
        out.append(FamilyScore(family, label, n, flagged, _rate(flagged, n)))
    return tuple(out)


def backtick_blocks() -> int:
    return sum(1 for text in CANONICAL_BACKTICKS if v1_disposition(text) == "block")
