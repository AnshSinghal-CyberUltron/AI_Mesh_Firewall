"""Build the guard-model manifest and prototype the boot verification (fail closed on mismatch).

  flock /home/claude/work/.heavy.lock nice -n 10 .f2-scratch/venv/bin/python -I manifest.py

1. ``build``: hash every shipped file (ONNX, tokenizer.json, config.json, LICENSE, USE_POLICY.md,
   NOTICE) and the pristine source (safetensors), record versions, IO contract, windowing,
   threshold, and a golden decision set; write results/guard-manifest.json and its canonical
   digest (sha256 of the sorted-key, compact JSON) to results/guard-manifest.sha256.
2. ``verify``: the check the gateway would run at boot. Reads each file ONCE into memory, hashes
   those bytes, and only then builds the session/tokenizer FROM THOSE BYTES (no re-read, so no
   time-of-check/time-of-use gap). The manifest's own digest must equal a digest pinned OUTSIDE the
   manifest (the plan pins it in source). Any mismatch raises; the caller refuses readiness.
3. Golden self-test: the loaded session must reproduce every golden decision and score.
4. Tamper matrix: flipped ONNX byte, edited tokenizer, edited manifest, missing file, wrong pin,
   golden drift -- each must be refused with a specific reason. Timings recorded.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import onnxruntime as ort
import tokenizers
from f2common import (
    EVIDENCE_DIR,
    MODEL_DIR,
    ONNX_PATH,
    OUT_DIR,
    load_tokenizer,
    make_session,
    p_malicious,
    plan_windows,
    run_rows,
    sha256_file,
)

SCHEMA = "amf.guard.manifest/v1"
REPO = EVIDENCE_DIR.parents[3]
NOTICE = ("Llama 4 is licensed under the Llama 4 Community License, Copyright © Meta Platforms, "
          "Inc. All Rights Reserved.\n")
SHIPPED = ("model.onnx", "tokenizer.json", "config.json", "LICENSE", "USE_POLICY.md", "NOTICE")


class ManifestMismatch(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def canonical(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def stage_bundle(dest: Path) -> None:
    """Lay out the bundle exactly as an image would carry it."""
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ONNX_PATH, dest / "model.onnx")
    for name in ("tokenizer.json", "config.json", "LICENSE", "USE_POLICY.md"):
        shutil.copyfile(MODEL_DIR / name, dest / name)
    (dest / "NOTICE").write_text(NOTICE)


def golden_rows(tok, sess) -> list[dict[str, object]]:
    texts = []
    for name, take in (("malicious.jsonl", 16), ("benign.jsonl", 16)):
        lines = [json.loads(x) for x in (REPO / "tests/detection_corpus" / name).read_text()
                 .splitlines() if x.strip()]
        texts += [row["text"] for row in lines if row["split"] == "eval"][:take]
    out = []
    for text in texts:
        plan = plan_windows(tok, text)
        ids = plan.windows[0].ids
        p = float(p_malicious(run_rows(sess, [ids]))[0])
        out.append({"text": text, "ids_sha256": hashlib.sha256(canonical(list(ids))).hexdigest(),
                    "p": round(p, 6)})
    return out


def build(bundle: Path, threshold: float, overlap: int, max_windows: int) -> dict[str, object]:
    tok = load_tokenizer(bundle / "tokenizer.json")
    sess = make_session(bundle / "model.onnx")
    export = json.loads((EVIDENCE_DIR / "results/export.json").read_text())
    parity = json.loads((EVIDENCE_DIR / "results/parity.json").read_text())
    golden = golden_rows(tok, sess)
    (bundle / "golden.json").write_text(json.dumps(golden, ensure_ascii=False, indent=1))
    files = {name: {"sha256": sha256_file(bundle / name), "bytes": (bundle / name).stat().st_size}
             for name in (*SHIPPED, "golden.json")}
    return {
        "schema": SCHEMA,
        "model": {
            "name": "Llama-Prompt-Guard-2-22M",
            "upstream": "huggingface.co/meta-llama/Llama-Prompt-Guard-2-22M",
            "upstream_revision": "11614a155199674a0a95e6602d6ab0417b790ed0",
            "license": "Llama 4 Community License Agreement (see LICENSE, USE_POLICY.md, NOTICE)",
            "attribution": "Built with Llama",
        },
        "files": files,
        "source": {
            "model.safetensors": {"sha256": export["source"]["safetensors_sha256"]},
            "tokenizer.json": {"sha256": export["source"]["tokenizer_sha256"]},
            "config.json": {"sha256": export["source"]["config_sha256"]},
        },
        "export": {k: export[k] for k in ("exporter", "opset", "onnx_ir_version")}
        | {"versions": export["versions"]},
        "runtime": {"onnxruntime": ort.__version__, "tokenizers": tokenizers.__version__,
                    "execution_provider": "CPUExecutionProvider", "dtype": "float32"},
        "io": {"inputs": ["input_ids", "attention_mask"], "output": "logits",
               "positive_index": 1, "max_positions": 512, "cls_id": tok.token_to_id("[CLS]"),
               "sep_id": tok.token_to_id("[SEP]"), "pad_id": 0,
               "tokenizer": "tokenizers.Tokenizer.from_str; truncation and padding disabled"},
        "windowing": {"payload_tokens": 510, "overlap_tokens": overlap,
                      "max_windows": max_windows, "aggregate": "max"},
        "decision": {"threshold": threshold, "category": "prompt_injection"},
        "parity": {"max_abs_logit_diff": parity["max_abs_logit_diff"],
                   "rows": parity["corpus"]["model_rows_compared"],
                   "decision_agreement_0.5": parity["decision_agreement"]["0.5"]},
        "golden": {"file": "golden.json", "rows": len(golden), "p_tolerance": 1e-4},
    }


@dataclass(frozen=True, slots=True)
class Loaded:
    session: object
    tokenizer: object
    manifest: dict[str, object]
    timings_s: dict[str, float]


def verify(bundle: Path, manifest_bytes: bytes, pinned_digest: str) -> Loaded:
    """The boot check. Raises ManifestMismatch(reason) on ANY deviation; never returns partial."""
    t0 = time.perf_counter()
    if hashlib.sha256(manifest_bytes).hexdigest() != pinned_digest:
        raise ManifestMismatch("manifest_digest")
    manifest = json.loads(manifest_bytes)
    if manifest.get("schema") != SCHEMA:
        raise ManifestMismatch("manifest_schema")
    blobs: dict[str, bytes] = {}
    for name, spec in manifest["files"].items():
        path = bundle / name
        if not path.is_file():
            raise ManifestMismatch(f"missing:{name}")
        data = path.read_bytes()  # read ONCE; everything below uses these bytes
        if len(data) != spec["bytes"] or hashlib.sha256(data).hexdigest() != spec["sha256"]:
            raise ManifestMismatch(f"sha256:{name}")
        blobs[name] = data
    t_hash = time.perf_counter()
    if ort.__version__ != manifest["runtime"]["onnxruntime"]:
        raise ManifestMismatch("onnxruntime_version")
    from tokenizers import Tokenizer

    tok = Tokenizer.from_str(blobs["tokenizer.json"].decode("utf-8"))
    tok.no_truncation()
    tok.no_padding()
    opts = ort.SessionOptions()
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    opts.intra_op_num_threads = 1
    opts.inter_op_num_threads = 1
    opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
    sess = ort.InferenceSession(blobs["model.onnx"], sess_options=opts,
                                providers=["CPUExecutionProvider"])
    sess.disable_fallback()
    if sess.get_providers()[0] != "CPUExecutionProvider":
        raise ManifestMismatch("execution_provider")
    sess._model_bytes = None  # onnxruntime keeps the bytes for fallback; fallback is disabled
    del blobs["model.onnx"]
    t_session = time.perf_counter()
    golden = json.loads(blobs["golden.json"])
    tol = manifest["golden"]["p_tolerance"]
    thr = manifest["decision"]["threshold"]
    for row in golden:
        enc = tok.encode(row["text"], add_special_tokens=True).ids
        if hashlib.sha256(canonical(list(enc))).hexdigest() != row["ids_sha256"]:
            raise ManifestMismatch("golden_tokenizer")
        p = float(p_malicious(run_rows(sess, [enc]))[0])
        if abs(p - row["p"]) > tol or (p >= thr) != (row["p"] >= thr):
            raise ManifestMismatch("golden_decision")
    t_golden = time.perf_counter()
    return Loaded(sess, tok, manifest, {
        "hash_files": round(t_hash - t0, 3),
        "session_from_bytes": round(t_session - t_hash, 3),
        "golden_self_test": round(t_golden - t_session, 3),
        "total": round(t_golden - t0, 3),
    })


def expect_refusal(label: str, fn) -> dict[str, object]:
    t0 = time.perf_counter()
    try:
        fn()
    except ManifestMismatch as exc:
        return {"case": label, "refused": True, "reason": exc.reason,
                "seconds": round(time.perf_counter() - t0, 3)}
    return {"case": label, "refused": False}


def main() -> int:
    summary_path = EVIDENCE_DIR / "results" / "eval_summary.json"
    threshold = 0.5
    if summary_path.exists():
        threshold = json.loads(summary_path.read_text())["chosen_threshold"]
    bundle = OUT_DIR / "bundle"
    if bundle.exists():
        shutil.rmtree(bundle)
    stage_bundle(bundle)
    manifest = build(bundle, threshold=threshold, overlap=64, max_windows=32)
    m_bytes = canonical(manifest)
    digest = hashlib.sha256(m_bytes).hexdigest()
    (EVIDENCE_DIR / "results" / "guard-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    (EVIDENCE_DIR / "results" / "guard-manifest.sha256").write_text(digest + "\n")
    (bundle / "manifest.json").write_bytes(m_bytes)

    ok = verify(bundle, m_bytes, digest)
    cases = []

    def tampered(name: str, mutate) -> Path:
        copy = OUT_DIR / f"bundle-{name}"
        if copy.exists():
            shutil.rmtree(copy)
        shutil.copytree(bundle, copy)
        mutate(copy)
        return copy

    def flip_onnx(root: Path) -> None:
        data = bytearray((root / "model.onnx").read_bytes())
        data[len(data) // 2] ^= 0x01
        (root / "model.onnx").write_bytes(bytes(data))

    def edit_tokenizer(root: Path) -> None:
        text = (root / "tokenizer.json").read_text()
        (root / "tokenizer.json").write_text(text.replace('"Strip"', '"Strip" ', 1))

    b1 = tampered("onnx", flip_onnx)
    cases.append(expect_refusal("onnx_one_bit_flipped", lambda: verify(b1, m_bytes, digest)))
    b2 = tampered("tok", edit_tokenizer)
    cases.append(expect_refusal("tokenizer_one_byte_added", lambda: verify(b2, m_bytes, digest)))
    b3 = tampered("missing", lambda r: (r / "LICENSE").unlink())
    cases.append(expect_refusal("license_file_missing", lambda: verify(b3, m_bytes, digest)))
    edited = dict(manifest)
    edited["decision"] = {"threshold": 0.01, "category": "prompt_injection"}
    cases.append(expect_refusal("manifest_threshold_edited",
                                lambda: verify(bundle, canonical(edited), digest)))
    cases.append(expect_refusal("wrong_pinned_digest", lambda: verify(bundle, m_bytes, "0" * 64)))
    # A self-consistent forgery: attacker swaps the model AND rewrites the manifest to match it.
    # Only the pin held outside the manifest stops it.
    forged = json.loads(json.dumps(manifest))
    forged["files"]["model.onnx"]["sha256"] = sha256_file(b1 / "model.onnx")
    cases.append(expect_refusal("model_swapped_and_manifest_rewritten",
                                lambda: verify(b1, canonical(forged), digest)))
    # Golden drift: pinned manifest is honest but the golden file promises other scores.
    def drift_golden(root: Path) -> None:
        rows = json.loads((root / "golden.json").read_text())
        rows[0]["p"] = round(1.0 - rows[0]["p"], 6)
        (root / "golden.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    b4 = tampered("golden", drift_golden)
    g_manifest = json.loads(json.dumps(manifest))
    g_manifest["files"]["golden.json"] = {"sha256": sha256_file(b4 / "golden.json"),
                                          "bytes": (b4 / "golden.json").stat().st_size}
    g_bytes = canonical(g_manifest)
    cases.append(expect_refusal("golden_scores_disagree_with_model",
                                lambda: verify(b4, g_bytes, hashlib.sha256(g_bytes).hexdigest())))
    for path in (b1, b2, b3, b4):
        shutil.rmtree(path)

    report = {
        "manifest_digest": digest,
        "threshold_recorded": threshold,
        "verify_ok_timings_s": ok.timings_s,
        "tamper_cases": cases,
        "all_tamper_cases_refused": all(c["refused"] for c in cases),
    }
    (EVIDENCE_DIR / "results" / "manifest_verify.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["all_tamper_cases_refused"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
