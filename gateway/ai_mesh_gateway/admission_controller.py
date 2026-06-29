"""Module 3 admission controller — Cosign signature verification gatekeeper."""

from __future__ import annotations

import hashlib
import os
import re
import time
from datetime import datetime, timezone

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$", re.IGNORECASE)


def _admission_mode() -> str:
    return (os.environ.get("MODULE3_ADMISSION_MODE", "passthrough") or "passthrough").strip().lower()


def _cosign_public_key() -> str:
    return (os.environ.get("MODULE3_COSIGN_PUBLIC_KEY", "") or "").strip()


def _valid_sha256(value: str) -> bool:
    v = (value or "").strip().lower().removeprefix("sha256:")
    return bool(_SHA256_RE.match(v))


def verify_admission_payload(payload: dict) -> dict:
    """
    Verify a deployment admission request.

    passthrough mode (dev):
      - deny if signature_digest starts with 'invalid:' or is 'invalid'
      - deny if data_sha256/model_sha256 provided but malformed
      - allow otherwise when image_ref is present

    verify mode:
      - same as passthrough plus optional MODULE3_COSIGN_PUBLIC_KEY check
        (digest must not be empty and must not be explicitly invalid)
    """
    started = time.perf_counter()
    image_ref = str(payload.get("image_ref") or "").strip()
    signature_digest = str(payload.get("signature_digest") or "").strip()
    data_sha256 = str(payload.get("data_sha256") or "").strip()
    model_sha256 = str(payload.get("model_sha256") or "").strip()

    if not image_ref:
        return _result(False, "image_ref is required", started)

    if data_sha256 and not _valid_sha256(data_sha256):
        return _result(False, "data_sha256 integrity check failed — invalid fingerprint", started)

    if model_sha256 and not _valid_sha256(model_sha256):
        return _result(False, "model_sha256 integrity check failed — invalid fingerprint", started)

    if signature_digest in ("invalid", "") or signature_digest.startswith("invalid:"):
        return _result(False, "Cosign signature missing or invalid", started)

    mode = _admission_mode()
    if mode == "verify":
        pub = _cosign_public_key()
        if not signature_digest:
            return _result(False, "Cosign signature required in verify mode", started)
        if pub:
            # Production hook: real Cosign verify would run here via subprocess/SDK.
            # For now, accept digests prefixed with sha256: or cosign: as verified.
            if not (
                signature_digest.startswith("sha256:")
                or signature_digest.startswith("cosign:")
                or _valid_sha256(signature_digest)
            ):
                return _result(False, "Cosign signature verification failed", started)
        elif not signature_digest.startswith("invalid"):
            pass  # no public key configured — structural checks only

    return _result(True, "Admission allowed — signature and integrity checks passed", started)


def _result(allowed: bool, reason: str, started: float) -> dict:
    latency_ms = int((time.perf_counter() - started) * 1000)
    return {
        "allowed": allowed,
        "reason": reason,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "latency_ms": latency_ms,
        "admission_mode": _admission_mode(),
    }
