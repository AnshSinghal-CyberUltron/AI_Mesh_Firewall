#!/usr/bin/env bash
set -euxo pipefail
EXTRA=${1:-}
mkdir -p ~/gb/models ~/gb/out
sudo DEBIAN_FRONTEND=noninteractive apt-get update -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q netperf sockperf || sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q netperf
curl -LsSf https://astral.sh/uv/install.sh | sh
~/.local/bin/uv venv -p 3.12 ~/venv
~/.local/bin/uv pip install -p ~/venv/bin/python onnxruntime==1.30.0 numpy tokenizers onnx psutil
if [[ $EXTRA == export ]]; then
  ~/.local/bin/uv pip install -p ~/venv/bin/python --index-url https://download.pytorch.org/whl/cpu torch
  ~/.local/bin/uv pip install -p ~/venv/bin/python transformers onnxscript onnxconverter-common sentencepiece protobuf
fi
bash ~/gb/hostinfo.sh
echo BOOTSTRAP_DONE
