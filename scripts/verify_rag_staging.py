#!/usr/bin/env python3
"""Staging verification for RAG E2E (Chroma + bootstrap + demo query).

Exit 0 when all checks pass; non-zero on first hard failure.

Usage (from repo root):
  python scripts/verify_rag_staging.py
  python scripts/verify_rag_staging.py --skip-bootstrap
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

DEMO_BASE = os.environ.get("DEMO_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
GATEWAY_BASE = os.environ.get("ZEROSHIELD_BASE_URL", "http://127.0.0.1:8300/v1").rstrip("/")
CONTROL_BASE = os.environ.get("CONTROL_BASE_URL", "http://127.0.0.1:8100").rstrip("/")
COLLECTION = os.environ.get("RAG_DEFAULT_COLLECTION", "demo_knowledge")
TIMEOUT = int(os.environ.get("VERIFY_RAG_TIMEOUT", "180"))


def _ok(msg: str) -> None:
    print(f"  PASS  {msg}")


def _fail(msg: str, code: int = 1) -> None:
    print(f"  FAIL  {msg}", file=sys.stderr)
    sys.exit(code)


def _get(url: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode())


def _post(url: str, body: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.status, json.loads(resp.read().decode())


def _docker_chroma_running() -> bool:
    try:
        out = subprocess.run(
            ["docker", "ps", "--filter", "name=chromadb", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return bool(out.stdout.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify RAG staging readiness")
    parser.add_argument("--skip-bootstrap", action="store_true", help="Skip bootstrap_rag.py")
    parser.add_argument("--skip-ingest", action="store_true", help="Skip ingest step")
    args = parser.parse_args()

    print("=== RAG staging verification ===\n")

    print("[1] Docker Chroma")
    if _docker_chroma_running():
        _ok("chromadb container is running")
    else:
        print("  WARN  chromadb container not detected — run: docker compose --profile chroma up -d chromadb")

    print("\n[2] Gateway health")
    gateway_root = GATEWAY_BASE[:-3] if GATEWAY_BASE.endswith("/v1") else GATEWAY_BASE
    try:
        status, _ = _get(f"{gateway_root}/health")
        if status == 200:
            _ok(f"gateway reachable at {GATEWAY_BASE}")
        else:
            _fail(f"gateway health returned {status}")
    except Exception as exc:
        _fail(f"gateway not reachable: {exc}")

    print("\n[3] Demo server")
    try:
        status, body = _get(f"{DEMO_BASE}/api/health")
        if status == 200 and body.get("ok"):
            _ok(f"demo server at {DEMO_BASE}")
        else:
            _fail(f"demo /api/health unexpected: {body}")
    except Exception as exc:
        _fail(f"demo not reachable — start: cd demo/zeroshield-openai-demo && python -m app.server ({exc})")

    if not args.skip_bootstrap:
        print("\n[4] Bootstrap RAG policy + vector provider")
        script = os.path.join(
            os.path.dirname(__file__),
            "..",
            "demo",
            "zeroshield-openai-demo",
            "scripts",
            "bootstrap_rag.py",
        )
        script = os.path.normpath(script)
        try:
            subprocess.run([sys.executable, script], check=True, timeout=120)
            _ok("bootstrap_rag.py completed")
        except subprocess.CalledProcessError as exc:
            _fail(f"bootstrap_rag.py failed (exit {exc.returncode})")
        except Exception as exc:
            _fail(f"bootstrap_rag.py error: {exc}")
    else:
        print("\n[4] Bootstrap (skipped)")

    print("\n[5] Demo RAG readiness probe")
    try:
        status, body = _get(f"{DEMO_BASE}/api/rag/readiness?collection={COLLECTION}")
        if body.get("ok"):
            _ok(f"RAG readiness for {COLLECTION}")
        else:
            _fail(f"RAG not ready: {body.get('message')} — {body.get('next_step')}")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            _fail("RAG readiness requires demo auth — set DEMO_REQUIRE_APP_LOGIN=0 or configure nginx basic auth")
        _fail(f"RAG readiness HTTP {exc.code}")
    except Exception as exc:
        _fail(f"RAG readiness probe failed: {exc}")

    if not args.skip_ingest:
        print("\n[6] Ingest + query (R2 Zedland)")
        doc = (
            "The capital of the demo country Zedland is Zedopolis. "
            "The national currency is the Zedollar (ZD)."
        )
        try:
            st, ing = _post(f"{DEMO_BASE}/api/rag/ingest", {"collection": COLLECTION, "texts": [doc]})
            if st >= 400 or ing.get("error"):
                _fail(f"ingest failed: status={st} body={ing}")
            _ok("ingest accepted")
        except Exception as exc:
            _fail(f"ingest error: {exc}")

        time.sleep(3)

        try:
            st, out = _post(
                f"{DEMO_BASE}/api/rag/query",
                {
                    "collection": COLLECTION,
                    "query": "What is the capital of Zedland?",
                    "synthesize": True,
                    "model": "auto",
                },
            )
            answer = (out.get("answer") or {}).get("content") or ""
            if "Zedopolis" not in answer:
                _fail(f"query answer missing Zedopolis: {answer[:200]!r}")
            _ok(f"synthesis grounded: {answer[:80]!r}")
        except Exception as exc:
            _fail(f"query error: {exc}")
    else:
        print("\n[6] Ingest + query (skipped)")

    print("\n[7] Control plane (optional)")
    try:
        status, _ = _get(f"{CONTROL_BASE}/api/health/")
        if status == 200:
            _ok(f"control at {CONTROL_BASE}")
    except Exception:
        print("  WARN  control not reachable (Module 2 KPI sync not verified)")

    print("\n=== All RAG staging checks passed ===")


if __name__ == "__main__":
    main()
