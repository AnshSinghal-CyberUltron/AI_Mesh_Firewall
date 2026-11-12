"""Per-window cost of the fp32 ONNX guard on CPU, and the tokenizer cost, measured separately.

  flock /home/claude/work/.heavy.lock nice -n 10 .f2-scratch/venv/bin/python -I bench_window.py

The host is shared (2 vCPUs, other agents' suites running), so wall-clock numbers are noisy and
are reported alongside PROCESS CPU time per call (``time.process_time_ns`` covers every thread in
this process, including ORT intra-op workers) which is what the capacity arithmetic uses.

Measured:
  * session cold start: file read + sha256, InferenceSession from bytes, first run, RSS deltas;
  * one window at seq 16/64/128/256/512 with intra_op threads 1 and 2 (batch 1);
  * batches of full 512 windows (2, 4) at threads 1 and 2;
  * concurrency: 1 shared session (intra_op 1) driven by 1 and 2 Python threads -> windows/s;
  * tokenizer: CPU per KiB for English prose, code, CJK; tokens per char per script (the
    char-based windowing in the current code assumes 3.04 chars per token).
Writes results/bench_window.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import onnxruntime as ort
import psutil
from f2common import (
    EVIDENCE_DIR,
    ONNX_PATH,
    load_tokenizer,
    make_session,
)

REPO = EVIDENCE_DIR.parents[3]
PROC = psutil.Process(os.getpid())


def rss_mb() -> float:
    return PROC.memory_info().rss / 2**20


def pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, round(q * (len(s) - 1)))]


def timed_runs(sess, ids: np.ndarray, n: int) -> dict[str, float]:
    mask = np.ones_like(ids)
    feed = {"input_ids": ids, "attention_mask": mask}
    for _ in range(3):
        sess.run(["logits"], feed)
    wall: list[float] = []
    cpu: list[float] = []
    for _ in range(n):
        c0 = time.process_time_ns()
        w0 = time.perf_counter_ns()
        sess.run(["logits"], feed)
        wall.append((time.perf_counter_ns() - w0) / 1e6)
        cpu.append((time.process_time_ns() - c0) / 1e6)
    windows = ids.shape[0]
    return {
        "runs": n,
        "batch": windows,
        "seq": int(ids.shape[1]),
        "wall_ms_p50": round(pct(wall, 0.5), 2),
        "wall_ms_p99": round(pct(wall, 0.99), 2),
        "cpu_ms_mean": round(statistics.fmean(cpu), 2),
        "cpu_ms_p50": round(pct(cpu, 0.5), 2),
        "cpu_ms_p99": round(pct(cpu, 0.99), 2),
        "cpu_ms_per_window_mean": round(statistics.fmean(cpu) / windows, 2),
    }


def cold_start() -> dict[str, float]:
    r0 = rss_mb()
    t0 = time.perf_counter()
    c0 = time.process_time()
    data = ONNX_PATH.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    t1 = time.perf_counter()
    r1 = rss_mb()
    opts = ort.SessionOptions()
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    opts.intra_op_num_threads = 1
    opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
    sess = ort.InferenceSession(data, sess_options=opts, providers=["CPUExecutionProvider"])
    t2 = time.perf_counter()
    r2 = rss_mb()
    del data
    r3 = rss_mb()
    ids = np.ones((1, 512), dtype=np.int64)
    sess.run(["logits"], {"input_ids": ids, "attention_mask": ids})
    t3 = time.perf_counter()
    for _ in range(5):
        sess.run(["logits"], {"input_ids": ids, "attention_mask": ids})
    r4 = rss_mb()
    return {
        "read_and_sha256_s": round(t1 - t0, 3),
        "session_create_s": round(t2 - t1, 3),
        "first_512_run_s": round(t3 - t2, 3),
        "cpu_s_total": round(time.process_time() - c0, 3),
        "rss_mb_before": round(r0, 1),
        "rss_mb_after_read": round(r1, 1),
        "rss_mb_after_session": round(r2, 1),
        "rss_mb_after_dropping_bytes": round(r3, 1),
        "rss_mb_after_warm_512": round(r4, 1),
        "rss_mb_session_resident_after_warm": round(r4 - r0, 1),
        "sha256_prefix": digest[:12],
    }


def concurrency(sess, threads: int, seconds: float) -> dict[str, float]:
    ids = np.ones((1, 512), dtype=np.int64)
    feed = {"input_ids": ids, "attention_mask": ids}
    counts = [0] * threads
    stop = time.perf_counter() + seconds

    def worker(k: int) -> None:
        while time.perf_counter() < stop:
            sess.run(["logits"], feed)
            counts[k] += 1

    c0 = time.process_time()
    w0 = time.perf_counter()
    pool = [threading.Thread(target=worker, args=(k,)) for k in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()
    wall = time.perf_counter() - w0
    cpu = time.process_time() - c0
    total = sum(counts)
    return {"python_threads": threads, "windows": total,
            "windows_per_s_wall": round(total / wall, 2),
            "cpu_ms_per_window": round(cpu * 1000 / total, 2),
            "cpu_utilisation": round(cpu / wall, 2)}


def tokenizer_cost(tok) -> dict[str, object]:
    rows = [json.loads(x)["text"] for x in (REPO / "tests/detection_corpus/benign.jsonl")
            .read_text().splitlines() if x.strip()]
    prose = " ".join(rows)
    code = (REPO / "gateway_v2/gateway_v2/detect/semantic/windowing.py").read_text()
    cjk = "注意：请在回答之前仔细阅读以下内容。这是一个关于系统配置的长篇说明，包含多个段落和示例。" * 40
    out: dict[str, object] = {}
    for name, text in (("english_prose", prose), ("python_code", code), ("cjk", cjk)):
        text = (text * (1 + 65536 // max(1, len(text.encode()))))[:65536]
        nbytes = len(text.encode())
        times = []
        for _ in range(7):
            c0 = time.thread_time_ns()
            enc = tok.encode(text, add_special_tokens=False)
            times.append((time.thread_time_ns() - c0) / 1e6)
        ntok = len(enc.ids)
        out[name] = {
            "chars": len(text), "bytes": nbytes, "tokens": ntok,
            "chars_per_token": round(len(text) / ntok, 3),
            "thread_cpu_ms_p50": round(pct(times, 0.5), 2),
            "thread_cpu_us_per_kib": round(pct(times, 0.5) * 1000 / (nbytes / 1024), 1),
            "tokens_in_1557_chars": len(tok.encode(text[:1557], add_special_tokens=False).ids),
        }
    return out


def main() -> int:
    report: dict[str, object] = {
        "host": {"cpus": os.cpu_count(), "loadavg_at_start": os.getloadavg(),
                 "onnxruntime": ort.__version__,
                 "note": "shared 2-vCPU host; other agents' test suites were running"},
    }
    report["cold_start"] = cold_start()
    rng = np.random.default_rng(0)
    single = []
    for threads in (1, 2):
        sess = make_session(threads=threads)
        for seq in (16, 64, 128, 256, 512):
            ids = rng.integers(5, 120000, size=(1, seq), dtype=np.int64)
            ids[0, 0], ids[0, -1] = 1, 2
            n = {16: 200, 64: 200, 128: 150, 256: 100, 512: 80}[seq]
            row = timed_runs(sess, ids, n)
            row["intra_op_threads"] = threads
            single.append(row)
        for batch in (2, 4):
            ids = rng.integers(5, 120000, size=(batch, 512), dtype=np.int64)
            row = timed_runs(sess, ids, 30)
            row["intra_op_threads"] = threads
            single.append(row)
        del sess
    report["per_window"] = single
    sess1 = make_session(threads=1)
    report["concurrency_shared_session_intra1"] = [concurrency(sess1, k, 8.0) for k in (1, 2)]
    report["tokenizer"] = tokenizer_cost(load_tokenizer())
    report["host"]["loadavg_at_end"] = os.getloadavg()
    out = EVIDENCE_DIR / "results" / "bench_window.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
