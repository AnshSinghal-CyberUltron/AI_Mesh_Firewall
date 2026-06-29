#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3.12 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

if [[ ! -f .env ]]; then
  echo "Copy .env.example to .env and set ZEROSHIELD_API_KEY"
  cp .env.example .env
  exit 1
fi

export $(grep -v '^#' .env | xargs)
exec python -m app.server
