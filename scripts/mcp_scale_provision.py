#!/usr/bin/env python3
"""Provision the P8/P9 scale matrix: N orgs x M MCP servers.

Org count is ``NUM_ORGS`` (default 3, backward compat) and servers-per-org is
``SERVERS_PER_ORG`` (default 5). For the required 300-500 sandboxes:
``NUM_ORGS=50 SERVERS_PER_ORG=10 python scripts/mcp_scale_provision.py`` (the 50
orgs must be pre-created in the control plane first).

Orgs are pre-created via `ensure_zeroshield_admin` (control mgmt command). For
each org this: logs in, provisions the org gateway key, registers M deterministic
"Everything" stdio MCP servers (echo/add tools), and writes a manifest
(org slug -> gateway key -> server slugs) consumed by the parallel tool-call /
concurrency / load / leakage harnesses (items #27-31).

The manifest holds live gateway keys → it is written to a git-ignored path and
never committed.

  BASE_URL=http://127.0.0.1:8180 python scripts/mcp_scale_provision.py
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8180").rstrip("/")
PASSWORD = os.environ.get("SCALE_PASSWORD", "Adm1n!Pass#2024")
SERVERS_PER_ORG = int(os.environ.get("SERVERS_PER_ORG", "5"))
MANIFEST = os.environ.get(
    "SCALE_MANIFEST",
    os.path.join(os.path.dirname(__file__), "ralph", ".mcp_scale_manifest.json"),
)
_BASE_ORGS = [
    ("zeroshield", "admin@zeroshield.io"),
    ("org-a", "admin@org-a.io"),
    ("org-b", "admin@org-b.io"),
]


def build_orgs(num_orgs: int) -> list[tuple[str, str]]:
    """First ``num_orgs`` (slug, email) pairs for the scale matrix.

    BACKSTOP CHG-0010 (item 14): org count was a hardcoded 3-tuple, capping the
    matrix at 3 orgs x SERVERS_PER_ORG (=15 at default 5) — unreachable for the
    required 300-500 sandboxes. It is now driven by ``NUM_ORGS``. The first three
    preserve the historical (zeroshield, org-a, org-b) identities for backward
    compat; additional orgs follow the ``org-<i>`` / ``admin@org-<i>.io``
    convention (i from 3). Each org MUST be PRE-CREATED in the control plane
    before this provisioner runs (it logs in, it does not create orgs) — the
    fixed convention makes bulk pre-creation scriptable. For a true per-tenant
    fork budget the broker must also assign a DISTINCT sandbox UID per org
    (NPROC_ROOT_CAUSE.md); that is a broker-side concern, tracked separately.
    """
    n = max(num_orgs, 0)
    if n <= len(_BASE_ORGS):
        return list(_BASE_ORGS[:n])
    extra = [(f"org-{i}", f"admin@org-{i}.io") for i in range(len(_BASE_ORGS), n)]
    return list(_BASE_ORGS) + extra


NUM_ORGS = int(os.environ.get("NUM_ORGS", str(len(_BASE_ORGS))))
ORGS = build_orgs(NUM_ORGS)


def _req(method: str, path: str, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE}{path}", method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {"error": e.read().decode()[:300]}


def login(email: str) -> str:
    _, d = _req("POST", "/api/auth/token/", body={"email": email, "password": PASSWORD})
    return d.get("access", "")


def gateway_key(token: str) -> str:
    # POST provisions (and returns, once) the org gateway key.
    _, d = _req("POST", "/api/mcp-connector/org-gateway-key/", token=token)
    return d.get("key") or ""


def list_servers(token: str) -> list[dict]:
    _, d = _req("GET", "/api/mcp-connector/servers/", token=token)
    return d if isinstance(d, list) else d.get("results", [])


def ensure_everything(token: str, n: int) -> str:
    name = f"Everything {n}"
    for s in list_servers(token):
        if s.get("name") == name:
            return s.get("server_slug", "")
    _, d = _req(
        "POST",
        "/api/mcp-connector/servers/",
        token=token,
        body={
            "name": name,
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-everything"],
            "description": "P8/P9 scale test",
        },
    )
    return d.get("server_slug", "") or f"ERR:{d.get('detail') or d.get('error') or d}"


def main() -> int:
    manifest = {"base": BASE, "orgs": []}
    for slug, email in ORGS:
        tok = login(email)
        if not tok:
            print(f"{slug}: LOGIN FAILED ({email})")
            continue
        key = gateway_key(tok)
        servers = [ensure_everything(tok, n) for n in range(1, SERVERS_PER_ORG + 1)]
        manifest["orgs"].append({"slug": slug, "email": email, "gateway_key": key, "servers": servers})
        print(f"{slug}: key={'set' if key else 'MISSING'} servers={servers}")
    os.makedirs(os.path.dirname(os.path.abspath(MANIFEST)), exist_ok=True)
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    total = sum(len(o["servers"]) for o in manifest["orgs"])
    print(f"\nmanifest -> {MANIFEST}: {len(manifest['orgs'])} orgs, {total} MCP servers")
    ok = len(manifest["orgs"]) == len(ORGS) and total == len(ORGS) * SERVERS_PER_ORG
    ok = ok and all(o["gateway_key"] for o in manifest["orgs"])
    ok = ok and all(s and not s.startswith("ERR") for o in manifest["orgs"] for s in o["servers"])
    print("PROVISION:", "OK" if ok else "INCOMPLETE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
