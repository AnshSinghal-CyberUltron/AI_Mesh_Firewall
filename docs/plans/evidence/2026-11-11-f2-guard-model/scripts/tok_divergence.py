"""Score the two tokenizer dumps from tok_dump.py with the ONNX model and compare decisions.

  .f2-scratch/venv/bin/python -I tok_divergence.py <tok_tf4.json> <tok_tf5.json>

Writes results/tokenizer_divergence.json: id agreement per front-end/version and the effect of the
transformers 5.x AutoTokenizer ids on P(malicious) and on decisions over the in-repo corpus.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from f2common import EVIDENCE_DIR, make_session, p_malicious, run_rows


def main() -> int:
    tf4 = json.loads(Path(sys.argv[1]).read_text())
    tf5 = json.loads(Path(sys.argv[2]).read_text())
    sess = make_session(threads=1)
    rows = tf4["rows"]
    flips = {0.5: 0, 0.9: 0}
    stats = {"raw": {"tp": 0, "fp": 0, "pos": 0, "neg": 0}, "tf5_auto": {"tp": 0, "fp": 0,
                                                                        "pos": 0, "neg": 0}}
    max_diff = 0.0
    examples = {}
    for rid, item in rows.items():
        raw_ids = item["raw"]
        auto5 = tf5["rows"][rid]["auto"]
        if len(raw_ids) > 512 or len(auto5) > 512:
            continue
        p_raw = float(p_malicious(run_rows(sess, [raw_ids]))[0])
        p_5 = float(p_malicious(run_rows(sess, [auto5]))[0])
        max_diff = max(max_diff, abs(p_raw - p_5))
        for t in flips:
            flips[t] += int((p_raw >= t) != (p_5 >= t))
        if rid.startswith(("ws-", "nfc-")):
            examples[rid] = {"p_shipped_tokenizer": round(p_raw, 6), "p_transformers5": round(p_5, 6)}
        label = "pos" if rid.startswith("mal-") else ("neg" if rid.startswith("ben-") else None)
        if label is None:
            continue
        for name, p in (("raw", p_raw), ("tf5_auto", p_5)):
            stats[name][label] += 1
            if p >= 0.5:
                stats[name]["tp" if label == "pos" else "fp"] += 1
    report = {
        "versions": {"a": [tf4["transformers"], tf4["tokenizers"], tf4["auto_class"]],
                     "b": [tf5["transformers"], tf5["tokenizers"], tf5["auto_class"]]},
        "id_mismatch_rows": {
            "transformers4_auto_vs_tokenizer_json": sum(
                1 for v in rows.values() if v["auto"] != v["raw"]),
            "transformers5_auto_vs_tokenizer_json": sum(
                1 for v in tf5["rows"].values() if v["auto"] != v["raw"]),
            "tokenizers_0.22_vs_0.23_raw": sum(
                1 for k in rows if rows[k]["raw"] != tf5["rows"][k]["raw"]),
            "rows": len(rows),
        },
        "max_abs_p_diff_shipped_vs_transformers5": max_diff,
        "decision_flips": {str(k): v for k, v in flips.items()},
        "in_repo_corpus_at_0.5": {
            name: {"recall": f"{s['tp']}/{s['pos']}", "fpr": f"{s['fp']}/{s['neg']}"}
            for name, s in stats.items()
        },
        "whitespace_examples": examples,
    }
    out = EVIDENCE_DIR / "results" / "tokenizer_divergence.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
