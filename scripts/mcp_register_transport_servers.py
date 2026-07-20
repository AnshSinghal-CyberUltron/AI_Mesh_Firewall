#!/usr/bin/env python3
"""Register P4.13 four-transport MCP servers + write TRANSPORT_MANIFEST for verify harness."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8180").rstrip("/")
PASSWORD = os.environ.get("SCALE_PASSWORD", "Adm1n!Pass#2024")
ORG = os.environ.get("TRANSPORT_ORG", "zeroshield")
EMAIL = os.environ.get("TRANSPORT_EMAIL", f"admin@{ORG}.io")
SCALE_MANIFEST = Path(
    os.environ.get(
        "SCALE_MANIFEST",
        REPO / "scripts" / "ralph" / ".mcp_scale_manifest.json",
    )
)
OUT_MANIFEST = Path(
    os.environ.get(
        "TRANSPORT_MANIFEST_OUT",
        REPO / "mcp-parallel" / "findings" / "p4-13" / "TRANSPORT_MANIFEST.zeroshield.json",
    )
)

REMOTE_SERVERS = [
    {
        "name": "HTTP Everything stub",
        "transport": "streamable-http",
        "url": os.environ.get("HTTP_STUB_URL", "http://http-everything.stub:3001/mcp"),
        "slug_key": "streamable-http",
        "slug_hint": "http-everything-stub",
    },
    {
        "name": "SSE Everything stub",
        "transport": "sse",
        "url": os.environ.get("SSE_STUB_URL", "http://sse-everything.stub:3002/sse"),
        "slug_key": "sse",
        "slug_hint": "sse-everything-stub",
    },
    {
        "name": "WS Everything stub",
        "transport": "websocket",
        "url": os.environ.get("WS_STUB_URL", "ws://ws-everything.stub:3003/mcp"),
        "slug_key": "websocket",
        "slug_hint": "ws-everything-stub",
    },
]


def _req(method: str, path: str, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE}{path}", method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {"error": e.read().decode()[:400]}


def _gateway_key(token: str) -> str:
    if SCALE_MANIFEST.is_file():
        data = json.loads(SCALE_MANIFEST.read_text(encoding="utf-8"))
        for org in data.get("orgs") or []:
            if org.get("slug") == ORG and org.get("gateway_key"):
                return org["gateway_key"]
    _, d = _req("POST", "/api/mcp-connector/org-gateway-key/", token=token)
    return d.get("key") or ""


def _list_servers(token: str) -> list[dict]:
    _, d = _req("GET", "/api/mcp-connector/servers/", token=token)
    return d if isinstance(d, list) else d.get("results", [])


def _ensure_server(token: str, spec: dict) -> tuple[str, str]:
    for s in _list_servers(token):
        if s.get("name") == spec["name"]:
            return s.get("server_slug", ""), "exists"
    status, d = _req(
        "POST",
        "/api/mcp-connector/servers/",
        token=token,
        body={
            "name": spec["name"],
            "transport": spec["transport"],
            "url": spec["url"],
            "auth_type": "none",
            "description": "P4.13 four-transport verify",
        },
    )
    if status not in (200, 201):
        return "", f"ERR:{status}:{d}"
    slug = d.get("server_slug") or ""
    # Sync tools for remote transports (stdio self-registers on first broker call).
    if slug and spec["transport"] != "stdio":
        sid = d.get("id")
        if sid:
            sync_status, sync_body = _req(
                "POST", f"/api/mcp-connector/servers/{sid}/tools/", token=token, body={}
            )
            if sync_status >= 400:
                return slug, f"registered_but_sync_{sync_status}:{sync_body}"
    return slug, "created"


def main() -> int:
    _, tok = _req("POST", "/api/auth/token/", body={"email": EMAIL, "password": PASSWORD})
    token = tok.get("access", "")
    if not token:
        print(f"LOGIN FAILED for {EMAIL}")
        return 1

    key = _gateway_key(token)
    if not key:
        print("gateway key missing")
        return 1

    stdio_slug = "everything-1"
    for s in _list_servers(token):
        if s.get("server_slug") == stdio_slug or s.get("name") == "Everything 1":
            stdio_slug = s.get("server_slug") or stdio_slug
            break

    servers: dict[str, str] = {"stdio": stdio_slug}
    errors: list[str] = []
    for spec in REMOTE_SERVERS:
        slug, status = _ensure_server(token, spec)
        if slug:
            servers[spec["slug_key"]] = slug
            print(f"{spec['transport']}: {slug} ({status})")
        else:
            errors.append(f"{spec['transport']}: {status}")
            print(f"{spec['transport']}: FAILED {status}")

    manifest = {"org": ORG, "gateway_key": key, "servers": servers}
    OUT_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    OUT_MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"manifest -> {OUT_MANIFEST}")
    print(json.dumps(manifest, indent=2))

    # ws may be absent — still write partial manifest for stdio+http+sse runs.
    return 0 if not errors or len(servers) >= 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
