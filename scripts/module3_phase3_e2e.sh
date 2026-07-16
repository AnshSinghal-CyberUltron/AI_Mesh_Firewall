#!/usr/bin/env bash
# Thin wrapper → Python Phase 3 e2e (Windows-friendly).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
exec "$PY" "$ROOT/scripts/module3_phase3_e2e.py"
