"""Weight-free latency proxy for PG2-22M (F2 plan §4.7). Needs NO gated download and NO token.

Latency of dense fp32/int8 kernels does not depend on the weight VALUES, only on the graph and
the shapes. This script therefore builds ``DebertaV2ForSequenceClassification`` with the
DeBERTa-v3-xsmall architecture (the base of Llama Prompt Guard 2 22M) from RANDOM weights
(``torch.manual_seed(0)``), and refuses to continue unless its parameter count equals the real
model's 70,830,722 (``results/export.json``). It exports with exactly the arguments of
``export_onnx.py`` and measures, per call at batch 1 and intra-op 1:

* ``fp32_dynamic``   - the same graph shape as the real export (dynamic batch and sequence axes);
* ``fp32_static_S``  - exported with a fixed ``[1, S]`` shape, so shape glue folds at export;
* ``int8_dynamic``   - ``onnxruntime.quantization.quantize_dynamic`` (QInt8 weights) of the fp32
  graph, the "PG2-22M INT8 ONNX" of issue #98.

Per call it records THREAD CPU time (``time.thread_time_ns``: with intra-op 1 onnxruntime runs
every kernel on the calling thread) and wall time, and reports p50/p99. It also records node
counts of the ORT-optimised graphs. Accuracy is NOT measured here (random weights): the INT8
decision-flip rate and L36-2 need the real bytes (plan §4.6, pending).

Run (outside the repo for the ONNX files; ~2 min CPU, so under the heavy lock):
  flock /home/claude/work/.heavy.lock nice -n 10 <venv>/bin/python -I \
    docs/plans/evidence/2026-11-11-f2-guard-model/scripts/proxy_latency.py
Env: ``F2_PROXY_OUT`` (default ``/home/claude/work/runs/f2-author/out``) receives the ONNX files.
"""

from __future__ import annotations

import json
import os
import platform
import statistics
import time
import warnings
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
import torch
import transformers
from onnxruntime.quantization import QuantType, quantize_dynamic
from transformers import DebertaV2Config, DebertaV2ForSequenceClassification

EVIDENCE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = Path(os.environ.get("F2_PROXY_OUT", "/home/claude/work/runs/f2-author/out"))
REAL_PARAMETERS = 70_830_722  # results/export.json source.num_parameters (the real PG2-22M)
OPSET = 17
SEQS = (16, 64, 128, 256, 512)
RUNS = {16: 120, 64: 80, 128: 50, 256: 25, 512: 15}
WARMUP = 5
STATIC = (16, 64)


def config() -> DebertaV2Config:
    """DeBERTa-v3-xsmall, as published by Microsoft (MIT), with the 2-label head of PG2."""
    return DebertaV2Config(
        vocab_size=128100,
        hidden_size=384,
        num_hidden_layers=12,
        num_attention_heads=6,
        intermediate_size=1536,
        hidden_act="gelu",
        hidden_dropout_prob=0.1,
        attention_probs_dropout_prob=0.1,
        max_position_embeddings=512,
        type_vocab_size=0,
        initializer_range=0.02,
        layer_norm_eps=1e-7,
        relative_attention=True,
        max_relative_positions=-1,
        position_buckets=256,
        norm_rel_ebd="layer_norm",
        share_att_key=True,
        pos_att_type="p2c|c2p",
        position_biased_input=False,
        pad_token_id=0,
        num_labels=2,
    )


def export(model: torch.nn.Module, path: Path, seq: int, dynamic: bool) -> float:
    ids = torch.randint(3, 128000, (1, seq), generator=torch.Generator().manual_seed(seq))
    mask = torch.ones_like(ids)
    axes = {
        "input_ids": {0: "batch", 1: "seq"},
        "attention_mask": {0: "batch", 1: "seq"},
        "logits": {0: "batch"},
    }
    t0 = time.perf_counter()
    with torch.no_grad(), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        torch.onnx.export(
            model,
            (ids, mask),
            str(path),
            input_names=["input_ids", "attention_mask"],
            output_names=["logits"],
            dynamic_axes=axes if dynamic else None,
            opset_version=OPSET,
            do_constant_folding=True,
            dynamo=False,
        )
    return round(time.perf_counter() - t0, 2)


def histogram(path: Path) -> dict[str, int]:
    proto = onnx.load(str(path), load_external_data=False)
    ops: dict[str, int] = {}
    for node in proto.graph.node:
        ops[node.op_type] = ops.get(node.op_type, 0) + 1
    return ops


