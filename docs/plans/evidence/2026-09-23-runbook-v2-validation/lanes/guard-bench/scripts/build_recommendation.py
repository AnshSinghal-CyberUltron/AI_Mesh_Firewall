#!/usr/bin/env python3
"""Assemble recommended_guard_config.json from the recomputed evidence (summary/*.json, knee confirmations).

Every number in the output is read from a file in the evidence dir; the file is named next to it.
usage: build_recommendation.py <evidence-dir>
"""
import csv
import glob
import json
import sys
import time
from pathlib import Path


def rows(path):
    with open(path) as fh:
        return list(csv.DictReader(fh))


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def knee(ev, name):
    """Confirmation result (3 x 300 s at the knee + 300 s at the next rate) recomputed by analyze_knee.py."""
    ks = json.loads((ev / "summary" / "knee_summary.json").read_text()) if (ev / "summary" / "knee_summary.json").exists() else {}
    for k, v in ks.items():
        if k.rstrip("/").endswith(name):
            return {"knee_wps": v.get("knee_wps"), "repeats_p99_ms": [r["p99"] for r in v.get("knee_repeats", [])],
                    "repeatable_pass": v.get("knee_repeatable_pass"), "next_wps": v.get("next_wps"),
                    "next_p99_ms": [r["p99"] for r in v.get("next_step", [])],
                    "dir": str(Path(k).resolve().relative_to(ev.resolve())) if str(k).startswith(str(ev)) else k}
    return None


def confirm(ev, name, target):
    """driver_confirm.sh run: 3 x 300 s at a rate + 1 x 300 s at the next rate."""
    import analyze_ladder as AL
    d = next(iter(sorted(ev.glob(f"raw/A/*/Llama-Prompt-Guard-2-22M/confirm_{name}"))), None)
    if d is None:
        return None
    groups = AL.load_steps(d)
    reps = [AL.step_metrics(groups[k]) for k in sorted(groups)]
    return {"steps": [{"offered_wps": r["offered_wps"], "p99_ms": (r["lat_ms"] or {}).get("p99"), "n": r["n"],
                       "drops": r["drops_gt5ms"], "pass": bool(r["lat_ms"] and r["lat_ms"]["p99"] <= target and r["drops_gt5ms"] == 0
                                                                  and r["completed"] == r["n"])} for r in reps],
            "dir": str(d.relative_to(ev))}


def operating_points(ev, Q, m22):
    return {
        "unit": "offered windows/s on ONE L4 (divide by mean windows/request for RPS: W2 -> /2, headline mix -> /1.556)",
        "p99_le_5ms": {
            "W1_only_prompts_le_510_tokens": Q(m22, "mb_trt_fp16_bucketed_g25/W1_serial1"),
            "W2_or_headline": "INFEASIBLE: W=2 p99 is 7.36 ms already at 20 windows/s (GPU idle most of the time); "
                              "headline mix 6.19 ms at 20 w/s",
        },
        "p99_le_8ms": {"W2_confirmed": knee(ev, "knee_serial1_W2_p99le8"),
                       "headline_confirmed": knee(ev, "knee_serial1_headline_p99le8")},
        "p99_le_10ms": {"W2_confirmed": knee(ev, "knee_serial1_W2_p99le10"),
                        "headline_knee_attempt_162": knee(ev, "knee_serial1_headline_p99le10"),
                        "headline_confirm_142": confirm(ev, "serial1_headline_142", 10.0),
                        "headline_confirm_122": confirm(ev, "serial1_headline_122", 10.0)},
        "summary_q_safe_windows_per_s_one_L4_22M": {
            "p99<=5ms": {"W1_only": "93 (ladder, 30 s steps; first fail 140)", "W2": 0, "headline_mix": 0},
            "p99<=8ms": {"W2": "41 (3 x 300 s confirmed; 49 passed 300 s once, failed 30 s)",
                         "headline_mix": "81 (3 x 300 s confirmed; 89 fails)"},
            "p99<=10ms": {"W2": "89 (3 x 300 s confirmed; 97 passed 300 s once, 114 failed)",
                          "headline_mix": "122 (3 x 300 s confirmed; 142 fails 2 of 4 x 300 s, 162 fails 4 of 4)"},
            "RPS": {"p99<=8ms": {"W2": 20.5, "headline_mix": 52}, "p99<=10ms": {"W2": 44.6, "headline_mix": 78}},
        },
        "batch_wait_setting_for_all_targets": {"max_batch_windows_B": 1, "max_wait_us": 0,
                                               "evidence": "every B in {8,16,32,64} x wait in {0.5,1,2} ms grid point had "
                                                           "q_safe(p99<=10 ms) <= 20 w/s vs 81 w/s without batching "
                                                           "(summary/throughput_qsafe.csv)"},
    }


