"""K: 'At long-window-1% thresholds (0.902/0.984) ... ALL recall 18.3%/22.3%'. The 99th percentile of long benign
windows is itself set by the replicated SQL snippet. Recompute the long-window 1% threshold WITHOUT snippet windows
and the recall (guard-bench's own per-sample malicious scores, trt_fp16_static) at both thresholds."""
import json, sys
import numpy as np
from tokenizers import Tokenizer
GB, CORPUS, TOKDIR, M = sys.argv[1:5]
tok = Tokenizer.from_file(f"{TOKDIR}/tokenizer.json"); tok.no_truncation(); tok.no_padding()
ben = [json.loads(l) for l in open(f"{CORPUS}/benign.jsonl") if l.strip()]
mal = [json.loads(l) for l in open(f"{CORPUS}/malicious.jsonl") if l.strip()]
texts = [r["text"] for r in ben]
ref = json.load(open(f"{GB}/ref_scores.json"))
hot = {i for i, r in enumerate(ben) if ref[r["id"]]["p1"] >= 0.5}
rng = np.random.default_rng(101); mem = []
for _ in range(1000):
    order = rng.permutation(len(texts)); parts, idx, k = [], [], 0
    while True:
        parts.append(texts[order[k % len(order)]]); idx.append(int(order[k % len(order)])); k += 1
        if k % 16 == 0 or k >= 1530:
            text = " ".join(parts); enc = tok.encode(text, add_special_tokens=False)
            if len(enc.ids) >= 510:
                cut = enc.offsets[509][1]; break
    starts, pos = [], 0
    for p in parts:
        starts.append(pos); pos += len(p) + 1
    mem.append({i for i, s in zip(idx, starts) if s < cut})
sc = json.load(open(f"{GB}/scores_trt_fp16_static.json"))
L = np.array(sc["multiwindow"]["1"], dtype=np.float64)[:, 0, :]
p = np.exp(L[:, 1]) / np.exp(L).sum(-1)
has = np.array([bool(m & hot) for m in mem])
t_all = float(np.quantile(p, 0.99, method="higher")); t_clean = float(np.quantile(p[~has], 0.99, method="higher"))
Lc = {k: v for k, v in sc["corpus"].items()}
def p1(k):
    a, b = Lc[k]; m = max(a, b); return np.exp(b - m) / (np.exp(a - m) + np.exp(b - m))
OUT = {"data_leakage", "sql_injection", "command_injection", "path_traversal"}
def rec(t, fams=None):
    xs = [r for r in mal if fams is None or r["family"] not in fams]
    return round(sum(p1(r["id"]) >= t for r in xs) / len(xs), 4), len(xs)
print(M, f"long-window t1 (all windows)={t_all:.4f} -> recall ALL {rec(t_all)} in-scope {rec(t_all, OUT)}",
      f"| t1 (windows w/o snippet)={t_clean:.4f} -> recall ALL {rec(t_clean)} in-scope {rec(t_clean, OUT)}")
