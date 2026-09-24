"""ONNX Runtime session factory for the guard benchmark.

Backends
  trt_fp16_static : TensorRT EP, fp16, one optimization profile pinned to exactly (W, seq)
  trt_fp16_dyn    : TensorRT EP, fp16, one dynamic profile min 1x16 / opt <opt>x512 / max <max>x512
  trt_fp16_multi  : TensorRT EP, fp16, 7 profiles (batch bands 1|2|3-4|5-8|9-16|17-32|33-64, seq 512)
  trt_fp32_dyn    : TensorRT EP, fp32 (TF32 allowed) dynamic profile (parity reference on GPU)
  cuda_fp32       : CUDA EP on model.fp32.onnx
  cuda_fp16       : CUDA EP on model.fp16.onnx (constants clipped to fp16 range at conversion)
  cpu_fp32 / cpu_int8 : CPU EP (intra-op threads configurable)
Every session gets intra-op threads = 1 and spinning off on GPU backends so N-process runs do not
burn CPU in idle ORT worker threads.
"""
from __future__ import annotations

import os
import time
from pathlib import Path


# (min, opt, max) batch per optimization profile for trt_fp16_multi (seq fixed at 512)
MULTI_BANDS = [(1, 1, 1), (2, 2, 2), (3, 3, 4), (5, 7, 8), (9, 16, 16), (17, 32, 32), (33, 64, 64)]


def make_session(model_dir, backend, *, W=None, seq=512, opt_batch=16, max_batch=64,
                 cache_root=None, threads=1, builder_level=3, cuda_graph=False, profile_prefix=None):
    import onnxruntime as ort

    if backend == "trt_fp16_serial1":
        t0 = time.perf_counter()
        ss = Serial1Session(model_dir, cache_root=cache_root, builder_level=builder_level)
        return ss, {"backend": backend, "create_s": round(time.perf_counter() - t0, 3), "engine": ss.info.get("engine_cache_dir"),
                    "active_providers": ss.get_providers(), "ort_version": ort.__version__}
    if backend == "trt_fp16_bucketed":
        t0 = time.perf_counter()
        bs = BucketedSession(model_dir, max_batch=max_batch, cache_root=cache_root, builder_level=builder_level)
        return bs, {"backend": backend, "buckets": bs.buckets, "create_s": round(time.perf_counter() - t0, 3),
                    "engines": {b: i.get("engine_cache_dir") for b, i in bs.info.items()},
                    "active_providers": bs.get_providers(), "ort_version": ort.__version__}

    model_dir = Path(model_dir)
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.log_severity_level = 3
    info = {"backend": backend}
    if profile_prefix:
        so.enable_profiling = True
        so.profile_file_prefix = profile_prefix
    if backend.startswith("cpu"):
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        path = model_dir / ("model.int8.onnx" if backend == "cpu_int8" else "model.fp32.onnx")
        if backend == "cpu_thirdparty_int8":
            path = model_dir / "model.quant.onnx"
        if backend == "cpu_thirdparty_fp32":
            path = model_dir / "model.onnx"
        providers = ["CPUExecutionProvider"]
    else:
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        so.add_session_config_entry("session.intra_op.allow_spinning", "0")
        so.add_session_config_entry("session.inter_op.allow_spinning", "0")
        if backend.startswith("trt"):
            path = model_dir / "model.fp32.onnx"
            cache_root = Path(cache_root or (Path.home() / "gb" / "trtcache"))
            if backend == "trt_fp16_static":
                shp = f"input_ids:{W}x{seq},attention_mask:{W}x{seq}"
                mn = op = mx = shp
                sub = f"static_{W}x{seq}"
            elif backend == "trt_fp16_multi":
                # one optimization profile per batch band so small batches get kernels tuned for their shape
                bands = [b for b in MULTI_BANDS if b[0] <= max_batch]
                f = lambda b: f"input_ids:{b}x512,attention_mask:{b}x512"
                mn = ",".join(f(b[0]) for b in bands)
                op = ",".join(f(b[1]) for b in bands)
                mx = ",".join(f(min(b[2], max_batch)) for b in bands)
                sub = f"multi_max{max_batch}"
            else:
                mn = "input_ids:1x16,attention_mask:1x16"
                op = f"input_ids:{opt_batch}x512,attention_mask:{opt_batch}x512"
                mx = f"input_ids:{max_batch}x512,attention_mask:{max_batch}x512"
                sub = f"dyn_opt{opt_batch}_max{max_batch}"
            fp16 = backend != "trt_fp32_dyn"
            cdir = cache_root / model_dir.name / (sub + ("_fp16" if fp16 else "_fp32") + f"_L{builder_level}")
            cdir.mkdir(parents=True, exist_ok=True)
            trt = {
                "trt_fp16_enable": fp16,
                "trt_engine_cache_enable": True,
                "trt_engine_cache_path": str(cdir),
                "trt_timing_cache_enable": True,
                "trt_timing_cache_path": str(cdir),
                "trt_max_workspace_size": str(4 << 30),
                "trt_builder_optimization_level": builder_level,
                "trt_profile_min_shapes": mn,
                "trt_profile_opt_shapes": op,
                "trt_profile_max_shapes": mx,
                "trt_cuda_graph_enable": cuda_graph,
            }
            providers = [("TensorrtExecutionProvider", trt), ("CUDAExecutionProvider", {}), "CPUExecutionProvider"]
            info.update({"trt_options": {k: str(v) for k, v in trt.items()}, "engine_cache_dir": str(cdir),
                         "engine_files_before": sorted(os.listdir(cdir))})
        else:
            path = model_dir / ("model.fp16.onnx" if backend == "cuda_fp16" else "model.fp32.onnx")
            providers = [("CUDAExecutionProvider", {"cudnn_conv_algo_search": "EXHAUSTIVE"}), "CPUExecutionProvider"]
    t0 = time.perf_counter()
    sess = ort.InferenceSession(str(path), so, providers=providers)
    if not backend.startswith("cpu"):
        # ORT's Python wrapper otherwise silently re-creates the session on CUDA EP after a TensorRT error
        sess.disable_fallback()
    info["create_s"] = round(time.perf_counter() - t0, 4)
    info["model_path"] = str(path)
    info["active_providers"] = sess.get_providers()
    try:
        po = sess.get_provider_options()
        info["provider_options"] = {k: {kk: str(vv) for kk, vv in v.items()} for k, v in po.items()}
    except Exception:
        pass
    info["ort_version"] = ort.__version__
    return sess, info


