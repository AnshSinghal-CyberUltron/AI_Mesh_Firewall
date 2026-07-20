"""Shared pytest fixtures and import path bootstrap for mcp-broker tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# B3 item#19: disable the warm agent-readiness wait in unit tests so the ensure
# route does not attempt a real HTTP /health poll against the mocked agent_url
# (timeout <= 0 short-circuits _wait_agent_ready with no network call).
os.environ.setdefault("MCP_SANDBOX_WARM_READY_TIMEOUT", "0")

REPO_ROOT = Path(__file__).resolve().parents[3]
SHARED = REPO_ROOT / "shared"
BROKER_SRC = REPO_ROOT / "services" / "mcp-broker" / "src"
for path in (SHARED, BROKER_SRC):
    if path.is_dir() and str(path) not in sys.path:
        sys.path.insert(0, str(path))
