"""Shared helpers for the PG2 guard benchmark (tokenizer, windowing, batching, stats).

Label convention (verified empirically in export_onnx.py against the corpus):
logits[:, 1] is the MALICIOUS class; score = softmax(logits)[:, 1].
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import subprocess
import time
from pathlib import Path

import numpy as np

CLS_ID, SEP_ID, PAD_ID = 1, 2, 0
MAX_SEQ = 512
CONTENT = MAX_SEQ - 2  # [CLS] ... [SEP]


def load_tokenizer(model_dir):
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(Path(model_dir) / "tokenizer.json"))
    # tokenizer.json ships with truncation=512 and Fixed(512) padding enabled; both
    # must be off or long inputs are silently truncated.
    tok.no_truncation()
    tok.no_padding()
    return tok


def content_ids(tok, text):
    return tok.encode(text, add_special_tokens=False).ids


def windows_from_ids(ids, overlap=0, content=CONTENT):
    """Split content token ids into windows of <= `content` tokens with `overlap`."""
    if not ids:
        return [[]]
    step = content - overlap
    out = []
    start = 0
    while True:
        out.append(ids[start:start + content])
        if start + content >= len(ids):
            break
        start += step
    return out


def batch_from_windows(wins, seq=MAX_SEQ, pad_to=None):
    """Build int64 input_ids/attention_mask [W, L]; L = seq (fixed) or longest window (pad_to='longest')."""
    rows = [[CLS_ID] + list(w) + [SEP_ID] for w in wins]
    L = max(len(r) for r in rows) if pad_to == "longest" else seq
    ids = np.full((len(rows), L), PAD_ID, dtype=np.int64)
    mask = np.zeros((len(rows), L), dtype=np.int64)
    for i, r in enumerate(rows):
        r = r[:L]
        ids[i, : len(r)] = r
        mask[i, : len(r)] = 1
    return ids, mask


def softmax_p1(logits):
    logits = np.asarray(logits, dtype=np.float64)
    m = logits.max(axis=-1, keepdims=True)
    e = np.exp(logits - m)
    return (e[..., 1] / e.sum(axis=-1))


def load_corpus(corpus_dir):
    rows = []
    for f in ("benign", "malicious"):
        with open(Path(corpus_dir) / f"{f}.jsonl") as fh:
            rows += [json.loads(l) for l in fh if l.strip()]
    return rows


def pct(xs, p):
    """Nearest-rank percentile on the raw samples (no interpolation)."""
    if len(xs) == 0:
        return float("nan")
    ys = np.sort(np.asarray(xs, dtype=np.float64))
    k = max(0, min(len(ys) - 1, int(math.ceil(p / 100.0 * len(ys))) - 1))
    return float(ys[k])


def summarize_ms(samples_ms):
    a = np.asarray(samples_ms, dtype=np.float64)
    return {
        "n": int(a.size),
        "mean": round(float(a.mean()), 4),
        "p50": round(pct(a, 50), 4),
        "p90": round(pct(a, 90), 4),
        "p99": round(pct(a, 99), 4),
        "p999": round(pct(a, 99.9), 4),
        "max": round(float(a.max()), 4),
        "min": round(float(a.min()), 4),
    }


def sha256_file(path, bufsize=1 << 22):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def host_facts():
    facts = {"host": platform.node(), "python": platform.python_version(), "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        facts["gpu"] = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,driver_version,clocks.sm,clocks.max.sm,temperature.gpu,power.draw", "--format=csv,noheader"],
            text=True, timeout=10).strip()
    except Exception:
        facts["gpu"] = None
    facts["nproc"] = os.cpu_count()
    return facts


def benign_filler_ids(tok, corpus_dir, n_tokens, seed=0):
    """Deterministic benign token stream of >= n_tokens built by concatenating shuffled benign samples."""
    rows = [r for r in load_corpus(corpus_dir) if r["label"] == "benign"]
    rng = np.random.default_rng(seed)
    ids = []
    while len(ids) < n_tokens:
        for i in rng.permutation(len(rows)):
            ids += content_ids(tok, rows[i]["text"] + " ")
            if len(ids) >= n_tokens:
                break
    return ids[:n_tokens]
