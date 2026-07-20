"""Demo runtime config from environment."""
from __future__ import annotations

import os


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


CONTROL_BASE_URL = env("CONTROL_BASE_URL", "http://127.0.0.1:8100").rstrip("/")
GATEWAY_BASE_URL = env(
    "ZEROSHIELD_BASE_URL",
    env("DEMO_GATEWAY_BASE_URL", "http://127.0.0.1:8300/v1"),
).rstrip("/")
RAG_COLLECTION = env("RAG_COLLECTION", "zeroshield-rag-e2e")
