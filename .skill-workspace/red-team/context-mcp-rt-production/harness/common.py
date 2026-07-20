"""
Common utilities for the PRODUCTION-ONLY live red-team harness.

ABSOLUTE CONSTRAINTS (see FINAL_REPORT_PRODUCTION.md section 0):
  - Target ONLY https://aimeshgateway.zeroshield.ai (no localhost/docker/control-plane).
  - The API key is read ONLY from os.environ["GATEWAY_API_KEY"], falling back to a
    literal `GATEWAY_API_KEY=` line in a `.env` file on this host. No other variable
    name, no backend/control-plane bootstrap, no key minting.
  - If no key is found, every harness entrypoint raises NoProductionApiKey and the
    caller must stop and report rather than switching environments.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

GATEWAY_BASE_URL = "https://aimeshgateway.zeroshield.ai"
OPENAI_BASE_URL = f"{GATEWAY_BASE_URL}/v1"
MCP_URL_TEMPLATE = GATEWAY_BASE_URL + "/gateway/{org}/mcp/{server_slug}"
DEFAULT_ORG_SLUG = "zeroshield"

REPO_ROOT = Path("/home/contact_cyberultron_com/AI_Mesh_Firewall")
ENV_FILE = REPO_ROOT / ".env"
EVIDENCE_DIR = Path(__file__).resolve().parent.parent / "evidence"

_ENV_KEY_RE = re.compile(r"^GATEWAY_API_KEY=(.*)$")


class NoProductionApiKey(RuntimeError):
    """Raised when GATEWAY_API_KEY cannot be found via the sanctioned lookup path."""


def find_gateway_api_key() -> Optional[str]:
    """Look up GATEWAY_API_KEY ONLY via os.environ or a literal .env line.

    Returns None (never raises) so callers can decide how to report the gap.
    Never falls back to any other variable name (e.g. GATEWAY_INTERNAL_API_KEY,
    ZEROSHIELD_API_KEY) and never contacts the control plane / backend to mint one.
    """
    env_val = os.environ.get("GATEWAY_API_KEY", "").strip()
    if env_val:
        return env_val

    if ENV_FILE.is_file():
        try:
            for line in ENV_FILE.read_text(errors="ignore").splitlines():
                m = _ENV_KEY_RE.match(line.strip())
                if m:
                    val = m.group(1).strip().strip('"').strip("'")
                    if val:
                        return val
        except OSError:
            pass
    return None


def require_gateway_api_key() -> str:
    key = find_gateway_api_key()
    if not key:
        raise NoProductionApiKey(
            "GATEWAY_API_KEY not found in os.environ or in "
            f"{ENV_FILE} — per the assessment's absolute constraints, no other "
            "variable name or key-minting path is permitted."
        )
    return key


def redact_key(key: Optional[str]) -> str:
    if not key:
        return "<none>"
    if len(key) <= 8:
        return "<redacted>"
    return f"{key[:4]}...{key[-4:]} (len={len(key)})"


def new_canary(server_slug: str) -> str:
    """A unique, greppable per-server canary token for cross-MCP leakage checks."""
    return f"CANARY-{server_slug}-{uuid.uuid4().hex[:12]}"


@dataclass
class Evidence:
    """A single evidence record: one live request/response pair."""

    server_slug: str
    label: str
    request: dict[str, Any]
    response: dict[str, Any]
    notes: str = ""
    captured_at: float = field(default_factory=time.time)

    def to_json(self) -> dict[str, Any]:
        return {
            "server_slug": self.server_slug,
            "label": self.label,
            "captured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.captured_at)),
            "request": self.request,
            "response": self.response,
            "notes": self.notes,
        }


def save_evidence(ev: Evidence) -> Path:
    out_dir = EVIDENCE_DIR / ev.server_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^a-zA-Z0-9_.-]+", "_", ev.label)
    path = out_dir / f"{safe_label}.json"
    path.write_text(json.dumps(ev.to_json(), indent=2, default=str))
    return path


def redact_headers_for_evidence(headers: dict[str, str]) -> dict[str, str]:
    out = {}
    for k, v in headers.items():
        if k.lower() == "authorization":
            out[k] = "Bearer <redacted>"
        else:
            out[k] = v
    return out
