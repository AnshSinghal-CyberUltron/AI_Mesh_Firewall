"""Fernet helpers for LLM provider API keys shared by control plane and gateway."""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

LLM_MODEL_KEY_ENCRYPTION_ENV = "ZEROSHIELD_MODEL_KEY_ENCRYPTION_KEY"


def build_llm_model_key_cipher(
    *,
    configured_key: str = "",
    fallback_secret: str = "",
) -> Fernet:
    """
    Build a Fernet cipher for model provider credentials.

    Priority:
    1) ``ZEROSHIELD_MODEL_KEY_ENCRYPTION_KEY`` (shared across control/gateway)
    2) deterministic derivation from fallback_secret (Django SECRET_KEY in control)
    """
    configured = (configured_key or os.environ.get(LLM_MODEL_KEY_ENCRYPTION_ENV, "") or "").strip()
    if configured:
        try:
            return Fernet(configured.encode("utf-8"))
        except Exception:
            derived = base64.urlsafe_b64encode(hashlib.sha256(configured.encode("utf-8")).digest())
            return Fernet(derived)

    fallback_seed = (fallback_secret or "zeroshield-model-keys").strip()
    fallback_key = base64.urlsafe_b64encode(hashlib.sha256(fallback_seed.encode("utf-8")).digest())
    return Fernet(fallback_key)


def encrypt_api_key(
    raw_key: str,
    *,
    configured_key: str = "",
    fallback_secret: str = "",
) -> str:
    key = (raw_key or "").strip()
    if not key:
        return ""
    cipher = build_llm_model_key_cipher(
        configured_key=configured_key,
        fallback_secret=fallback_secret,
    )
    return cipher.encrypt(key.encode("utf-8")).decode("utf-8")


def decrypt_api_key(
    encrypted: str,
    *,
    configured_key: str = "",
    fallback_secret: str = "",
) -> str:
    if not encrypted:
        return ""
    cipher = build_llm_model_key_cipher(
        configured_key=configured_key,
        fallback_secret=fallback_secret,
    )
    try:
        return cipher.decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""
    except Exception:
        return ""
