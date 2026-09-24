"""Extension of k_window_culprits.py: all W in {1,2,4,7}, both models. For each request: does it contain the
benign samples that PG2 flags ON THEIR OWN (sample-level p>=0.5 in guard-bench's ref_scores.json)? Report
request FPR@0.5 overall, among requests containing >=1 such sample, and among requests containing none."""
import json, sys
import numpy as np
from tokenizers import Tokenizer
GB, CORPUS, TOKDIR, OUT = sys.argv[1:5]
CONTENT = 510
tok = Tokenizer.from_file(f"{TOKDIR}/tokenizer.json"); tok.no_truncation(); tok.no_padding()
ben = [json.loads(l) for l in open(f"{CORPUS}/benign.jsonl") if l.strip()]
texts = [r["text"] for r in ben]
ref = json.load(open(f"{GB}/ref_scores.json"))
selfflag = {i for i, r in enumerate(ben) if ref[r["id"]]["p1"] >= 0.5}
def build(W, n, seed):
    rng = np.random.default_rng(seed); need = W * CONTENT; reqs = []
    for _ in range(n):
        order = rng.permutation(len(texts)); parts, idx, k = [], [], 0
        while True:
            parts.append(texts[order[k % len(order)]]); idx.append(int(order[k % len(order)])); k += 1
            if k % 16 == 0 or k >= 3 * need:
                text = " ".join(parts); enc = tok.encode(text, add_special_tokens=False)
                if len(enc.ids) >= need:
                    cut = enc.offsets[need - 1][1]; text = text[:cut]; break
        starts, pos = [], 0
        for p in parts:
            starts.append(pos); pos += len(p) + 1
        reqs.append({"chars": len(text), "n_samples": k, "members": {i for i, s in zip(idx, starts) if s < cut}})
    return reqs
meta = json.load(open(f"{GB}/multiwindow_requests_meta.json"))
sc = json.load(open(f"{GB}/scores_trt_fp16_static.json"))
res = {"self_flagged_benign_samples": [{"id": ben[i]["id"], "p1": ref[ben[i]["id"]]["p1"], "text": texts[i]} for i in sorted(selfflag)]}
for W in ("1", "2", "4", "7"):
    reqs = build(int(W), len(meta[W]), 100 + int(W))
    ok = all(r["chars"] == m["chars"] and r["n_samples"] == m["n_samples"] for r, m in zip(reqs, meta[W]))
    L = np.array(sc["multiwindow"][W], dtype=np.float64)
    p = np.exp(L[..., 1]) / np.exp(L).sum(-1)
    fl = p.max(axis=1) >= 0.5
    has = np.array([bool(r["members"] & selfflag) for r in reqs])
    res[W] = {"reconstruction_matches_meta": ok, "n": len(reqs), "request_fpr": round(float(fl.mean()), 4),
              "share_containing_selfflagged": round(float(has.mean()), 4),
              "fpr_if_contains": round(float(fl[has].mean()), 4) if has.any() else None,
              "fpr_if_not": round(float(fl[~has].mean()), 4) if (~has).any() else None,
              "flagged_not_containing": int(fl[~has].sum()), "n_not_containing": int((~has).sum())}
json.dump(res, open(OUT, "w"), indent=1)
print("self-flagged benign samples:", res["self_flagged_benign_samples"])
for W in ("1", "2", "4", "7"):
    print("W", W, res[W])
