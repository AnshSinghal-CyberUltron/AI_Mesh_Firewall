"""Parity on FULL 512-position windows from real long prompts (complements parity.py).

  flock /home/claude/work/.heavy.lock nice -n 10 .f2-scratch/venv/bin/python -I parity_full_windows.py

parity.py's fixed corpus is mostly short rows (28 full windows). This samples 60 full windows
(seeded) from the in-the-wild jailbreak (30) and regular (30) prompts and compares PyTorch fp32
logits with ONNX Runtime logits on identical unpadded ids. Also compares a batch of 8 full windows
(one ORT call) with the same windows run one at a time. Writes results/parity_full_windows.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pyarrow.parquet as pq
import torch
from f2common import (
    EVIDENCE_DIR,
    MODEL_DIR,
    OUT_DIR,
    load_tokenizer,
    make_session,
    p_malicious,
    plan_windows,
    run_rows,
)
from transformers import AutoModelForSequenceClassification

BASE = OUT_DIR / "datasets/TrustAIRLab__in-the-wild-jailbreak-prompts"


def full_windows(tok, path: Path, take: int, seed: int) -> list[tuple[int, ...]]:
    texts = pq.read_table(path, columns=["prompt"]).column("prompt").to_pylist()
    rng = np.random.default_rng(seed)
    out: list[tuple[int, ...]] = []
    for i in rng.permutation(len(texts)):
        for w in plan_windows(tok, texts[int(i)]).windows:
            if len(w.ids) == 512:
                out.append(w.ids)
                break
        if len(out) == take:
            break
    return out


def main() -> int:
    torch.set_num_threads(1)
    model = AutoModelForSequenceClassification.from_pretrained(str(MODEL_DIR))
    model.eval()
    tok = load_tokenizer()
    sess = make_session(threads=1)
    rows = (full_windows(tok, BASE / "jailbreak_2023_12_25/train-00000-of-00001.parquet", 30, 1)
            + full_windows(tok, BASE / "regular_2023_12_25/train-00000-of-00001.parquet", 30, 2))
    max_abs = max_p = 0.0
    agree = {t: 0 for t in (0.5, 0.9, 0.95)}
    ort_single = []
    for ids in rows:
        arr = torch.tensor([ids], dtype=torch.int64)
        with torch.no_grad():
            pt = model(input_ids=arr, attention_mask=torch.ones_like(arr)).logits.numpy()
        ox = run_rows(sess, [ids])
        max_abs = max(max_abs, float(np.max(np.abs(pt - ox))))
        a, b = float(p_malicious(pt)[0]), float(p_malicious(ox)[0])
        max_p = max(max_p, abs(a - b))
        for t in agree:
            agree[t] += int((a >= t) == (b >= t))
        ort_single.append(b)
    batch_diff = 0.0
    for i in range(0, len(rows), 8):
        pb = p_malicious(run_rows(sess, rows[i:i + 8]))
        batch_diff = max(batch_diff, float(np.max(np.abs(pb - np.asarray(ort_single[i:i + 8])))))
    report = {"full_windows": len(rows), "max_abs_logit_diff": max_abs,
              "max_abs_p_malicious_diff": max_p,
              "decision_agreement": {str(t): f"{v}/{len(rows)}" for t, v in agree.items()},
              "ort_batch8_vs_single_max_abs_p_diff": batch_diff,
              "windows_with_p_ge_0.5": int(sum(1 for p in ort_single if p >= 0.5))}
    (EVIDENCE_DIR / "results" / "parity_full_windows.json").write_text(
        json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if all(v == len(rows) for v in agree.values()) and max_abs < 1e-4 else 1


if __name__ == "__main__":
    raise SystemExit(main())
