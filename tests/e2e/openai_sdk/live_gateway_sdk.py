#!/usr/bin/env python3
"""Thin wrapper — delegates to scripts/openai_sdk_live_gateway.py (canonical O1 gate)."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

_CANONICAL = Path(__file__).resolve().parents[3] / "scripts" / "openai_sdk_live_gateway.py"
if not _CANONICAL.is_file():
    raise SystemExit(f"missing canonical gate script: {_CANONICAL}")
sys.argv[0] = str(_CANONICAL)
runpy.run_path(str(_CANONICAL), run_name="__main__")
