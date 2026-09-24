#!/usr/bin/env bash
# Run the repo's frozen OpenAI conformance suite against a LIVE rvproto base URL.
#  (a) unmodified suite, TCP mode (AMF_CONFORMANCE_BASE_URL)   -> conformance-tcp.xml
#  (b) live-uvicorn test bodies with only live_url -> rvproto    -> conformance-live-adapted.xml
#  (c) repo Node matrix (openai@4.104.0)                          -> node-matrix.txt
# Provider must be in v1-stub fixture mode and the suite key seeded (org-a posture).
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
EV=${1:?evidence dir}; mkdir -p "$EV"
REPO=/home/contact_cyberultron_com/AI_Mesh_Firewall
GWPY=$REPO/gateway/.venv/bin/python
BASE=${RV_BASE_URL:-http://127.0.0.1:8400}
export PYTHONDONTWRITEBYTECODE=1 AMF_CONFORMANCE_APP=v2 AMF_CONFORMANCE_BASE_URL=$BASE
export PYTHONPATH=$REPO/gateway_v2:$REPO/gateway:$REPO/shared
cd "$REPO/gateway_v2"
"$GWPY" -m pytest tests/openai_conformance -o addopts="" -p no:cacheprovider -rA -q \
  --junitxml="$EV/conformance-tcp.xml" > "$EV/conformance-tcp.txt" 2>&1
echo "(a) exit=$?"
cd "$HERE/conformance_adapted"
"$GWPY" -m pytest test_live_uvicorn_on_rvproto.py -o addopts="" -p no:cacheprovider -rA -q \
  --import-mode=importlib --rootdir="$HERE/conformance_adapted" -c /dev/null \
  --junitxml="$EV/conformance-live-adapted.xml" \
  > "$EV/conformance-live-adapted.txt" 2>&1
echo "(b) exit=$?"
AMF_CONFORMANCE_API_KEY=zs_test_sdk_compat_0123456789abcdef \
  node "$REPO/gateway_v2/tests/openai_conformance/node/openai_matrix.mjs" > "$EV/node-matrix.txt" 2>&1
echo "(c) exit=$?"
