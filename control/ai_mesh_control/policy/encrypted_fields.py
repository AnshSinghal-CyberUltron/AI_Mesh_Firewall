"""
Bundle X3 — at-rest encryption for sensitive model fields.

Background
----------
``VectorProviderConfig.api_key`` previously stored provider credentials
(Pinecone keys, Milvus tokens, custom-provider tokens) as cleartext in
PostgreSQL despite a misleading ``help_text="Stored encrypted at rest"``.
Anyone with read access to the database (DBA, leaked backup, SQL
injection through a separate flaw) could exfiltrate every customer's
key. This module provides :class:`EncryptedCharField`, a drop-in
replacement that transparently encrypts on write and decrypts on read
using Fernet (AES-128 in CBC + HMAC-SHA256).

Key derivation
--------------
The Fernet key is derived deterministically from
``settings.FIELD_ENCRYPTION_KEY`` (preferred) or ``settings.SECRET_KEY``
(fallback for dev) via HKDF-SHA256. Rotating ``FIELD_ENCRYPTION_KEY``
*will* invalidate existing ciphertexts, so set it explicitly in
production and rotate via the standard re-encrypt migration pattern.

On-disk format
--------------
Encrypted rows are stored as ``"enc:<base64 fernet token>"``. The
``enc:`` prefix lets the descriptor distinguish ciphertext from legacy
plaintext written before this field type was introduced, so the data
migration in ``0028_encrypt_vector_provider_api_key`` can re-encrypt
in place without a separate column swap.
"""

from __future__ import annotations

import base64
import logging

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models

logger = logging.getLogger(__name__)

_CIPHERTEXT_PREFIX = "enc:"
_HKDF_INFO = b"ai_mesh.policy.encrypted_fields.v1"
_HKDF_SALT = b"ai_mesh.policy.encrypted_fields.salt.v1"


def _derive_fernet_key() -> bytes:
    """
    Return a 32-byte base64-encoded Fernet key derived from settings.
    Prefers ``FIELD_ENCRYPTION_KEY`` (operator-rotatable); falls back to
    ``SECRET_KEY`` so existing dev/test environments do not require
    additional configuration.
    """
    raw = (
        getattr(settings, "FIELD_ENCRYPTION_KEY", None)
        or settings.SECRET_KEY
    )
    if not raw:
        raise RuntimeError(
            "EncryptedCharField requires FIELD_ENCRYPTION_KEY or "
            "SECRET_KEY to be set."
        )
    if isinstance(raw, str):
        raw_bytes = raw.encode("utf-8")
    else:
        raw_bytes = bytes(raw)

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    )
    derived = hkdf.derive(raw_bytes)
    return base64.urlsafe_b64encode(derived)


_FERNET: Fernet | None = None


def _get_fernet() -> Fernet:
    """Lazy-cached Fernet instance keyed off derived settings."""
    global _FERNET
    if _FERNET is None:
        _FERNET = Fernet(_derive_fernet_key())
    return _FERNET


def encrypt_value(plaintext: str) -> str:
    """Encrypt ``plaintext`` and return the ``enc:`` prefixed token."""
    if plaintext is None or plaintext == "":
        return ""
    token = _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"{_CIPHERTEXT_PREFIX}{token}"


def decrypt_value(stored: str) -> str:
    """
    Inverse of :func:`encrypt_value`. Tolerates legacy plaintext rows
    written before this field type existed by returning them unchanged.
    """
    if not stored:
        return ""
    if not stored.startswith(_CIPHERTEXT_PREFIX):
        # Legacy plaintext row — return as-is. The data migration will
        # eventually rewrite it on the next save() or via a one-shot
        # re-encryption pass.
        return stored
    token = stored[len(_CIPHERTEXT_PREFIX):]
    try:
        return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        logger.error(
            "EncryptedCharField: failed to decrypt value — "
            "FIELD_ENCRYPTION_KEY may have rotated without a re-encrypt "
            "migration. Returning empty string to avoid surfacing "
            "ciphertext to callers."
        )
        return ""


class EncryptedCharField(models.CharField):
    """
    CharField subclass that transparently encrypts at rest.

    Storage format: ``"enc:<token>"`` (see module docstring). The DB
    column type is unchanged so this is a zero-downtime swap from
    ``CharField`` provided that ``max_length`` is large enough to hold
    the ciphertext (Fernet tokens are ~1.4× the plaintext length plus
    ~57 bytes of envelope; the existing 512-char column comfortably
    fits keys up to ~320 chars of plaintext).
    """

    description = "CharField encrypted at rest via Fernet"

    def from_db_value(self, value, expression, connection):  # noqa: D401
        if value is None:
            return value
        return decrypt_value(value)

    def to_python(self, value):
        if value is None:
            return value
        if isinstance(value, str):
            return decrypt_value(value) if value.startswith(_CIPHERTEXT_PREFIX) else value
        return value

    def get_prep_value(self, value):
        if value is None:
            return value
        # Re-encrypting an already-ciphertext value would double-wrap, so
        # short-circuit when callers pass us a stored representation.
        if isinstance(value, str) and value.startswith(_CIPHERTEXT_PREFIX):
            return value
        return encrypt_value(value or "")


__all__ = [
    "EncryptedCharField",
    "decrypt_value",
    "encrypt_value",
]