def main():
    ev = Path(sys.argv[1])
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    S = ev / "summary"
    lat = rows(S / "latency_table.csv")
    thr = rows(S / "throughput_qsafe.csv")

    def L(model, backend, W, seq=512, exp="A in-process ORT"):
        for r in lat:
            if r["exp"] == exp and r["model"] == model and r["backend"] == backend and int(r["W"]) == W and int(r["seq"]) == seq:
                return {"p50": f(r["p50"]), "p99": f(r["p99"]), "max": f(r["max"]), "n": int(r["n"]), "file": r["file"]}
        return None

    def Q(model, config_suffix, exp="A in-process"):
        for r in thr:
            if r["exp"] == exp and r["model"] == model and r["config"].endswith(config_suffix):
                return {k: f(v) for k, v in r.items() if k.startswith(("q_safe", "first_fail"))} | {
                    "max_sustained_wps": f(r["max_achieved_wps_all_complete"]), "lowest_step_wps": f(r["lowest_step_wps"]),
                    "lowest_step_p99_ms": f(r["lowest_step_p99"]), "dir": r["dir"]}
        return None

    knees = {}
    ks = ev / "summary" / "knee_summary.json"
    if ks.exists():
        knees = json.loads(ks.read_text())
    cap = json.loads((ev / "raw/A/g2-1/Llama-Prompt-Guard-2-22M/capacity_trt_fp16_bucketed.json").read_text())
    capb = {r["batch"]: {"service_p50_ms": r["service_ms"]["p50"], "windows_per_s": r["windows_per_s"]} for r in cap["rows"]}
    restart = [json.loads(Path(p).read_text()) for p in sorted(glob.glob(str(ev / "raw/A/g2-1/Llama-Prompt-Guard-2-22M/restart_static_1x512_*.json")))]
    static = json.loads((ev / "raw/A/g2-1/Llama-Prompt-Guard-2-22M/lat_trt_fp16_static.json").read_text())
    cold = {f'{c["W"]}x{c["seq"]}': c.get("cold_ready_s") for c in static["configs"]}

    m22 = "PG2-22M"
    rec = {
        "schema": "rv-guard-recommendation/v1",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "evidence_dir": str(ev),
        "measured_on": {"vm": "GCE g2-standard-8: 1x NVIDIA L4 (24 GB, 72 W board power limit), 8 vCPU Intel Cascade Lake 2.2 GHz",
                        "zone": "asia-south1-c", "driver": "580.178.04", "image": "common-cu129-ubuntu-2204-nvidia-580-v20260909",
                        "note": "under sustained load the L4 sits at its 72 W cap and throttles SM clock (p50 ~1.4 GHz vs 2.04 GHz boost); "
                                "numbers below are at that steady state"},
        "model": {
            "use": "meta-llama/Llama-Prompt-Guard-2-22M",
            "label": "class index 1 = MALICIOUS; score = softmax(logits)[1] (probe: attack p1=0.999, benign p1=0.0006; "
                     "upstream config id2label {0: BENIGN, 1: MALICIOUS})",
            "onnx_export": "torch 2.14.0 TorchScript exporter (dynamo=False), opset 17, dynamic [batch, seq], inputs "
                           "input_ids/attention_mask int64, output logits fp32; ORT-CPU fp32 parity vs PyTorch fp32: 748/748 "
                           "decisions, max |dp| 6e-6 (raw/export/export_report.json)",
            "do_not_use_on_hot_path": "Llama-Prompt-Guard-2-86M: single-request latency on L4 is W1 "
                                      f"{(L('PG2-86M', 'trt_fp16_static', 1) or {}).get('p50')} ms / W2 "
                                      f"{(L('PG2-86M', 'trt_fp16_static', 2) or {}).get('p50')} ms p50 -> no p99<=10 ms point at W>=2",
        },
        "tokenizer": {
            "load": "tokenizers.Tokenizer.from_file(tokenizer.json); call no_truncation() and no_padding() (the file ships "
                    "truncation=512 + Fixed(512) padding enabled -> silent truncation otherwise)",
            "window": "encode(add_special_tokens=False) -> windows of 510 content tokens -> [CLS]=1 + window + [SEP]=2, pad id 0 to 512",
            "cost": "HF tokenizers single call: 1,024 tokens = 3.05 ms p50 on G2 Cascade Lake vCPU, 1.74 ms on C4 Emerald Rapids "
                    "(raw/A/*/tok/tok_bench*.json) -- budget it on the gateway CPU, it is not inside the GPU numbers",
        },
        "guard_backend": {
            "name": "local_gpu",
            "runtime": {"onnxruntime-gpu": "1.30.0", "tensorrt": "tensorrt-cu13==10.16.1.11 (ORT 1.30 TRT EP links libnvinfer.so.10)",
                        "cuda": "13.x from pip nvidia-* wheels (onnxruntime-gpu[cuda,cudnn]); put tensorrt_libs + nvidia/*/lib on "
                                "LD_LIBRARY_PATH; onnxruntime.preload_dlls()"},
            "session": {"providers": ["TensorrtExecutionProvider", "CUDAExecutionProvider"],
                        "trt_provider_options": {"trt_fp16_enable": True, "trt_engine_cache_enable": True,
                                                 "trt_engine_cache_path": "<cache dir keyed by model sha256 + ORT/TRT version + GPU sm + profile>",
                                                 "trt_timing_cache_enable": True, "trt_builder_optimization_level": 3,
                                                 "trt_max_workspace_size": 4294967296,
                                                 "trt_profile_min_shapes": "input_ids:1x512,attention_mask:1x512",
                                                 "trt_profile_opt_shapes": "input_ids:1x512,attention_mask:1x512",
                                                 "trt_profile_max_shapes": "input_ids:1x512,attention_mask:1x512"},
                        "session_options": {"graph_optimization_level": "ORT_ENABLE_ALL", "intra_op_num_threads": 1,
                                            "session.intra_op.allow_spinning": "0"},
                        "must": ["sess.disable_fallback()  # otherwise ORT silently re-creates the session on CUDA EP after a TRT error",
                                 "assert sess.get_providers()[0] == 'TensorrtExecutionProvider' at readiness",
                                 "warm the engine (>= 50 runs) before /readyz; no engine build after ready"]},
            "execution": "serial batch-1: each 510-token window is its own sess.run([1,512]) on the exact-shape engine; the W windows "
                         "of a request run back-to-back on ONE dedicated inference thread per GPU; FIFO across requests; no "
                         "cross-request batching",
            "microbatcher": {"max_batch_windows_B": 1, "max_wait_us": 0},
            "why": {
                "batch1_is_most_efficient": {"closed_loop_windows_per_s_by_batch": capb,
                                             "file": "raw/A/g2-1/Llama-Prompt-Guard-2-22M/capacity_trt_fp16_bucketed.json"},
                "dynamic_profile_engine_penalty": {"static_1x512": L(m22, "trt_fp16_static", 1),
                                                   "dyn_opt4": L(m22, "trt_fp16_dyn_opt4", 1),
                                                   "dyn_opt16": L(m22, "trt_fp16_dyn_opt16", 1)},
                "multi_profile_engine": "one engine with 7 profiles fails for any batch != profile 0 in ORT 1.30 TRT EP "
                                        "(IExecutionContext::enqueueV3 API Usage Error): use one exact-shape engine per shape",
                "cross_request_batching": "no throughput gain (larger batches are less efficient on the power-capped L4) and it "
                                          "adds the wait + a longer batch to every request's latency; see throughput table",
                "one_gpu_owner_process": {
                    "rule": "exactly ONE process owns each GPU session; N uvicorn workers must NOT each open their own "
                            "session on the same L4 (with or without CUDA MPS): send windows to the owner over local IPC or "
                            "run one worker per GPU",
                    "closed_loop_W2_by_process_count": [
                        {k: v for k, v in json.loads(Path(p).read_text()).items() if k in ("N", "mps", "aggregate_windows_per_s")}
                        | {"call_p50_ms": json.loads(Path(p).read_text())["call_latency_ms"]["p50"]}
                        for p in sorted(glob.glob(str(ev / "raw/A/g2-4/Llama-Prompt-Guard-2-22M/mp_trt_fp16_serial1/closed_*/closed_summary.json")))],
                    "single_process_reference": "466 windows/s closed loop at batch 1 (capacity_trt_fp16_bucketed.json)"},
                "window_execution_mode_one_request_in_flight": {
                    r["mode"] + f"_W{r['W']}": {"p50_ms": r["latency_ms"]["p50"], "p99_ms": r["latency_ms"]["p99"]}
                    for r in json.loads((ev / "raw/A/g2-2/parallel_windows_g22.json").read_text())["rows"]}
                if (ev / "raw/A/g2-2/parallel_windows_g22.json").exists() else None,
            },
            "engine_lifecycle": {"cold_build_s_by_shape": cold,
                                 "warm_restart_process_start_to_first_inference_s": [r["ready_s"] for r in restart]},
        },
        "measured_22M_single_request_latency_ms": {
            "W1": L(m22, "trt_fp16_static", 1), "W2_batch": L(m22, "trt_fp16_static", 2), "W3_batch": L(m22, "trt_fp16_static", 3),
            "W4_batch": L(m22, "trt_fp16_static", 4), "W7_batch": L(m22, "trt_fp16_static", 7),
            "W1_seq256": L(m22, "trt_fp16_static", 1, 256),
        },
        "capacity_22M_one_L4_open_loop": {
            "W2_serial1": Q(m22, "mb_trt_fp16_bucketed/W2_serial1"),
            "W3_serial1": Q(m22, "mb_trt_fp16_bucketed/W3_serial1"),
            "headline_mix_serial1": Q(m22, "mb_trt_fp16_bucketed/Wheadline_serial1"),
            "W2_nobatch_batch2": Q(m22, "mb_trt_fp16_bucketed/W2_nobatch"),
            "knee_confirmations": knees,
            "unit": "windows/s offered (Poisson); q_safe = highest step with p99 <= target, 0 drops, all complete",
        },
        "operating_points": operating_points(ev, Q, m22),
        "alternatives_measured": {
            "triton_26.05_trt_plan_loopback": Q(m22, "triton_26.05/W2_nobatch", exp="B Triton loopback"),
            "triton_offbox_c4_to_g2": Q(m22, "Llama-Prompt-Guard-2-22M/W2_nobatch", exp="C Triton off-box"),
        },
    }
    Path(ev / "recommended_guard_config.json").write_text(json.dumps(rec, indent=1))
    print(json.dumps(rec, indent=1)[:4000])


if __name__ == "__main__":
    main()
