#!/usr/bin/env python3
"""ORT-CPU parity of arbitrary ONNX files vs the PyTorch fp32 reference (ref_scores.json), unpadded and padded.
usage: cpu_parity.py <tok_dir> <corpus> <ref_scores.json> <out.json> <label=path.onnx> [...]"""
import json
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg2common as C  # noqa: E402

tok_dir, corpus, ref_path, out = sys.argv[1:5]
tok = C.load_tokenizer(tok_dir)
rows = C.load_corpus(corpus)
ref = json.load(open(ref_path))
res = {}
for spec in sys.argv[5:]:
    label, path = spec.split("=", 1)
    so = ort.SessionOptions()
    so.intra_op_num_threads = 8
    s = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
    for mode in ("unpadded", "pad512"):
        dp, agree, flips = [], 0, []
        for r in rows:
            ids, mask = C.batch_from_windows([C.content_ids(tok, r["text"])], pad_to="longest" if mode == "unpadded" else None)
            lg = s.run(None, {"input_ids": ids, "attention_mask": mask})[0][0]
            p = float(C.softmax_p1(lg[None])[0])
            rp = ref[r["id"]]["p1"]
            dp.append(abs(p - rp))
            ok = (p >= 0.5) == (rp >= 0.5)
            agree += ok
            if not ok:
                flips.append({"id": r["id"], "label": r["label"], "ref_p1": rp, "p1": p})
        res[f"{label}_{mode}"] = {"path": path, "sha256": C.sha256_file(path), "n": len(rows), "agree_pct": round(100 * agree / len(rows), 3),
                                  "max_abs_dp": max(dp), "mean_abs_dp": float(np.mean(dp)), "flips": flips}
        print(label, mode, res[f"{label}_{mode}"]["agree_pct"], round(max(dp), 4), len(flips), flush=True)
Path(out).write_text(json.dumps(res, indent=1))
