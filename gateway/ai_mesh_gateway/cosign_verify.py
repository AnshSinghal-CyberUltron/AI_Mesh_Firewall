"""Production Cosign verification for Module 3 admission.

Supports two production flows:

1. **Image verify** (``MODULE3_COSIGN_VERIFY_IMAGE=1``):
   ``cosign verify --key <pub> <image_ref>``
   Signatures are stored beside the image in the OCI registry.

2. **Blob verify** (default, ``MODULE3_COSIGN_VERIFY_IMAGE=0``):
   ``cosign verify-blob --key <pub> --signature <sig> <payload>``
   CI signs model/data fingerprints (SHA-256) as detached blobs; Admission
   stores the signature in ``signature_digest`` and re-verifies here.

Fail-closed: missing binary, missing key, timeout, or Cosign non-zero exit
denies admission.
"""

from __future__ import annotations

import base64
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SEC = 45


class CosignVerifyError(Exception):
    """Raised when Cosign cannot positively verify."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def cosign_binary() -> str:
    configured = (os.environ.get("MODULE3_COSIGN_BINARY") or "").strip()
    if configured:
        return configured
    found = shutil.which("cosign")
    return found or "cosign"


def cosign_timeout_sec() -> int:
    raw = (os.environ.get("MODULE3_COSIGN_TIMEOUT_SEC") or "").strip()
    try:
        return max(5, min(int(raw), 300)) if raw else _DEFAULT_TIMEOUT_SEC
    except ValueError:
        return _DEFAULT_TIMEOUT_SEC


def verify_image_enabled() -> bool:
    return (os.environ.get("MODULE3_COSIGN_VERIFY_IMAGE") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _extra_args() -> list[str]:
    raw = (os.environ.get("MODULE3_COSIGN_EXTRA_ARGS") or "").strip()
    if not raw:
        return []
    return shlex.split(raw)


def resolve_public_key_material() -> tuple[str | None, str]:
    """
    Resolve Cosign public key PEM text.

    Accepts (first match wins):
      - MODULE3_COSIGN_PUBLIC_KEY_PATH
      - MODULE3_COSIGN_PUBLIC_KEY_FILE
      - MODULE3_COSIGN_PUBLIC_KEY (PEM body or filesystem path)
    """
    for env_name in ("MODULE3_COSIGN_PUBLIC_KEY_PATH", "MODULE3_COSIGN_PUBLIC_KEY_FILE"):
        path_env = (os.environ.get(env_name) or "").strip()
        if not path_env:
            continue
        p = Path(path_env.removeprefix("file://"))
        if not p.is_file():
            return None, f"{env_name} not found: {path_env}"
        return p.read_text(encoding="utf-8"), f"file:{path_env}"

    key = (os.environ.get("MODULE3_COSIGN_PUBLIC_KEY") or "").strip()
    if not key:
        return None, (
            "Cosign public key required in verify mode. "
            "Set MODULE3_COSIGN_PUBLIC_KEY (PEM), MODULE3_COSIGN_PUBLIC_KEY_PATH, "
            "or MODULE3_COSIGN_PUBLIC_KEY_FILE."
        )

    key = key.replace("\\n", "\n")
    if key.startswith("file://"):
        p = Path(key.removeprefix("file://"))
        if not p.is_file():
            return None, f"MODULE3_COSIGN_PUBLIC_KEY file:// not found: {key}"
        return p.read_text(encoding="utf-8"), f"file:{p}"

    if key.startswith("-----BEGIN") or "PUBLIC KEY" in key:
        return key, "env:pem"

    maybe_path = Path(key)
    if maybe_path.is_file():
        return maybe_path.read_text(encoding="utf-8"), f"file:{key}"

    if "BEGIN" in key.upper():
        return key, "env:pem"

    return None, (
        "MODULE3_COSIGN_PUBLIC_KEY is neither a PEM public key nor an existing file path."
    )


def _write_temp(content: str | bytes, *, suffix: str) -> Path:
    fd, name = tempfile.mkstemp(prefix="cosign-", suffix=suffix)
    path = Path(name)
    try:
        if isinstance(content, (bytes, bytearray)):
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                text = content if content.endswith("\n") else content + "\n"
                # PEM keys need trailing newline; blob payload hashing must not
                # add an extra newline — callers pass bytes for exact payloads.
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _ensure_cosign_binary() -> str:
    binary = cosign_binary()
    if not shutil.which(binary) and not Path(binary).is_file():
        raise CosignVerifyError(
            f"Cosign binary not found ({binary}). Install Cosign in the gateway image "
            "or set MODULE3_COSIGN_BINARY."
        )
    return binary


def _run_cosign(cmd: list[str], *, image_or_blob: str, key_source: str) -> dict:
    logger.info("Cosign cmd=%s key_source=%s target=%s", cmd[:3], key_source, image_or_blob)
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=cosign_timeout_sec(),
            check=False,
            env={**os.environ},
        )
    except subprocess.TimeoutExpired as exc:
        raise CosignVerifyError(
            f"Cosign timed out after {cosign_timeout_sec()}s for {image_or_blob}"
        ) from exc
    except OSError as exc:
        raise CosignVerifyError(f"Failed to execute Cosign: {exc}") from exc

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if completed.returncode == 0:
        return {
            "ok": True,
            "reason": "Cosign signature verified",
            "method": "cosign_cli",
            "key_source": key_source,
            "stdout": stdout[:2000],
            "stderr": stderr[:2000],
        }
    detail = stderr or stdout or f"cosign exited {completed.returncode}"
    short = detail.splitlines()[-1] if detail else "verification failed"
    return {
        "ok": False,
        "reason": f"Cosign verification failed: {short[:500]}",
        "method": "cosign_cli",
        "key_source": key_source,
        "stdout": stdout[:2000],
        "stderr": stderr[:2000],
    }


def _signature_bytes(signature_digest: str) -> bytes:
    """Decode Cosign detached signature from artifact field."""
    raw = (signature_digest or "").strip()
    if not raw:
        raise CosignVerifyError(
            "signature_digest is required for blob verify mode "
            "(detached Cosign signature). Or set MODULE3_COSIGN_VERIFY_IMAGE=1 "
            "to verify registry image signatures."
        )
    # Avoid treating explicit deny markers as signatures
    if raw in ("invalid",) or raw.startswith("invalid:"):
        raise CosignVerifyError("Cosign signature missing or invalid")

    lower = raw.lower()
    if lower.startswith("cosign-blob:"):
        raw = raw.split(":", 1)[1].strip()
    elif lower.startswith("blob:"):
        raw = raw.split(":", 1)[1].strip()

    # PEM-ish / raw file contents
    if "BEGIN" in raw.upper():
        return (raw + ("\n" if not raw.endswith("\n") else "")).encode("utf-8")

    # Hex-encoded signature blob
    if all(c in "0123456789abcdefABCDEF" for c in raw) and len(raw) % 2 == 0 and len(raw) >= 64:
        try:
            return bytes.fromhex(raw)
        except ValueError:
            pass

    # Base64 (standard Cosign signature file content is often raw binary; CI may
    # store base64 in the control plane).
    try:
        # Validate / decode; accept both standard and urlsafe
        pad = "=" * (-len(raw) % 4)
        return base64.b64decode(raw + pad, validate=False)
    except Exception as exc:
        raise CosignVerifyError(
            "signature_digest is not valid base64/hex Cosign signature material"
        ) from exc


def verify_image_with_cosign(image_ref: str) -> dict:
    """``cosign verify --key <pub> <image_ref>``."""
    image = (image_ref or "").strip()
    if not image:
        raise CosignVerifyError("image_ref is required for Cosign image verification")

    pem, source = resolve_public_key_material()
    if not pem:
        raise CosignVerifyError(source)

    binary = _ensure_cosign_binary()
    key_path: Path | None = None
    try:
        key_path = _write_temp(pem, suffix=".pub")
        cmd = [binary, "verify", "--key", str(key_path), *_extra_args(), image]
        return _run_cosign(cmd, image_or_blob=image, key_source=source)
    finally:
        if key_path is not None:
            key_path.unlink(missing_ok=True)


def verify_blob_with_cosign(*, payload_text: str, signature_digest: str) -> dict:
    """``cosign verify-blob --key <pub> --signature <sig.file> <payload.file>``."""
    blob = (payload_text or "").strip()
    if not blob:
        raise CosignVerifyError(
            "data_sha256 or model_sha256 is required as the signed payload for blob verify"
        )

    pem, source = resolve_public_key_material()
    if not pem:
        raise CosignVerifyError(source)

    binary = _ensure_cosign_binary()
    sig_bytes = _signature_bytes(signature_digest)

    key_path: Path | None = None
    payload_path: Path | None = None
    sig_path: Path | None = None
    try:
        key_path = _write_temp(pem, suffix=".pub")
        # Exact fingerprint bytes (no trailing newline) — CI must sign the same.
        payload_path = _write_temp(blob.encode("utf-8"), suffix=".payload")
        sig_path = _write_temp(sig_bytes, suffix=".sig")
        cmd = [
            binary,
            "verify-blob",
            "--key",
            str(key_path),
            "--signature",
            str(sig_path),
            *_extra_args(),
            str(payload_path),
        ]
        return _run_cosign(cmd, image_or_blob=f"blob:{blob[:16]}…", key_source=source)
    finally:
        for p in (key_path, payload_path, sig_path):
            if p is not None:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    logger.debug("temp cleanup failed", exc_info=True)


def verify_with_cosign(
    *,
    image_ref: str = "",
    signature_digest: str = "",
    data_sha256: str = "",
    model_sha256: str = "",
) -> dict:
    """Dispatch image vs blob Cosign verification based on env."""
    if verify_image_enabled():
        return verify_image_with_cosign(image_ref)

    # Prefer model fingerprint, then data fingerprint as signed payload.
    payload = (model_sha256 or data_sha256 or "").strip().lower().removeprefix("sha256:")
    return verify_blob_with_cosign(payload_text=payload, signature_digest=signature_digest)
