"""Second part of the K claim: 'short-prompt-calibrated thresholds are useless on long text (14.8%/100% per
window)'. Is THAT also the one replicated snippet, or intrinsic to long concatenated text? W=1 windows only."""
import json, sys
import numpy as np
from tokenizers import Tokenizer
GB, CORPUS, TOKDIR, M = sys.argv[1:5]
tok = Tokenizer.from_file(f"{TOKDIR}/tokenizer.json"); tok.no_truncation(); tok.no_padding()
ben = [json.loads(l) for l in open(f"{CORPUS}/benign.jsonl") if l.strip()]
texts = [r["text"] for r in ben]
ref = json.load(open(f"{GB}/ref_scores.json"))
q = json.load(open(f"{GB}/quality_{M}.json"))
t1 = q["t1pct_on_reference_benign_samples"]
hot = {i for i, r in enumerate(ben) if ref[r["id"]]["p1"] >= 0.5}
rng = np.random.default_rng(101); need = 510; mem = []
for _ in range(1000):
    order = rng.permutation(len(texts)); parts, idx, k = [], [], 0
    while True:
        parts.append(texts[order[k % len(order)]]); idx.append(int(order[k % len(order)])); k += 1
        if k % 16 == 0 or k >= 3 * need:
            text = " ".join(parts); enc = tok.encode(text, add_special_tokens=False)
            if len(enc.ids) >= need:
                cut = enc.offsets[need - 1][1]; break
    starts, pos = [], 0
    for p in parts:
        starts.append(pos); pos += len(p) + 1
    mem.append({i for i, s in zip(idx, starts) if s < cut})
sc = json.load(open(f"{GB}/scores_trt_fp16_static.json"))
L = np.array(sc["multiwindow"]["1"], dtype=np.float64)[:, 0, :]
p = np.exp(L[:, 1]) / np.exp(L).sum(-1)
has = np.array([bool(m & hot) for m in mem])
print(M, f"t1(short-prompt 99th pct)={t1:.4f}",
      f"all windows >=t1: {np.mean(p >= t1):.3f}", f"| without snippet: {np.mean(p[~has] >= t1):.3f} (n={int((~has).sum())})",
      f"| median window score w/o snippet: {np.median(p[~has]):.4f} vs median short-prompt benign score: "
      f"{np.median([ref[r['id']]['p1'] for r in ben]):.5f}")