def assert_backend(sess, backend):
    """Refuse to report numbers if the requested EP is not first in the active provider list."""
    want = ("TensorrtExecutionProvider" if backend.startswith("trt") else
            "CUDAExecutionProvider" if backend.startswith("cuda") else "CPUExecutionProvider")
    act = sess.get_providers()
    if act[0] != want:
        raise RuntimeError(f"backend {backend}: wanted {want} first, active={act}")


def placement(model_dir, backend, feeds, **kw):
    """Run once with ORT profiling on and count kernel events per execution provider.

    A TensorRT subgraph shows up as one fused *TRTKernel* node; any node that fell back to
    CUDA/CPU EP shows up individually. Returns {provider: {"events": n, "dur_us": total, "ops": {op: n}}}.
    """
    import json
    import tempfile

    tmp = tempfile.mkdtemp()
    sess, info = make_session(model_dir, backend, profile_prefix=str(Path(tmp) / "prof"), **kw)
    for _ in range(3):
        sess.run(None, feeds)
    prof = sess.end_profiling()
    ev = json.load(open(prof))
    out = {}
    for e in ev:
        if e.get("cat") != "Node":
            continue
        a = e.get("args", {})
        prov = a.get("provider")
        if not prov:
            continue
        d = out.setdefault(prov, {"events": 0, "dur_us": 0, "ops": {}})
        d["events"] += 1
        d["dur_us"] += int(e.get("dur", 0))
        op = a.get("op_name", "?")
        d["ops"][op] = d["ops"].get(op, 0) + 1
    return out


BUCKETS = [1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 24, 32, 48, 64]


class BucketedSession:
    """trt_fp16_bucketed: one static-shape TensorRT engine per batch bucket (seq 512).

    ORT's TensorRT EP does not switch between optimization profiles by input shape (a 7-profile engine
    fails with enqueueV3 'API Usage Error' for any batch outside profile 0), so exact-shape engines are
    held side by side and a batch of n windows runs on the smallest bucket >= n, padded with a
    2-token [CLS][SEP] row (never an all-masked row) and sliced back to n rows.
    """

    def __init__(self, model_dir, max_batch=64, **kw):
        import numpy as np
        self.buckets = [b for b in BUCKETS if b <= max_batch]
        self.sess, self.info = {}, {}
        for b in self.buckets:
            s, i = make_session(model_dir, "trt_fp16_static", W=b, seq=512, **kw)
            assert_backend(s, "trt_fp16_static")
            self.sess[b], self.info[b] = s, i
        self.pad_ids = np.zeros((max(self.buckets), 512), np.int64)
        self.pad_ids[:, 0], self.pad_ids[:, 1] = 1, 2
        self.pad_mask = np.zeros((max(self.buckets), 512), np.int64)
        self.pad_mask[:, :2] = 1

    def run(self, outs, feeds):
        import numpy as np
        ids, mask = feeds["input_ids"], feeds["attention_mask"]
        n = ids.shape[0]
        b = next(x for x in self.buckets if x >= n)
        if b != n:
            ids = np.concatenate([ids, self.pad_ids[: b - n]])
            mask = np.concatenate([mask, self.pad_mask[: b - n]])
        return [self.sess[b].run(outs, {"input_ids": ids, "attention_mask": mask})[0][:n]]

    def get_providers(self):
        return self.sess[self.buckets[0]].get_providers()


class Serial1Session:
    """trt_fp16_serial1: every window runs as its own batch-1 call on the exact-shape 1x512 TensorRT engine.

    Measured on L4 (22M, closed loop): batch-1 = 2.15 ms/window (466 windows/s) vs batch-2 = 4.93 ms (405 w/s),
    batch-8 = 21.0 ms (380 w/s) -- larger batches are LESS efficient on this power-capped (72 W) GPU, so splitting
    a W-window request into W sequential batch-1 calls is both faster per request and higher throughput.
    """

    def __init__(self, model_dir, **kw):
        self.sess, self.info = make_session(model_dir, "trt_fp16_static", W=1, seq=512, **kw)
        assert_backend(self.sess, "trt_fp16_static")

    def run(self, outs, feeds):
        import numpy as np
        ids, mask = feeds["input_ids"], feeds["attention_mask"]
        res = [self.sess.run(outs, {"input_ids": ids[i:i + 1], "attention_mask": mask[i:i + 1]})[0]
               for i in range(ids.shape[0])]
        return [np.concatenate(res)]

    def get_providers(self):
        return self.sess.get_providers()
