#!/usr/bin/env python3
"""Build model.fp16.onnx (for the CUDA EP fp16 backend) from model.fp32.onnx and validate it on CUDA EP.

Tries, in order: ORT's transformers float16 converter, then onnxconverter-common with Cast blocked
(the plain onnxconverter-common conversion produces an invalid Cast on DeBERTa's embedding mask).
Constants are clipped to the fp16 range so attention masking stays finite.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg2common as C  # noqa: E402


def try_convert(src, dst, method):
    import onnx
    m = onnx.load(str(src))
    if method == "ort_transformers_float16":
        from onnxruntime.transformers.float16 import convert_float_to_float16
        m16 = convert_float_to_float16(m, keep_io_types=True)
    else:
        from onnxconverter_common import float16
        m16 = float16.convert_float_to_float16(m, keep_io_types=True,
                                               op_block_list=list(float16.DEFAULT_OP_BLOCK_LIST) + ["Cast"])
    onnx.save(m16, str(dst))


def main():
    onnx_dir, tok_dir, corpus = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    import onnxruntime as ort
    tok = C.load_tokenizer(tok_dir)
    rows = C.load_corpus(corpus)[:: 25]
    ref = json.load(open(onnx_dir / "ref_scores.json"))
    rep = {"attempts": []}
    for method in ("ort_transformers_float16", "occ_float16_cast_blocked"):
        dst = onnx_dir / "model.fp16.onnx"
        try:
            try_convert(onnx_dir / "model.fp32.onnx", dst, method)
            s = ort.InferenceSession(str(dst), providers=["CUDAExecutionProvider"])
            dps, nonfinite = [], 0
            for r in rows:
                ids, mask = C.batch_from_windows([C.content_ids(tok, r["text"])])
                lg = s.run(None, {"input_ids": ids, "attention_mask": mask})[0]
                if not np.all(np.isfinite(lg)):
                    nonfinite += 1
                    continue
                dps.append(abs(float(C.softmax_p1(lg)[0]) - ref[r["id"]]["p1"]))
            ok = nonfinite == 0 and len(dps) == len(rows)
            rep["attempts"].append({"method": method, "ok": ok, "n": len(rows), "nonfinite": nonfinite,
                                    "max_abs_dp_vs_torch_fp32": max(dps) if dps else None,
                                    "providers": s.get_providers()})
            if ok:
                rep["chosen"] = method
                rep["sha256"] = C.sha256_file(dst)
                break
        except Exception as e:
            rep["attempts"].append({"method": method, "ok": False, "error": repr(e)[:1500]})
    (onnx_dir / "fp16_report.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()
