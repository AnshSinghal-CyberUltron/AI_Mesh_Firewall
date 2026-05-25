"""
HMAC-SHA256 signing/verification for compiled policy bundles.

Backend (signer) attaches `_sig` (and `_sig_alg`, `_sig_at`) to the bundle
BEFORE storing it in Redis. The Gateway (verifier) refuses to load any
bundle whose signature does not verify with the shared key. This prevents
a malicious Redis writer from injecting a benign-looking policy set that
silently disables enforcement.

Shared key source: env `POLICY_SIGNING_KEY`. Must be set identically on
backend and gateway. In dev, fall back to a deterministic key derived
from DJANGO_SECRET_KEY so local stacks "just work" without extra config.

Wire format:
    bundle = {
        "version": 42,
        "policy_count": 7,
        "policies": [...],
        ...
        "_sig_alg": "HMAC-SHA256",
        "_sig_at": 1719500000.123,
        "_sig": "hex-encoded-mac",
    }

The MAC covers the canonical JSON of the bundle with `_sig` removed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any

LOG = logging.getLogger(__name__)

SIG_FIELD = "_sig"
SIG_ALG_FIELD = "_sig_alg"
SIG_AT_FIELD = "_sig_at"
SIG_ALG = "HMAC-SHA256"


def _get_signing_key() -> bytes:
    """Return the HMAC key as bytes.

    Resolution order:
        1. POLICY_SIGNING_KEY env var (preferred — set explicitly in prod)
        2. Derived from DJANGO_SECRET_KEY (dev fallback only)

    Raises:
        RuntimeError if no key material is available at all (refuse to sign).
    """
    raw = os.environ.get("POLICY_SIGNING_KEY", "").strip()
    if raw:
        return raw.encode("utf-8")

    # Dev fallback: derive deterministically from Django secret so backend
    # and gateway (which both read the same .env) match without extra config.
    fallback = os.environ.get("DJANGO_SECRET_KEY", "").strip()
    if fallback:
        return hashlib.sha256(b"policy-bundle-signing:" + fallback.encode("utf-8")).digest()

    raise RuntimeError(
        "No POLICY_SIGNING_KEY (and no DJANGO_SECRET_KEY fallback) configured. "
        "Refusing to sign policy bundle."
    )


def _canonical_payload(bundle: dict[str, Any]) -> bytes:
    """Return the bytes that the MAC covers: bundle with `_sig` removed."""
    stripped = {k: v for k, v in bundle.items() if k != SIG_FIELD}
    # sort_keys=True for canonical ordering; default=str for datetimes etc.
    return json.dumps(stripped, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")


def sign_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Attach a signature to `bundle` in place and return it.

    Safe to call on a bundle that already has `_sig` — the old one is
    stripped before computing the new MAC.
    """
    bundle.setdefault(SIG_ALG_FIELD, SIG_ALG)
    bundle[SIG_ALG_FIELD] = SIG_ALG
    bundle[SIG_AT_FIELD] = time.time()
    # Strip any prior signature before computing the new one
    bundle.pop(SIG_FIELD, None)

    key = _get_signing_key()
    mac = hmac.new(key, _canonical_payload(bundle), hashlib.sha256).hexdigest()
    bundle[SIG_FIELD] = mac
    return bundle


def verify_bundle(bundle: dict[str, Any]) -> bool:
    """Return True iff the bundle carries a valid HMAC-SHA256 signature.

    Returns False (rather than raising) on any structural problem so that
    callers can fail closed without leaking implementation detail.
    """
    if not isinstance(bundle, dict):
        return False

    provided = bundle.get(SIG_FIELD)
    if not isinstance(provided, str) or not provided:
        return False

    if bundle.get(SIG_ALG_FIELD) != SIG_ALG:
        # Unknown alg — refuse rather than silently downgrade
        return False

    try:
        key = _get_signing_key()
    except RuntimeError:
        LOG.error("verify_bundle: no signing key available; refusing")
        return False

    expected = hmac.new(key, _canonical_payload(bundle), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)
