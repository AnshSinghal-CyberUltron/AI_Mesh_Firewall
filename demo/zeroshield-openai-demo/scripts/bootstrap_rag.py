#!/usr/bin/env python3
"""Bootstrap demo RAG collection policy on the control plane (one-time setup)."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

CONTROL = os.environ.get("CONTROL_BASE_URL", "http://127.0.0.1:8100").rstrip("/")
EMAIL = os.environ.get("TEST_EMAIL", "admin@zeroshield.io")
PASSWORD = os.environ.get("TEST_PASSWORD", "Adm1n!Pass#2024")
COLLECTION = os.environ.get("RAG_DEFAULT_COLLECTION", "demo_knowledge")


def _req(method: str, path: str, token: str = "", body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{CONTROL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode()
        try:
            detail = json.loads(payload)
        except json.JSONDecodeError:
            detail = payload
        raise SystemExit(f"{method} {path} -> {exc.code}: {detail}") from exc


def main() -> None:
    tok = _req("POST", "/api/auth/token/", body={"email": EMAIL, "password": PASSWORD})
    access = tok["access"]

    existing = _req("GET", "/api/vector-policies/", token=access)
    items = existing if isinstance(existing, list) else existing.get("results", existing)

    for item in items if isinstance(items, list) else []:
        if item.get("collection_name") == COLLECTION and item.get("enabled"):
            if item.get("vector_db_type") != "chroma":
                _req(
                    "PATCH",
                    f"/api/vector-policies/{item['id']}/",
                    token=access,
                    body={"vector_db_type": "chroma"},
                )
                print(f"Updated vector policy '{COLLECTION}' vector_db_type -> chroma")
            else:
                print(f"Vector policy for '{COLLECTION}' already exists (id={item.get('id')})")
            break
    else:
        created = _req(
            "POST",
            "/api/vector-policies/",
            token=access,
            body={
                "name": f"Demo — {COLLECTION}",
                "project_id": "zeroshield",
                "collection_name": COLLECTION,
                "vector_db_type": "custom",
                "default_action": "allow",
                "allowed_operations": ["query", "insert", "delete"],
                "max_results_per_query": 10,
                "max_query_length": 4000,
                "require_context_scan": True,
                "block_sensitive_documents": False,
                "anomaly_distance_threshold": 0.85,
                "enabled": True,
                "metadata": {"source": "zeroshield-openai-demo-bootstrap"},
            },
        )
        print(f"Created vector policy id={created.get('id')} for collection '{COLLECTION}'")

    _req("POST", "/api/vector-policies/compile/", token=access, body={})
    print("Compiled vector policies to Redis.")

    providers = _req("GET", "/api/vector-providers/", token=access)
    plist = providers if isinstance(providers, list) else providers.get("results", [])
    chroma_url = os.environ.get("CHROMA_URL", "http://chromadb:8000")
    chroma = next((p for p in (plist or []) if p.get("provider_type") == "chroma"), None)
    custom = next((p for p in (plist or []) if p.get("provider_type") == "custom"), None)
    if chroma and chroma.get("is_active"):
        print(f"Chroma vector provider already configured (id={chroma.get('id')}).")
    elif custom:
        _req(
            "PATCH",
            f"/api/vector-providers/{custom['id']}/",
            token=access,
            body={
                "provider_type": "chroma",
                "display_name": "Demo Chroma",
                "connection_url": chroma_url,
                "is_active": True,
            },
        )
        print(f"Migrated custom provider id={custom['id']} -> chroma url={chroma_url}")
    else:
        prov = _req(
            "POST",
            "/api/vector-providers/",
            token=access,
            body={
                "provider_type": "chroma",
                "display_name": "Demo Chroma",
                "connection_url": chroma_url,
                "embedding_model": "text-embedding-3-small",
                "is_active": True,
            },
        )
        print(f"Created chroma vector provider id={prov.get('id')} url={chroma_url}")


if __name__ == "__main__":
    main()
