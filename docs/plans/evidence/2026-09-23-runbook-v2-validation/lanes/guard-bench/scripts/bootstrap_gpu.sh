#!/usr/bin/env bash
set -euxo pipefail
mkdir -p ~/gb/models ~/gb/out
curl -LsSf https://astral.sh/uv/install.sh | sh
~/.local/bin/uv venv -p 3.12 ~/venv
~/.local/bin/uv pip install -p ~/venv/bin/python "onnxruntime-gpu[cuda,cudnn]==1.30.0" numpy tokenizers onnx onnxconverter-common psutil
SO=$(ls ~/venv/lib/python3.12/site-packages/onnxruntime/capi/libonnxruntime_providers_tensorrt.so)
readelf -d "$SO" | grep NEEDED > ~/gb/trt_needed.txt || true
bash ~/gb/hostinfo.sh
echo BOOTSTRAP_DONE
