"""Is guard-bench's long-window benign FPR (2.7% per 510-token window @0.5, PG2-22M) an artefact of how the
windows were BUILT (concatenating ~48 shuffled short benign corpus prompts per window, re-drawn with replacement
across 1,000 requests)? Reconstruct each W=1 window's constituent samples exactly (same seed/tokenizer/corpus as
guard-bench gpu_scores.py build_requests), verify against the saved meta, then attribute flagged windows to
constituent samples. Scores are guard-bench's own saved logits (no new inference here)."""
import json, sys
from collections import Counter, defaultdict
import numpy as np
from tokenizers import Tokenizer
GB, CORPUS, TOKDIR, OUT = sys.argv[1:5]
CONTENT = 510
tok = Tokenizer.from_file(f"{TOKDIR}/tokenizer.json"); tok.no_truncation(); tok.no_padding()
rows = []
for f in ("benign", "malicious"):
    rows += [json.loads(l) for l in open(f"{CORPUS}/{f}.jsonl") if l.strip()]
ben = [r for r in rows if r["label"] == "benign"]
texts = [r["text"] for r in ben]
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
        # which constituents survive the cut (start offset < cut)
        starts, pos = [], 0
        for p in parts:
            starts.append(pos); pos += len(p) + 1
        keep = [i for i, s in zip(idx, starts) if s < cut]
        reqs.append({"chars": len(text), "n_samples": k, "members": keep})
    return reqs
meta = json.load(open(f"{GB}/multiwindow_requests_meta.json"))
res = {}
for backend in ("trt_fp16_static", "cuda_fp32"):
    sc = json.load(open(f"{GB}/scores_{backend}.json"))
    for W in ("1",):
        reqs = build(int(W), len(meta[W]), 100 + int(W))
        ok = all(r["chars"] == m["chars"] and r["n_samples"] == m["n_samples"] for r, m in zip(reqs, meta[W]))
        L = np.array(sc["multiwindow"][W], dtype=np.float64)          # [n, W, 2]
        p = np.exp(L[..., 1]) / np.exp(L).sum(-1)                       # softmax p(malicious)
        pw = p.max(axis=1)
        flagged = pw >= 0.5
        pres = defaultdict(lambda: [0, 0])                              # sample -> [windows present, flagged present]
        for r, fl in zip(reqs, flagged):
            for i in set(r["members"]):
                pres[i][0] += 1; pres[i][1] += int(fl)
        base = flagged.mean()
        lift = sorted(((v[1] / v[0], v[0], v[1], i) for i, v in pres.items() if v[0] >= 20), reverse=True)[:12]
        ref = json.load(open(f"{GB}/ref_scores.json"))
        ref_by_text = {ref[k]["text"] if "text" in ref[k] else None: ref[k]["p1"] for k in ref}
        top = []
        for rate, npres, nfl, i in lift:
            rid = ben[i]["id"]; rp = ref[rid]["p1"] if rid in ref else None
            top.append({"sample": rid, "family": ben[i].get("family"), "p1_alone": rp, "windows_with": npres,
                        "flagged_with": nfl, "flag_rate_with": round(rate, 3), "text": texts[i][:90]})
        # remove the k strongest culprits: FPR of windows containing none of them
        out_k = {}
        for kk in (0, 1, 2, 3, 5, 10):
            bad = {t["sample"] for t in top[:kk]}
            badi = {i for i, b in enumerate(ben) if b["id"] in bad}
            m = np.array([not (set(r["members"]) & badi) for r in reqs])
            out_k[kk] = {"windows_left": int(m.sum()), "flagged": int(flagged[m].sum()),
                         "fpr": round(float(flagged[m].mean()), 4) if m.any() else None}
        res[f"{backend}_W{W}"] = {"reconstruction_matches_meta": ok, "n_windows": len(reqs),
                                   "per_window_fpr@0.5": round(float(base), 4), "mean_members": float(np.mean([len(r['members']) for r in reqs])),
                                   "top_culprits": top, "fpr_excluding_top_k": out_k}
json.dump(res, open(OUT, "w"), indent=1)
for k, v in res.items():
    print(k, "reconstruction_matches_meta:", v["reconstruction_matches_meta"], "fpr:", v["per_window_fpr@0.5"], "members/window:", round(v["mean_members"], 1))
    for t in v["top_culprits"][:6]:
        print("   ", t)
    print("    FPR excluding top-k culprits:", v["fpr_excluding_top_k"])
