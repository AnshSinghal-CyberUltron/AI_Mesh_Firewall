"""Shared pytest fixtures and import path bootstrap for mcp-broker tests."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SHARED = REPO_ROOT / "shared"
BROKER_SRC = REPO_ROOT / "services" / "mcp-broker" / "src"
for path in (SHARED, BROKER_SRC):
    if path.is_dir() and str(path) not in sys.path:
        sys.path.insert(0, str(path))
