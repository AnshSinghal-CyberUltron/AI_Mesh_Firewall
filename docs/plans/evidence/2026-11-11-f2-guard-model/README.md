# F2 guard-model prototype evidence (11 November 2026)

Backs `docs/plans/2026-11-11-f2-guard-model.md`. Prototype code only; nothing here is imported by
`gateway_v2`. Model weights, the exported ONNX file and downloaded datasets are NOT in git
(`.gitignore`); their hashes are in `results/`.

## Environment

- Host: shared, 2 vCPU Intel Xeon @ 2.10 GHz (AVX-512, AMX), 7 GB RAM; load average 6–10 from
  other agents' suites during every measurement. Latency is reported as process CPU time first.
- Python 3.13.18 venv with `torch 2.15.0+cpu`, `transformers 5.22.2`, `onnx 1.23.2`,
  `onnxscript 0.7.2`, `onnxruntime 1.31.1`, `tokenizers 0.23.3`, `numpy 2.5.4`, `pyarrow 26.0.1`,
  `huggingface_hub 2.4.0`, `psutil 7.2.2` (full list: `results/venv-freeze.txt`). A second venv
  with `transformers 4.57.1` / `tokenizers 0.22.2` is used only by `tok_dump.py`.
- Model: `/home/claude/work/models/pg2-22m` (Meta's files at HF revision
  `11614a155199674a0a95e6602d6ab0417b790ed0`); set `F2_MODEL_DIR` to point elsewhere.
  `F2_OUT_DIR` (default `.f2-scratch/out`) receives the ONNX file, bundle copies and datasets.

## Scripts (run from the worktree root with `python -I`)

| Script | What it does | Output | Lock |
|---|---|---|---|
| `current_behaviour_probe.py` | drives today's `LocalOnnxBackend`/`SemanticDetector` (gateway_v2 venv) | `results/current_behaviour_probe.jsonl` | no (instant) |
| `export_onnx.py` | fp32 ONNX export, opset 17, checker; run twice for determinism | `results/export.json`, `results/export_determinism.json` | heavy (first run); second run 10 s CPU |
| `parity.py` | PyTorch vs ORT on 811 fixed rows + padding drift | `results/parity.json` | heavy |
| `parity_full_windows.py` | PyTorch vs ORT on 60 full 512-position windows | `results/parity_full_windows.json` | no (1 min 55 s CPU) |
| `language_probe.py` | today's confusable fold and language-ID vs PG2 scores (imports `gateway_v2` from the worktree) | `results/language_probe.json` | no (seconds) |
| `tok_dump.py`, `tok_divergence.py` | tokenizer front-ends across transformers 4/5 and their score impact | `results/tokenizer_divergence.json`, `results/golden_token_ids.json` | no (< 2 min CPU) |
| `fetch_datasets.py` | pinned-revision downloads | `results/datasets.json` | no (network) |
| `eval_datasets.py` | L36-2 recall/FPR, window-count FPR, languages, planted test | `results/eval_summary.json`, `results/eval_rows.csv` | heavy |
| `bench_window.py` | per-window CPU/wall at 1/2 threads, cold start, concurrency, tokenizer | `results/bench_window.json` | no (≈ 3 min CPU, see note) |
| `offloop.py` | loop lag inline vs executor, tokenizer GIL, terminate, overload, process pool, RSS | `results/offloop.json` | no (< 2 min CPU) |
| `manifest.py` | bundle + manifest build, boot verify, tamper matrix | `results/guard-manifest.json`, `guard-manifest.sha256`, `manifest_verify.json` | no (≈ 15 s CPU, mostly hashing) |
| `ort_bytes_rss.py` | RSS of a session built from verified bytes, with and without onnxruntime's retained copy | `results/ort_bytes_rss.json` | no (3 × 2 s) |
| `injection_lengths.py` | token lengths of the injection sets (overlap choice) | `results/injection_lengths.json` | no (seconds) |
| `proxy_latency.py` (12 Nov) | **weight-free** latency proxy: DeBERTa-v3-xsmall architecture with random weights (parameter count asserted equal to the real 70,830,722), same exporter; thread-CPU and wall p50/p99 for fp32, fixed-shape fp32 and INT8 (`quantize_dynamic` QInt8) at 16–512 positions; optimised-graph node counts. Needs no gated file and no token | `results/proxy_latency.json` | no (80 s CPU; run with `nice -n 10` while ~7 jobs queued on the heavy lock) |

**12 Nov.** The weights are no longer on this host and `HF_TOKEN` is not set, so every
weight-dependent script above is pending. Plan §3.2 lists them. They run next in the GitHub
`guard-model` workflow (plan §9.4), not on this host.

`proxy_latency.py` ran in a separate venv outside the repo (`/home/claude/work/runs/f2-author/venv`),
with the same pinned toolchain as `results/venv-freeze.txt`. Its ONNX files are under
`/home/claude/work/runs/f2-author/out` and are not committed.

Heavy steps ran as `flock /home/claude/work/.heavy.lock nice -n 10 <python> -I <script>`. The lock
queue was ~1 hour deep (full suites of other agents), so the short steps ran with `nice -n 10`
only; `bench_window.py` used ≈ 3 min CPU, slightly over the guide's 2-minute threshold, and its
numbers are CPU-time based for that reason.
