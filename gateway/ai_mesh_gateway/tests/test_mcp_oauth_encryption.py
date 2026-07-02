"""CHG-0042: at-rest encryption for OAuth flow state + tokens.

The helpers are pure (no Redis) — they gate on MCP_OAUTH_ENCRYPTION_KEY. Default
OFF = plaintext (unchanged); enabling the key encrypts new writes while still
reading legacy plaintext (Fernet tokens are prefix-detectable).
"""
from __future__ import annotations

import sys
from pathlib import Path

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))

from cryptography.fernet import Fernet  # noqa: E402

import mcp_oauth_proxy as oauth  # noqa: E402


def test_default_off_is_plaintext(monkeypatch):
    monkeypatch.delenv("MCP_OAUTH_ENCRYPTION_KEY", raising=False)
    assert oauth._oauth_cipher() is None
    blob = oauth._enc_dumps({"access_token": "tok-abc"})
    assert blob == '{"access_token": "tok-abc"}'          # plaintext, byte-unchanged
    assert oauth._enc_loads(blob) == {"access_token": "tok-abc"}


def test_round_trip_encrypted(monkeypatch):
    monkeypatch.setenv("MCP_OAUTH_ENCRYPTION_KEY", Fernet.generate_key().decode())
    # Long, distinctive secret values so a stray base64 substring can't false-match.
    data = {
        "access_token": "tok-ACCESS-9f8a7b6c5d4e3f2a1b",
        "refresh_token": "ref-REFRESH-0011223344556677",
        "client_secret": "csec-CLIENTSECRET-deadbeefcafe",
    }
    blob = oauth._enc_dumps(data)
    assert blob.startswith("gAAAAA")                      # Fernet ciphertext, not plaintext
    for secret in data.values():
        assert secret not in blob                         # no plaintext secret survives
    assert oauth._enc_loads(blob) == data                 # decrypts back exactly


def test_reads_legacy_plaintext_after_key_enabled(monkeypatch):
    # A value written BEFORE the key was configured is plaintext JSON; enabling the
    # key must still read it (prefix-detected as non-Fernet), so no token is orphaned.
    monkeypatch.setenv("MCP_OAUTH_ENCRYPTION_KEY", Fernet.generate_key().decode())
    legacy = '{"access_token": "old-plain"}'
    assert oauth._enc_loads(legacy) == {"access_token": "old-plain"}


def test_invalid_key_falls_back_to_plaintext(monkeypatch):
    monkeypatch.setenv("MCP_OAUTH_ENCRYPTION_KEY", "not-a-valid-fernet-key")
    assert oauth._oauth_cipher() is None                  # invalid key -> None (never breaks)
    assert oauth._enc_dumps({"a": 1}) == '{"a": 1}'       # plaintext fallback


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
