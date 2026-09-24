"""Guard scores on a harness corpus with rvproto's EXACT windowing (SemanticDetector) and backend
(LocalOnnxBackend, same TRT profile -> engine cache hit). Writes a summary JSON plus a per-request
JSONL (id, class, expected input disposition, tokens, every window score, max) so FPR / recall can
be recomputed offline at any threshold.
  RV_GUARD_BACKEND=local_gpu RV_GUARD_MODEL=... RV_GUARD_TOKENIZER=... python tools/corpus_fpr.py corpus.jsonl out.json
Messages are system+user text with the {{RVNONCE}} placeholder replaced by a fixed benign nonce."""
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rvproto.detect.guard.batcher import _bucket, _softmax_p1  # noqa: E402
from rvproto.detect.guard.factory import model_hash  # noqa: E402
from rvproto.detect.guard.local_onnx import LocalOnnxBackend  # noqa: E402
from rvproto.detect.semantic import SemanticDetector  # noqa: E402
from rvproto.runtime.config import load_settings  # noqa: E402
from rvproto.runtime.metrics import Registry  # noqa: E402

s = load_settings()
be = LocalOnnxBackend(s, Registry(0), gpu=s.guard_backend == "local_gpu", device_id=0, model_hash=model_hash(s))
be.session = be._make_session()
sem = SemanticDetector(s.guard_tokenizer, be, window=s.window_tokens, overlap=s.window_overlap,
                       max_windows=s.max_windows, model_hash=be.model_hash)
rows = [json.loads(line) for line in open(sys.argv[1])]
scores, windows, t0 = [], [], time.time()
per_request = open(sys.argv[2].replace(".json", "") + ".requests.jsonl", "w")
for r in rows:
    texts = [m["content"].replace("{{RVNONCE}}", "nonce-0000") for m in r["messages"] if isinstance(m.get("content"), str)]
    w = sem.windows(texts)
    ps = []
    for i in range(0, len(w.rows), s.guard_batch):
        chunk = w.rows[i:i + s.guard_batch]
        seq = _bucket(max(len(x) for x in chunk), s.guard_seq_buckets)  # same padding as the batcher
        ids = np.zeros((len(chunk), seq), np.int64)
        mask = np.zeros_like(ids)
        for k, x in enumerate(chunk):
            ids[k, :len(x)] = x
            mask[k, :len(x)] = 1
        ps.extend(_softmax_p1(be._infer(ids, mask)).tolist())
    scores.append(max(ps))
    windows.append(len(w.rows))
    per_request.write(json.dumps({"id": r.get("id"), "class": r.get("class"),
                                  "expect_input": (r.get("expect") or {}).get("input"),
                                  "tokens_in": r.get("tokens_in"), "windows": len(w.rows),
                                  "window_scores": [round(float(p), 6) for p in ps],
                                  "max_score": round(float(max(ps)), 6),
                                  "argmax_window": int(np.argmax(ps))}) + "\n")
per_request.close()
sc = np.array(scores)
cls = np.array([r.get("class") for r in rows])
benign = cls == "benign"
inj = cls == "injection"
out = {"corpus": sys.argv[1], "model": s.guard_model, "n": len(rows), "seconds": round(time.time() - t0, 1),
       "backend": {"batch": s.guard_batch, "seq_buckets": list(s.guard_seq_buckets), "providers": be.providers},
       "classes": {c: int((cls == c).sum()) for c in sorted(set(cls.tolist()))},
       "benign_fpr_at": {t: round(float((sc[benign] >= t).mean()), 5) for t in (0.5, 0.8, 0.9, 0.95, 0.99)}
       if benign.any() else None,
       "injection_recall_at": {t: round(float((sc[inj] >= t).mean()), 5) for t in (0.5, 0.8, 0.9, 0.95, 0.99)}
       if inj.any() else None,
       "windows_hist": {str(k): windows.count(k) for k in sorted(set(windows))},
       "fp_at_0.5": int((sc >= 0.5).sum()), "fpr_at_0.5": round(float((sc >= 0.5).mean()), 5),
       "fpr_at_0.8": round(float((sc >= 0.8).mean()), 5), "fpr_at_0.9": round(float((sc >= 0.9).mean()), 5),
       "p99_score": round(float(np.quantile(sc, 0.99)), 4)}
json.dump(out, open(sys.argv[2], "w"), indent=1)
print(json.dumps(out))
