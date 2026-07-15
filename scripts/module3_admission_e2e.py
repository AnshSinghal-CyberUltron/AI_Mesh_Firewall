#!/usr/bin/env python3
"""Live Module 3 Phase 1 admission e2e — Cosign sign → register → allow/deny → Module 2."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

CONTROL = os.environ.get("CONTROL_URL", "http://127.0.0.1:8100").rstrip("/")
EMAIL = os.environ.get("TEST_EMAIL", "admin@zeroshield.io")
PASSWORD = os.environ.get("TEST_PASSWORD", "Adm1n!Pass#2024")
COSIGN_KEY = os.environ.get("COSIGN_KEY", "secrets/cosign.key")
REQUIRE_VERIFY = os.environ.get("REQUIRE_VERIFY", "1") == "1"
POLL_SEC = int(os.environ.get("POLL_SEC", "30"))
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "2"))


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"OK: {msg}")


def http_json(method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{CONTROL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        fail(f"{method} {path} -> HTTP {exc.code}: {detail[:500]}")
    except Exception as exc:  # noqa: BLE001
        fail(f"{method} {path}: {exc}")


def sign_blob(payload_path: Path, sig_path: Path) -> None:
    """Sign with host cosign, else gateway container cosign (key already mounted)."""
    env = os.environ.copy()
    env.setdefault("COSIGN_PASSWORD", "")
    if shutil.which("cosign"):
        subprocess.run(
            [
                "cosign",
                "sign-blob",
                "--yes",
                "--tlog-upload=false",
                "--key",
                COSIGN_KEY,
                "--output-signature",
                str(sig_path),
                str(payload_path),
            ],
            check=True,
            env=env,
            stdout=subprocess.DEVNULL,
        )
        return

    if not shutil.which("docker"):
        fail("cosign not found on PATH and docker unavailable")

    ok("using gateway container cosign (host cosign not on PATH)")
    # Write payload into gateway /tmp, sign with mounted /run/secrets/cosign.key, copy sig out.
    root = Path(__file__).resolve().parents[1]
    compose = ["docker", "compose", "-f", str(root / "docker-compose.yml")]
    remote_payload = f"/tmp/{payload_path.name}"
    remote_sig = f"/tmp/{sig_path.name}"
    subprocess.run(compose + ["cp", str(payload_path), f"gateway:{remote_payload}"], check=True)
    subprocess.run(
        compose
        + [
            "exec",
            "-T",
            "-e",
            "COSIGN_PASSWORD=",
            "gateway",
            "cosign",
            "sign-blob",
            "--yes",
            "--tlog-upload=false",
            "--key",
            "/run/secrets/cosign.key",
            "--output-signature",
            remote_sig,
            remote_payload,
        ],
        check=True,
        env=env,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(compose + ["cp", f"gateway:{remote_sig}", str(sig_path)], check=True)


def main() -> None:
    if not Path(COSIGN_KEY).is_file():
        fail(f"Missing Cosign key: {COSIGN_KEY}")

    print(f"=== Module 3 admission e2e (control={CONTROL}) ===")
    token_payload = http_json("POST", "/api/auth/token/", {"email": EMAIL, "password": PASSWORD})
    token = token_payload.get("access")
    if not token:
        fail(f"login missing access token: {token_payload}")
    ok("login")

    model_sha = hashlib.sha256(b"module3-e2e-model").hexdigest()
    data_sha = hashlib.sha256(b"module3-e2e-data").hexdigest()
    ts = str(int(time.time()))
    name = f"e2e-model-{ts}"
    version = "1.0.0"
    image_ref = f"registry.example.com/zeroshield/{name}:{version}"

    tmp = Path(tempfile.gettempdir())
    payload_path = tmp / f"m3-payload-{ts}.txt"
    sig_path = tmp / f"m3-sig-{ts}.sig"
    payload_path.write_bytes(model_sha.encode())
    sign_blob(payload_path, sig_path)
    sig_b64 = base64.b64encode(sig_path.read_bytes()).decode()

    reg = http_json(
        "POST",
        "/api/module3/llmops/artifacts/",
        {
            "name": name,
            "version": version,
            "image_ref": image_ref,
            "data_sha256": data_sha,
            "model_sha256": model_sha,
            "signature_digest": sig_b64,
            "source": "ci",
        },
        token=token,
    )
    art_id = reg.get("id")
    if not art_id:
        fail(f"register failed: {reg}")
    ok(f"registered artifact id={art_id}")

    allow = http_json("POST", "/api/module3/llmops/verify/", {"artifact_id": art_id}, token=token)
    gw = allow.get("gateway") or {}
    decision = allow.get("decision") or {}
    mode = gw.get("admission_mode") or ""
    method = gw.get("verification_method") or ""
    result = decision.get("result")
    print(f"  admission_mode={mode} verification_method={method} result={result}")
    if REQUIRE_VERIFY and mode != "verify":
        fail("gateway still in passthrough — use docker-compose.module3-verify.yml")
    if result != "allow":
        fail(f"expected allow, got {result}: {decision.get('reason')}")
    if REQUIRE_VERIFY and method in ("", "passthrough"):
        fail(f"expected Cosign verification_method, got {method!r}")
    ok("allow path (Cosign verify)")

    deny = http_json(
        "POST",
        "/api/module3/llmops/verify/",
        {"artifact_id": art_id, "signature_digest": "invalid:missing-cosign"},
        token=token,
    )
    if (deny.get("decision") or {}).get("result") != "deny":
        fail(f"expected deny, got {deny}")
    ok("deny path")

    needle = f"LLMOps admission denied: {name}:{version}"
    deadline = time.time() + POLL_SEC
    found = False
    while time.time() < deadline:
        data = http_json("GET", "/api/module2/incidents/?page_size=50", token=token)
        rows = data.get("results") if isinstance(data, dict) else data
        if isinstance(rows, list) and any(needle in str(r.get("title") or "") for r in rows):
            found = True
            break
        time.sleep(POLL_INTERVAL)
    if not found:
        fail(f"Module 2 incident not found within {POLL_SEC}s (workers up?): {needle}")
    ok("Module 2 incident for deny")
    print("=== Module 3 admission e2e PASSED ===")


if __name__ == "__main__":
    main()
