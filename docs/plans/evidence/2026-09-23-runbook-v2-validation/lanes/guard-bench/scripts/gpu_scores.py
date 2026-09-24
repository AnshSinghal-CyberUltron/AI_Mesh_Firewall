#!/usr/bin/env python3
"""Exp E: logits from every GPU backend for (1) each corpus sample as one window padded to 512 and
(2) multi-window BENIGN requests built by concatenating shuffled benign samples.

Multi-window request construction (seeded): shuffle benign samples, join with a single space until the
text has >= W*510 content tokens, cut the text at the character offset of token W*510, then split into W
non-overlapping 510-token windows ([CLS] w [SEP], padded to 512). N requests per W in {1,2,4,7}.
Outputs one JSON per backend with raw logits; all rates/thresholds are computed by analyze_quality.py.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg2common as C  # noqa: E402
import ortsess  # noqa: E402


def build_requests(tok, corpus, W, n, seed):
    rows = [r["text"] for r in C.load_corpus(corpus) if r["label"] == "benign"]
    rng = np.random.default_rng(seed)
    need = W * C.CONTENT
    reqs = []
    for _ in range(n):
        order = rng.permutation(len(rows))
        parts, k = [], 0
        text = ""
        while True:
            parts.append(rows[order[k % len(order)]])
            k += 1
            if k % 16 == 0 or k >= 3 * need:
                text = " ".join(parts)
                enc = tok.encode(text, add_special_tokens=False)
                if len(enc.ids) >= need:
                    text = text[: enc.offsets[need - 1][1]]
                    break
        ids = C.content_ids(tok, text)[:need]
        wins = [ids[i * C.CONTENT:(i + 1) * C.CONTENT] for i in range(W)]
        reqs.append({"chars": len(text), "ntok": len(ids), "n_samples": k, "wins": wins})
    return reqs


def run_batched(sess, windows, bs=64):
    out = []
    for i in range(0, len(windows), bs):
        ids, mask = C.batch_from_windows(windows[i:i + bs])
        out.append(sess.run(None, {"input_ids": ids, "attention_mask": mask})[0])
    return np.concatenate(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx-dir", required=True)
    ap.add_argument("--tok-dir", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--backends", default="trt_fp16_dyn,trt_fp16_static,cuda_fp32,cuda_fp16")
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    import onnxruntime as ort
    try:
        ort.preload_dlls()
    except Exception:
        pass
    tok = C.load_tokenizer(args.tok_dir)
    corpus = C.load_corpus(args.corpus)
    reqs = {W: build_requests(tok, args.corpus, W, args.n, seed=100 + W) for W in (1, 2, 4, 7)}
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "multiwindow_requests_meta.json").write_text(json.dumps(
        {W: [{k: r[k] for k in ("chars", "ntok", "n_samples")} for r in rs] for W, rs in reqs.items()}))
    for backend in args.backends.split(","):
        sess, info = ortsess.make_session(args.onnx_dir, backend, W=1, seq=512, opt_batch=16, max_batch=64)
        ortsess.assert_backend(sess, backend)
        cw = [C.content_ids(tok, r["text"]) for r in corpus]
        if backend == "trt_fp16_static":  # engine pinned to 1x512: one window per call
            lg = np.concatenate([run_batched(sess, [w], bs=1) for w in cw])
        else:
            lg = run_batched(sess, cw)
        res = {"backend": backend, "session": info,
               "corpus": {r["id"]: [float(a), float(b)] for r, (a, b) in zip(corpus, lg)}, "multiwindow": {}}
        for W, rs in reqs.items():
            flat = [w for r in rs for w in r["wins"]]
            if backend == "trt_fp16_static":
                L = np.concatenate([run_batched(sess, [w], bs=1) for w in flat])
            else:
                L = run_batched(sess, flat)
            L = L.reshape(len(rs), W, 2)
            res["multiwindow"][W] = L.tolist()
        res["nonfinite"] = int(sum(1 for v in res["corpus"].values() if not np.all(np.isfinite(v))))
        (out / f"scores_{backend}.json").write_text(json.dumps(res))
        print(backend, "done; nonfinite", res["nonfinite"], flush=True)
        del sess


if __name__ == "__main__":
    main()
