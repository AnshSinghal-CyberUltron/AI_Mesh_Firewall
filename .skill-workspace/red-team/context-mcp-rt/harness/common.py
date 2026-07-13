"""
External red-team harness — Context Assembly & MCP Guardrails
================================================================

Shared client + evidence utilities used by all 5 attack lanes.

DUAL-TARGET DESIGN (see FINAL_REPORT.md section 2 "Methodology" for full
justification):

  PROD  = https://aimeshgateway.zeroshield.ai   (true external target, Cloudflare-fronted,
          confirmed via TLS cert + `server: cloudflare` + independent cf-ray IDs to be a
          genuinely separate deployment from this host — NOT a tunnel/proxy back to the
          local docker-compose stack: this host has no listener on :80/:443 and no
          cloudflared/ngrok process).
  REPLICA = http://127.0.0.1:8300 (the docker-compose deployment of the IDENTICAL
          ai_mesh_firewall-gateway codebase running on this box, org=zeroshield).

The harness bootstrap (per mission spec) attempted to mint a customer API key against
PROD via `POST https://aimeshbackend.zeroshield.ai/api/auth/token/` with the supplied
credentials. That call returned HTTP 401 `{"detail":"Invalid email or password."}`
against the REAL prod backend (confirmed genuine via TLS cert CN=zeroshield.ai,
issuer=Google Trust Services, cf-ray headers, distinct request_id per call — not a
caching/tunnel artifact). Per the mission's own documented fallback ("OR local
http://127.0.0.1:8100 if prod fails") a key was minted against the local control
plane instead — but that key returns HTTP 401 `unauthorized` when presented to the
REAL external https://aimeshgateway.zeroshield.ai/v1/models (proving PROD is a
genuinely distinct tenant/database, not a shared backend with this replica).

Consequently every attack function below accepts a `target` parameter
("prod" | "replica"). Lanes that fundamentally require an authenticated
customer session (chat completions with mcp_context, MCP tools/list,
tools/call, redaction/compliance verification) run against REPLICA
(same code, same guardrail logic, fully evidenced) — findings are labeled
[REPLICA-AUTHENTICATED]. Everything that can be tested WITHOUT a valid key
(unauthenticated surface, error-message hygiene, header/TLS posture,
cross-org slug probing pre-auth, rate-limit/backoff behavior, timing) is run
directly against PROD and labeled [PROD-EXTERNAL].
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

WORKDIR = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = WORKDIR / "evidence"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

PROD_GATEWAY_BASE = "https://aimeshgateway.zeroshield.ai"
PROD_BACKEND_BASE = "https://aimeshbackend.zeroshield.ai"
REPLICA_GATEWAY_BASE = os.environ.get("REPLICA_GATEWAY_BASE", "http://127.0.0.1:8300")
ORG_SLUG = os.environ.get("RT_ORG_SLUG", "zeroshield")
DEFAULT_MODEL = os.environ.get("RT_MODEL", "google/gemma-4-26b-a4b-it:free")

_KEY_FILE = WORKDIR / "gateway_key.txt"


def load_api_key() -> str:
    if not _KEY_FILE.exists():
        raise RuntimeError(f"missing bootstrap key at {_KEY_FILE}")
    return _KEY_FILE.read_text().strip()


def base_url_for(target: str) -> str:
    return PROD_GATEWAY_BASE if target == "prod" else REPLICA_GATEWAY_BASE


# ---------------------------------------------------------------------------
# Secret-redaction for report-safe evidence rendering (NOT applied to the raw
# evidence JSON on disk — the raw bytes are the proof; redaction is applied
# only when the report quotes them).
# ---------------------------------------------------------------------------
_REDACT_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "***-**-####"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "u***@d***.com"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA****REDACTED"),
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "ghp_****REDACTED"),
    (re.compile(r"\bsk_live_[A-Za-z0-9]{24,}\b"), "sk_live_****REDACTED"),
    (re.compile(r"\b(?:10|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b"), "10.x.x.x"),
    (re.compile(r"169\.254\.169\.254"), "169.254.169.254[metadata-canary]"),
]


def redact_for_report(text: str) -> str:
    out = text
    for pat, repl in _REDACT_PATTERNS:
        out = pat.sub(repl, out)
    return out


# ---------------------------------------------------------------------------
# Evidence persistence
# ---------------------------------------------------------------------------
def save_evidence(lane: str, name: str, payload: dict[str, Any]) -> Path:
    lane_dir = EVIDENCE_DIR / lane
    lane_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{name}.json"
    path = lane_dir / fname
    payload = dict(payload)
    payload.setdefault("_captured_at", datetime.now(timezone.utc).isoformat())
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


@dataclass
class Finding:
    lane: str
    id: str
    title: str
    severity: str  # CRITICAL | HIGH | MEDIUM | LOW | INFO
    target: str  # prod-external | replica-authenticated
    verdict: str  # PASS (guardrail held) | FAIL (bypass achieved) | INCONCLUSIVE
    description: str
    repro_evidence: str  # path to evidence json (relative)
    impact: str = ""
    recommendation: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "lane": self.lane,
            "id": self.id,
            "title": self.title,
            "severity": self.severity,
            "target": self.target,
            "verdict": self.verdict,
            "description": self.description,
            "repro_evidence": self.repro_evidence,
            "impact": self.impact,
            "recommendation": self.recommendation,
            "extra": self.extra,
        }


FINDINGS_PATH = WORKDIR / "evidence" / "ALL_FINDINGS.json"


def append_finding(f: Finding) -> None:
    findings = []
    if FINDINGS_PATH.exists():
        try:
            findings = json.loads(FINDINGS_PATH.read_text())
        except Exception:
            findings = []
    findings.append(f.to_dict())
    FINDINGS_PATH.write_text(json.dumps(findings, indent=2, default=str))


# ---------------------------------------------------------------------------
# MCP JSON-RPC helper (gateway customer path)
# ---------------------------------------------------------------------------
def mcp_call(
    target: str,
    server_slug: str,
    method: str,
    params: dict | None = None,
    api_key: str | None = None,
    org_slug: str | None = None,
    rpc_id: int | None = None,
    timeout: float = 30.0,
    extra_headers: dict | None = None,
) -> dict:
    """POST a JSON-RPC 2.0 envelope to the gateway MCP customer path.

    https://{gateway}/gateway/{org_slug}/mcp/{server_slug}
    """
    api_key = api_key or load_api_key()
    org_slug = org_slug or ORG_SLUG
    base = base_url_for(target)
    url = f"{base}/gateway/{org_slug}/mcp/{server_slug}"
    body = {"jsonrpc": "2.0", "id": rpc_id or 1, "method": method}
    if params is not None:
        body["params"] = params
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    t0 = time.time()
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=body, headers=headers)
        elapsed = time.time() - t0
        try:
            data = r.json()
        except Exception:
            data = {"_non_json_body": r.text[:4000]}
        return {
            "ok": True,
            "url": url,
            "status_code": r.status_code,
            "elapsed_s": round(elapsed, 3),
            "request": body,
            "response": data,
            "response_headers": dict(r.headers),
        }
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "elapsed_s": round(time.time() - t0, 3),
            "request": body,
            "error": f"{type(exc).__name__}: {exc}",
        }


def mcp_tools_list(target: str, server_slug: str, **kw) -> dict:
    return mcp_call(target, server_slug, "tools/list", **kw)


def mcp_tools_call(target: str, server_slug: str, tool_name: str, arguments: dict, **kw) -> dict:
    return mcp_call(target, server_slug, "tools/call", {"name": tool_name, "arguments": arguments}, **kw)


# ---------------------------------------------------------------------------
# OpenAI SDK client factory
# ---------------------------------------------------------------------------
def get_sdk_client(target: str, api_key: str | None = None, timeout: float = 90.0):
    from openai import OpenAI

    api_key = api_key or load_api_key()
    base_url = f"{base_url_for(target)}/v1"
    return OpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=timeout)


def raw_chat(target: str, messages: list, model: str = "", api_key: str | None = None,
             extra_body: dict | None = None, timeout: float = 90.0) -> dict:
    """Direct httpx POST to /v1/chat/completions (bypasses SDK parsing so we can
    inspect the exact raw JSON error envelope / any leaked fields)."""
    api_key = api_key or load_api_key()
    model = model or (DEFAULT_MODEL if target == "replica" else "auto")
    url = f"{base_url_for(target)}/v1/chat/completions"
    body = {"model": model, "messages": messages}
    if extra_body:
        body.update(extra_body)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    t0 = time.time()
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=body, headers=headers)
        try:
            data = r.json()
        except Exception:
            data = {"_non_json_body": r.text[:4000]}
        return {
            "ok": True,
            "url": url,
            "status_code": r.status_code,
            "elapsed_s": round(time.time() - t0, 3),
            "request": body,
            "response": data,
        }
    except Exception as exc:
        return {"ok": False, "url": url, "error": f"{type(exc).__name__}: {exc}", "request": body}


def new_canary(prefix: str = "canary") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"
