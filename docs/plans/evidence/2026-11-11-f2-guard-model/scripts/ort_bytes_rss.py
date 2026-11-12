"""RSS cost of building the session from VERIFIED BYTES (no TOCTOU) vs from the file path.

  .f2-scratch/venv/bin/python -I ort_bytes_rss.py   (each mode runs in a fresh child process)

onnxruntime's Python wrapper keeps the bytes it was given (``self._model_bytes``, marked "TODO:
This is bad as we're holding the memory indefinitely" in onnxruntime_inference_collection.py) so it
can re-create the session for provider fallback. With ``disable_fallback()`` that copy is never
used again. Writes results/ort_bytes_rss.json.
"""

from __future__ import annotations

import gc
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from f2common import EVIDENCE_DIR, ONNX_PATH


def child(mode: str) -> dict[str, object]:
    import hashlib

    import numpy as np
    import onnxruntime as ort
    import psutil

    proc = psutil.Process()

    def rss() -> float:
        return round(proc.memory_info().rss / 2**20, 1)

    base = rss()
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 1
    if mode == "path":
        sess = ort.InferenceSession(str(ONNX_PATH), sess_options=opts,
                                    providers=["CPUExecutionProvider"])
    else:
        data = ONNX_PATH.read_bytes()
        hashlib.sha256(data).hexdigest()
        sess = ort.InferenceSession(data, sess_options=opts, providers=["CPUExecutionProvider"])
        del data
        if mode == "bytes_released":
            sess.disable_fallback()
            sess._model_bytes = None  # the retained copy; only provider fallback reads it
    gc.collect()
    ids = np.ones((1, 512), dtype=np.int64)
    sess.run(["logits"], {"input_ids": ids, "attention_mask": ids})
    return {"mode": mode, "rss_mb_before": base, "rss_mb_after_warm_512": rss(),
            "delta_mb": round(rss() - base, 1)}


def main() -> int:
    if len(sys.argv) > 1:
        print(json.dumps(child(sys.argv[1])))
        return 0
    out = []
    for mode in ("path", "bytes", "bytes_released"):
        res = subprocess.run([sys.executable, "-I", __file__, mode], capture_output=True, text=True,
                             check=True)
        out.append(json.loads(res.stdout.strip().splitlines()[-1]))
    (EVIDENCE_DIR / "results" / "ort_bytes_rss.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
