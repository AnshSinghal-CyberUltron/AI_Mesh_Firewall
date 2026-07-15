"""Module 3 admission controller — Cosign signature verification gatekeeper."""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$", re.IGNORECASE)


def _admission_mode() -> str:
    return (os.environ.get("MODULE3_ADMISSION_MODE", "passthrough") or "passthrough").strip().lower()


def _valid_sha256(value: str) -> bool:
    v = (value or "").strip().lower().removeprefix("sha256:")
    return bool(_SHA256_RE.match(v))


def verify_admission_payload(payload: dict) -> dict:
    """
    Verify a deployment admission request.

    passthrough mode (dev / local demo):
      - deny if signature_digest is empty or starts with 'invalid:'
      - deny if data_sha256/model_sha256 provided but malformed
      - allow otherwise when image_ref is present
      - does NOT call Cosign (safe for Admission Simulator)

    verify mode (production):
      - same integrity / explicit-invalid denies
      - requires Cosign public key (PEM or file path)
      - MODULE3_COSIGN_VERIFY_IMAGE=1 → ``cosign verify`` on image_ref
      - MODULE3_COSIGN_VERIFY_IMAGE=0 (default) → ``cosign verify-blob`` on
        model/data SHA-256 payload with detached signature_digest
      - fail-closed on missing Cosign binary, timeout, or Cosign non-zero exit
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

    mode = _admission_mode()

    if signature_digest.startswith("invalid:") or signature_digest == "invalid":
        return _result(False, "Cosign signature missing or invalid", started, method="explicit_deny")

    if mode != "verify":
        if not signature_digest:
            return _result(False, "Cosign signature missing or invalid", started, method="passthrough")
        return _result(
            True,
            "Admission allowed — passthrough mode (Cosign not enforced)",
            started,
            method="passthrough",
        )

    # ── Production verify mode ──────────────────────────────────────────────
    try:
        from cosign_verify import CosignVerifyError, verify_with_cosign
    except ImportError:
        try:
            from .cosign_verify import CosignVerifyError, verify_with_cosign
        except ImportError as exc:
            return _result(
                False,
                f"Cosign verifier unavailable: {exc}",
                started,
                method="cosign_unavailable",
            )

    try:
        cosign_out = verify_with_cosign(
            image_ref=image_ref,
            signature_digest=signature_digest,
            data_sha256=data_sha256,
            model_sha256=model_sha256,
        )
    except CosignVerifyError as exc:
        logger.warning("Admission Cosign deny/misconfig: %s", exc.reason)
        return _result(False, exc.reason, started, method="cosign_error")

    if not cosign_out.get("ok"):
        return _result(
            False,
            str(cosign_out.get("reason") or "Cosign verification failed"),
            started,
            method=str(cosign_out.get("method") or "cosign_cli"),
            extra={
                "cosign_stderr": cosign_out.get("stderr"),
                "key_source": cosign_out.get("key_source"),
            },
        )

    return _result(
        True,
        str(cosign_out.get("reason") or "Cosign signature verified"),
        started,
        method=str(cosign_out.get("method") or "cosign_cli"),
        extra={"key_source": cosign_out.get("key_source")},
    )


def _result(
    allowed: bool,
    reason: str,
    started: float,
    *,
    method: str = "",
    extra: dict | None = None,
) -> dict:
    latency_ms = int((time.perf_counter() - started) * 1000)
    out = {
        "allowed": allowed,
        "reason": reason,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "latency_ms": latency_ms,
        "admission_mode": _admission_mode(),
        "verification_method": method
        or ("passthrough" if _admission_mode() != "verify" else "cosign_cli"),
    }
    if extra:
        for key, val in extra.items():
            if val is not None and val != "":
                out[key] = val
    return out
