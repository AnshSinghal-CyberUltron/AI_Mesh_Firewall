"""
Gateway-side verifier for compiled policy bundles.

MUST stay byte-compatible with backend/policy/signing.py — same canonical
JSON (sort_keys=True, default=str, separators=(",", ":")), same MAC field
names, same algorithm. We duplicate the small verify function here rather
than importing from backend to keep the gateway Django-free.

Fail-closed: if verification fails for any reason the caller MUST discard
the bundle and continue serving the previous (already-verified) cache, or
serve no policies at all if nothing was ever loaded.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

LOG = logging.getLogger("gateway.policy_signing")

SIG_FIELD = "_sig"
SIG_ALG_FIELD = "_sig_alg"
SIG_ALG = "HMAC-SHA256"


def _get_signing_key() -> bytes | None:
    raw = os.environ.get("POLICY_SIGNING_KEY", "").strip()
    if raw:
        return raw.encode("utf-8")
    fallback = os.environ.get("DJANGO_SECRET_KEY", "").strip()
    if fallback:
        return hashlib.sha256(b"policy-bundle-signing:" + fallback.encode("utf-8")).digest()
    return None


def _canonical_payload(bundle: dict[str, Any]) -> bytes:
    stripped = {k: v for k, v in bundle.items() if k != SIG_FIELD}
    return json.dumps(stripped, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")


def verify_bundle(bundle: dict[str, Any]) -> bool:
    """Return True iff `bundle` carries a valid HMAC-SHA256 signature."""
    if not isinstance(bundle, dict):
        return False

    provided = bundle.get(SIG_FIELD)
    if not isinstance(provided, str) or not provided:
        return False
    if bundle.get(SIG_ALG_FIELD) != SIG_ALG:
        return False

    key = _get_signing_key()
    if key is None:
        LOG.error("verify_bundle: no POLICY_SIGNING_KEY available; cannot verify")
        return False

    expected = hmac.new(key, _canonical_payload(bundle), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


def signing_enforced() -> bool:
    """Return True iff bundle signatures should be ENFORCED (reject unsigned).

    Default: True in production-shaped environments, configurable via
    GATEWAY_POLICY_SIGNING_REQUIRED. Set to "false" only for the brief
    rolling-cutover window where unsigned legacy bundles must still be
    accepted while the backend deploys the signer.
    """
    val = os.environ.get("GATEWAY_POLICY_SIGNING_REQUIRED", "true").strip().lower()
    return val in ("1", "true", "yes", "on")
