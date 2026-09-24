#!/usr/bin/env bash
# Build the unit tarball on the controller: rvproto code, a read-only vendored copy of the REAL
# GW03 ResourceContract package (gateway_v2/__init__.py + gateway_v2/runtime/), ONNX exports and the
# models' tokenizer.json files. Output: $SP/rvproto-unit.tar.gz (+ .sha256).
# `make_tarball.sh code`: code-only $SP/rvproto-code.tar.gz (no ONNX; tokenizers + the models'
# sha256 files) for gateway-only nodes and for updating the code on units made from the image.
set -euo pipefail
MODE=${1:-full}
SP=${SP:-/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad}
REPO=/home/contact_cyberultron_com/AI_Mesh_Firewall
ST=$(mktemp -d "$SP/unit-staging.XXXX")
mkdir -p "$ST/rvproto" "$ST/vendor/gateway_v2" "$ST/models"
rsync -a --exclude .venv --exclude .run --exclude __pycache__ --exclude models --exclude '*.pyc' \
  --exclude node_modules --exclude .pytest_cache "$SP/rvproto/" "$ST/rvproto/"
cp "$REPO/gateway_v2/gateway_v2/__init__.py" "$ST/vendor/gateway_v2/"
rsync -a --exclude __pycache__ "$REPO/gateway_v2/gateway_v2/runtime" "$ST/vendor/gateway_v2/"
(cd "$REPO" && git rev-parse HEAD) > "$ST/vendor/GATEWAY_V2_COMMIT"
for m in 22M 86M; do
  [[ $MODE == code ]] || ln -s "$SP/rvproto/models/pg2-$m.onnx" "$ST/models/pg2-$m.onnx"
  (cd "$SP/rvproto/models" && sha256sum "pg2-$m.onnx") > "$ST/models/pg2-$m.onnx.sha256"
  cp "$SP/models/Llama-Prompt-Guard-2-$m/tokenizer.json" "$ST/models/tokenizer-$m.json"
done
OUTF=$SP/rvproto-unit.tar.gz; [[ $MODE == code ]] && OUTF=$SP/rvproto-code.tar.gz
tar -chf - -C "$ST" rvproto vendor models | gzip -1 > "$OUTF"
sha256sum "$OUTF" | tee "$OUTF.sha256"
rm -rf "$ST"
