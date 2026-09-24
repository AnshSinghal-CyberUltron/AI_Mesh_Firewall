#!/usr/bin/env python3
"""Exp A (tokenization, timed separately): HF `tokenizers` (Rust) cost on this host's vCPUs.

For each tokenizer and each target size (512 / 1,024 / 3,584 content tokens of benign English built
from tests/detection_corpus), time (a) encode only and (b) encode + 510-token windowing + int64
[W,512] input arrays (what the guard needs before inference). Single call on one thread, as a
gateway worker would do it. Raw ns samples saved.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg2common as C  # noqa: E402


def build_text(tok, corpus, n_tokens):
    rows = [r["text"] for r in C.load_corpus(corpus) if r["label"] == "benign"]
    rng = np.random.default_rng(n_tokens)
    parts, text = [], ""
    while True:
        for i in rng.permutation(len(rows)):
            parts.append(rows[i])
            text = " ".join(parts)
            if len(parts) % 8 == 0 and len(C.content_ids(tok, text)) >= n_tokens:
                ids = C.content_ids(tok, text)
                # trim to exactly n_tokens content tokens
                enc = tok.encode(text, add_special_tokens=False)
                end_char = enc.offsets[n_tokens - 1][1]
                return text[:end_char]


def main():
    tok_dirs, corpus, out = sys.argv[1].split(","), sys.argv[2], sys.argv[3]
    iters, warm = 2000, 200
    res = {"host": C.host_facts(), "iters": iters, "rows": []}
    pc = time.perf_counter_ns
    for td in tok_dirs:
        tok = C.load_tokenizer(td)
        for n in (512, 1024, 3584):
            text = build_text(tok, corpus, n)
            ntok = len(C.content_ids(tok, text))
            a = np.empty(iters, dtype=np.int64)
            b = np.empty(iters, dtype=np.int64)
            for _ in range(warm):
                C.content_ids(tok, text)
            for i in range(iters):
                t0 = pc()
                C.content_ids(tok, text)
                a[i] = pc() - t0
            for i in range(iters):
                t0 = pc()
                ids = C.content_ids(tok, text)
                wins = C.windows_from_ids(ids, overlap=0)
                C.batch_from_windows(wins)
                b[i] = pc() - t0
            row = {"tokenizer": Path(td).name, "target_tokens": n, "content_tokens": ntok, "chars": len(text),
                   "windows_510": len(C.windows_from_ids(C.content_ids(tok, text))),
                   "encode_ms": C.summarize_ms(a / 1e6), "encode_window_batch_ms": C.summarize_ms(b / 1e6),
                   "encode_ns": a.tolist(), "encode_window_batch_ns": b.tolist()}
            res["rows"].append(row)
            print(row["tokenizer"], n, ntok, len(text), "encode p50/p99", row["encode_ms"]["p50"], row["encode_ms"]["p99"],
                  "full p50/p99", row["encode_window_batch_ms"]["p50"], row["encode_window_batch_ms"]["p99"], flush=True)
    Path(out).write_text(json.dumps(res))


if __name__ == "__main__":
    main()
