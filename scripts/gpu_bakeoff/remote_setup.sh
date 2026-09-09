#!/usr/bin/env bash
# Install CUDA-capable ORT + the PG2-22M ONNX checkpoint, then run the bake-off.
set -euo pipefail
cd "$(dirname "$0")"

echo "== gpu =="
nvidia-smi || { echo "nvidia-smi missing — driver not ready"; exit 1; }

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -U pip wheel
# CUDA 12 wheel. TensorRT EP is extra; CUDA EP is the floor for this pass.
python -m pip install \
  'onnxruntime-gpu>=1.19' \
  'transformers>=4.44' \
  'huggingface_hub>=0.24' \
  'numpy<2.3' \
  'protobuf>=4'

python - <<'PY'
import onnxruntime as ort
print("ort", ort.__version__)
print("providers", ort.get_available_providers())
PY

OUT="${OUT:-$PWD/pg2_input_latency.json}"
python pg2_input_latency.py --iters "${ITERS:-1000}" --warmup "${WARMUP:-50}" --out "$OUT"
ls -l "$OUT"
