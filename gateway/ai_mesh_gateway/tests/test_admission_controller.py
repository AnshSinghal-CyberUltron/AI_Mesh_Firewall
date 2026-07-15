"""Unit tests for admission_controller + Cosign production path."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from admission_controller import verify_admission_payload
from cosign_verify import resolve_public_key_material, verify_with_cosign


@pytest.fixture(autouse=True)
def _passthrough_mode(monkeypatch):
    monkeypatch.setenv("MODULE3_ADMISSION_MODE", "passthrough")
    monkeypatch.delenv("MODULE3_COSIGN_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("MODULE3_COSIGN_PUBLIC_KEY_PATH", raising=False)
    monkeypatch.delenv("MODULE3_COSIGN_PUBLIC_KEY_FILE", raising=False)
    monkeypatch.setenv("MODULE3_COSIGN_VERIFY_IMAGE", "0")


def test_denies_invalid_signature():
    result = verify_admission_payload(
        {
            "image_ref": "registry.example.com/m:1",
            "signature_digest": "invalid:unsigned",
            "data_sha256": "a" * 64,
        }
    )
    assert result["allowed"] is False
    assert "invalid" in result["reason"].lower()


def test_allows_valid_passthrough():
    result = verify_admission_payload(
        {
            "image_ref": "registry.example.com/m:1",
            "signature_digest": "sha256:abc123",
            "data_sha256": "a" * 64,
            "model_sha256": "b" * 64,
        }
    )
    assert result["allowed"] is True
    assert result["verification_method"] == "passthrough"


def test_requires_image_ref():
    result = verify_admission_payload({"signature_digest": "sha256:x"})
    assert result["allowed"] is False


def test_verify_mode_requires_public_key(monkeypatch):
    monkeypatch.setenv("MODULE3_ADMISSION_MODE", "verify")
    result = verify_admission_payload(
        {
            "image_ref": "registry.example.com/m:1",
            "signature_digest": "dGVzdA==",
            "model_sha256": "a" * 64,
        }
    )
    assert result["allowed"] is False
    assert "public key" in result["reason"].lower() or "cosign" in result["reason"].lower()


def test_verify_mode_explicit_invalid_skips_cosign(monkeypatch):
    monkeypatch.setenv("MODULE3_ADMISSION_MODE", "verify")
    monkeypatch.setenv(
        "MODULE3_COSIGN_PUBLIC_KEY",
        "-----BEGIN PUBLIC KEY-----\nMIIB\n-----END PUBLIC KEY-----",
    )
    with patch("cosign_verify.verify_with_cosign") as mock_v:
        result = verify_admission_payload(
            {
                "image_ref": "registry.example.com/m:1",
                "signature_digest": "invalid:missing-cosign",
                "model_sha256": "a" * 64,
            }
        )
    assert result["allowed"] is False
    mock_v.assert_not_called()


def test_verify_mode_calls_cosign_and_allows(monkeypatch):
    monkeypatch.setenv("MODULE3_ADMISSION_MODE", "verify")
    monkeypatch.setenv(
        "MODULE3_COSIGN_PUBLIC_KEY",
        "-----BEGIN PUBLIC KEY-----\nMIIB\n-----END PUBLIC KEY-----",
    )
    with patch("cosign_verify.verify_with_cosign") as mock_v:
        mock_v.return_value = {
            "ok": True,
            "reason": "Cosign signature verified",
            "method": "cosign_cli",
            "key_source": "env:pem",
        }
        result = verify_admission_payload(
            {
                "image_ref": "registry.example.com/m:1",
                "signature_digest": "ZGVtbw==",
                "model_sha256": "a" * 64,
            }
        )
    assert result["allowed"] is True
    assert "verified" in result["reason"].lower()
    mock_v.assert_called_once()


def test_verify_mode_cosign_deny(monkeypatch):
    monkeypatch.setenv("MODULE3_ADMISSION_MODE", "verify")
    monkeypatch.setenv(
        "MODULE3_COSIGN_PUBLIC_KEY",
        "-----BEGIN PUBLIC KEY-----\nMIIB\n-----END PUBLIC KEY-----",
    )
    with patch("cosign_verify.verify_with_cosign") as mock_v:
        mock_v.return_value = {
            "ok": False,
            "reason": "Cosign verification failed: no matching signatures",
            "method": "cosign_cli",
            "stderr": "no matching signatures",
        }
        result = verify_admission_payload(
            {
                "image_ref": "registry.example.com/m:1",
                "signature_digest": "ZGVtbw==",
                "model_sha256": "a" * 64,
            }
        )
    assert result["allowed"] is False
    assert "failed" in result["reason"].lower()


def test_resolve_public_key_from_file(tmp_path: Path, monkeypatch):
    pub = tmp_path / "cosign.pub"
    pub.write_text("-----BEGIN PUBLIC KEY-----\nABC\n-----END PUBLIC KEY-----\n", encoding="utf-8")
    monkeypatch.setenv("MODULE3_COSIGN_PUBLIC_KEY_FILE", str(pub))
    pem, source = resolve_public_key_material()
    assert pem is not None
    assert "BEGIN" in pem
    assert source.startswith("file:")


def test_verify_with_cosign_image_mode_invokes_cli(monkeypatch, tmp_path: Path):
    pub = tmp_path / "cosign.pub"
    pub.write_text("-----BEGIN PUBLIC KEY-----\nABC\n-----END PUBLIC KEY-----\n", encoding="utf-8")
    monkeypatch.setenv("MODULE3_COSIGN_PUBLIC_KEY_PATH", str(pub))
    monkeypatch.setenv("MODULE3_COSIGN_VERIFY_IMAGE", "1")
    fake_bin = tmp_path / "cosign-bin"
    fake_bin.write_text("noop", encoding="utf-8")
    monkeypatch.setenv("MODULE3_COSIGN_BINARY", str(fake_bin))

    completed = MagicMock(returncode=0, stdout="verified", stderr="")
    with patch("cosign_verify.subprocess.run", return_value=completed) as run:
        out = verify_with_cosign(image_ref="ghcr.io/org/model:1.0")
    assert out["ok"] is True
    args = run.call_args[0][0]
    assert args[0] == str(fake_bin)
    assert args[1] == "verify"
    assert "ghcr.io/org/model:1.0" in args


def test_verify_with_cosign_blob_mode_invokes_verify_blob(monkeypatch, tmp_path: Path):
    pub = tmp_path / "cosign.pub"
    pub.write_text("-----BEGIN PUBLIC KEY-----\nABC\n-----END PUBLIC KEY-----\n", encoding="utf-8")
    monkeypatch.setenv("MODULE3_COSIGN_PUBLIC_KEY_FILE", str(pub))
    monkeypatch.setenv("MODULE3_COSIGN_VERIFY_IMAGE", "0")
    fake_bin = tmp_path / "cosign-bin"
    fake_bin.write_text("noop", encoding="utf-8")
    monkeypatch.setenv("MODULE3_COSIGN_BINARY", str(fake_bin))

    completed = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("cosign_verify.subprocess.run", return_value=completed) as run:
        out = verify_with_cosign(
            image_ref="ignored/for/blob:1",
            model_sha256="a" * 64,
            signature_digest="ZGVtby1zaWduYXR1cmU=",
        )
    assert out["ok"] is True
    args = run.call_args[0][0]
    assert "verify-blob" in args


def test_signature_digest_accepts_cosign_blob_prefix(monkeypatch, tmp_path: Path):
    from cosign_verify import _signature_bytes

    raw = _signature_bytes("cosign-blob:ZGVtbw==")
    assert raw == b"demo"
