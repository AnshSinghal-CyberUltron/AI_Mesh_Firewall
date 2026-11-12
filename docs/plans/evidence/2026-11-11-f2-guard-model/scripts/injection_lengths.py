"""Token-length distribution of the injection sets (input to the window-overlap choice).

  .f2-scratch/venv/bin/python -I injection_lengths.py   (tokenizer only, seconds of CPU)

Uses the same items as eval_datasets.py (same seed and samples). Writes results/injection_lengths.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import eval_datasets
import numpy as np
from f2common import EVIDENCE_DIR, load_tokenizer

SETS = {
    "explicit_injection (gandalf+deepset label1+inrepo PI/goal_hijacking)": lambda r: (
        r["label"] == 1 and r["dataset"] in (
            "gandalf", "deepset", "inrepo:prompt_injection", "inrepo:goal_hijacking")),
    "yanis multilingual": lambda r: r["dataset"] == "yanis:multilingual",
    "jailbreaks sampled (jackhhao+inthewild)": lambda r: (
        r["label"] == 1 and r["dataset"] in ("jackhhao", "inthewild:jailbreak")),
}


def main() -> int:
    tok = load_tokenizer()
    items = eval_datasets.load_items()
    out = {}
    for name, pred in SETS.items():
        lengths = np.array([len(tok.encode_batch([r["text"]], add_special_tokens=False)[0].ids)
                            for r in items if pred(r)])
        out[name] = {
            "n": len(lengths),
            **{f"p{q}": int(np.percentile(lengths, q)) for q in (50, 90, 95, 99)},
            "max": int(lengths.max()),
            "share_le_64": round(float((lengths <= 64).mean()), 4),
            "share_le_128": round(float((lengths <= 128).mean()), 4),
        }
    (EVIDENCE_DIR / "results" / "injection_lengths.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