def session(path: Path, optimized_to: Path | None = None) -> ort.InferenceSession:
    """Same options as f2common.make_session (intra 1, inter 1, sequential, ENABLE_ALL)."""
    opts = ort.SessionOptions()
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    opts.intra_op_num_threads = 1
    opts.inter_op_num_threads = 1
    opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
    opts.add_session_config_entry("session.inter_op.allow_spinning", "0")
    if optimized_to is not None:
        opts.optimized_model_filepath = str(optimized_to)
    sess = ort.InferenceSession(str(path), sess_options=opts, providers=["CPUExecutionProvider"])
    sess.disable_fallback()
    return sess


def pct(xs: list[float], q: float) -> float:
    ordered = sorted(xs)
    return round(ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))], 2)


def bench(sess: ort.InferenceSession, seq: int) -> dict[str, float]:
    rng = np.random.default_rng(seq)
    ids = rng.integers(3, 128000, size=(1, seq), dtype=np.int64)
    feed = {"input_ids": ids, "attention_mask": np.ones_like(ids)}
    for _ in range(WARMUP):
        sess.run(None, feed)
    cpu: list[float] = []
    wall: list[float] = []
    for _ in range(RUNS[seq]):
        c0 = time.thread_time_ns()
        w0 = time.perf_counter_ns()
        sess.run(None, feed)
        wall.append((time.perf_counter_ns() - w0) / 1e6)
        cpu.append((time.thread_time_ns() - c0) / 1e6)
    return {
        "seq": seq,
        "runs": RUNS[seq],
        "thread_cpu_ms_p50": pct(cpu, 0.5),
        "thread_cpu_ms_p99": pct(cpu, 0.99),
        "thread_cpu_ms_mean": round(statistics.fmean(cpu), 2),
        "wall_ms_p50": pct(wall, 0.5),
        "wall_ms_p99": pct(wall, 0.99),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(0)
    torch.set_num_threads(1)
    model = DebertaV2ForSequenceClassification(config()).eval()
    params = sum(p.numel() for p in model.parameters())
    if params != REAL_PARAMETERS:
        raise SystemExit(f"architecture mismatch: {params} parameters != {REAL_PARAMETERS}")

    report: dict[str, object] = {
        "what": "weight-free latency proxy: identical architecture, random weights",
        "parameters": params,
        "parameters_match_real_export": True,
        "host": {
            "cpus": os.cpu_count(),
            "cpu": platform.processor() or platform.machine(),
            "loadavg_at_start": [round(x, 2) for x in os.getloadavg()],
        },
        "versions": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "onnx": onnx.__version__,
            "onnxruntime": ort.__version__,
            "numpy": np.__version__,
        },
        "timing": "batch 1, intra-op 1, inter-op 1, sequential, ORT_ENABLE_ALL, no spinning; "
        "thread CPU = time.thread_time_ns of the calling thread",
        "variants": {},
    }
    variants: dict[str, dict[str, object]] = {}

    fp32 = OUT_DIR / "proxy_fp32_dynamic.onnx"
    export_s = export(model, fp32, 16, dynamic=True)
    int8 = OUT_DIR / "proxy_int8_dynamic.onnx"
    t0 = time.perf_counter()
    quantize_dynamic(str(fp32), str(int8), weight_type=QuantType.QInt8)
    quant_s = round(time.perf_counter() - t0, 2)

    plan: list[tuple[str, Path, tuple[int, ...], dict[str, object]]] = [
        ("fp32_dynamic", fp32, SEQS, {"export_s": export_s}),
        ("int8_dynamic", int8, SEQS, {"quantize_s": quant_s, "weight_type": "QInt8"}),
    ]
    for seq in STATIC:
        path = OUT_DIR / f"proxy_fp32_static_{seq}.onnx"
        plan.append((f"fp32_static_{seq}", path, (seq,), {"export_s": export(model, path, seq, False)}))

    for name, path, seqs, extra in plan:
        optimized = OUT_DIR / f"{name}.optimized.onnx"
        sess = session(path, optimized_to=optimized)
        opt_ops = histogram(optimized)
        variants[name] = {
            **extra,
            "bytes": path.stat().st_size,
            "exported_nodes": sum(histogram(path).values()),
            "optimized_nodes": sum(opt_ops.values()),
            "optimized_matmul_like": {
                k: v for k, v in sorted(opt_ops.items())
                if "MatMul" in k or k in ("Gemm", "FusedMatMul")
            },
            "per_window": [bench(sess, seq) for seq in seqs],
        }
        del sess

    report["variants"] = variants
    report["host"]["loadavg_at_end"] = [round(x, 2) for x in os.getloadavg()]  # type: ignore[index]
    out = EVIDENCE_DIR / "results" / "proxy_latency.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
