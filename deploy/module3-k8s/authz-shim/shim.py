"""HTTP front for Envoy ext_authz → OPA allow query → Module 3 deny ingest."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OPA_URL = os.environ.get("OPA_URL", "http://module3-opa:8181").rstrip("/")
CONTROL_URL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
AGENT_API_KEY = os.environ.get("AGENT_API_KEY", "")
ORG_SLUG = os.environ.get("ORGANIZATION_SLUG", "zeroshield")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "9191"))


def opa_allow(tenant: str, environment: str, estimated_tokens: int, path: str) -> tuple[bool, str]:
    body = {
        "input": {
            "tenant": tenant,
            "environment": environment,
            "estimated_tokens": estimated_tokens,
            "path": path or "/",
        }
    }
    req = urllib.request.Request(
        f"{OPA_URL}/v1/data/module3/authz/allow",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return False, f"opa http {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, f"opa error: {exc}"
    allowed = bool(data.get("result"))
    if allowed:
        return True, "ok"
    return False, "token quota exceeded or policy deny"


def report_deny(tenant: str, environment: str, estimated_tokens: int, path: str, reason: str) -> None:
    if not AGENT_API_KEY:
        return
    payload = {
        "organization_slug": ORG_SLUG,
        "action": "deny",
        "tenant_id": tenant,
        "environment": environment,
        "estimated_tokens": estimated_tokens,
        "path": path,
        "reason": reason,
        "source": "envoy_ext_authz",
    }
    req = urllib.request.Request(
        f"{CONTROL_URL}/api/module3/ingest/governance-event/",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {AGENT_API_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as exc:  # noqa: BLE001
        print("deny ingest error", exc, flush=True)


def report_usage(tenant: str, environment: str, estimated_tokens: int) -> None:
    if not AGENT_API_KEY or estimated_tokens <= 0:
        return
    payload = {
        "organization_slug": ORG_SLUG,
        "tenant_id": tenant,
        "environment": environment,
        "tokens": estimated_tokens,
    }
    req = urllib.request.Request(
        f"{CONTROL_URL}/api/module3/ingest/quota-usage/",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {AGENT_API_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as exc:  # noqa: BLE001
        print("usage ingest error", exc, flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        print("authz", fmt % args, flush=True)

    def do_GET(self) -> None:  # noqa: N802
        self._authorize()

    def do_POST(self) -> None:  # noqa: N802
        # Drain body if present (Envoy may POST)
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self._authorize()

    def _authorize(self) -> None:
        tenant = (self.headers.get("x-tenant-id") or "").strip()
        environment = (self.headers.get("x-environment") or "").strip()
        path = (self.headers.get("x-forwarded-uri") or self.headers.get(":path") or self.path or "/").strip()
        try:
            estimated = int(float(self.headers.get("x-estimated-tokens") or "0"))
        except (TypeError, ValueError):
            estimated = 0

        allowed, reason = opa_allow(tenant, environment, estimated, path)
        if allowed:
            report_usage(tenant, environment, estimated)
            self.send_response(200)
            self.send_header("content-length", "0")
            self.end_headers()
            return

        report_deny(tenant, environment, estimated, path, reason)
        body = b"quota exceeded"
        self.send_response(403)
        self.send_header("content-type", "text/plain")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    print("authz-shim on", LISTEN_PORT, "opa=", OPA_URL, flush=True)
    ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
