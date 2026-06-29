"""Unit tests for admission_controller."""

import os

import pytest

from admission_controller import verify_admission_payload


@pytest.fixture(autouse=True)
def _passthrough_mode(monkeypatch):
    monkeypatch.setenv("MODULE3_ADMISSION_MODE", "passthrough")


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


def test_requires_image_ref():
    result = verify_admission_payload({"signature_digest": "sha256:x"})
    assert result["allowed"] is False


def test_verify_mode_requires_digest(monkeypatch):
    monkeypatch.setenv("MODULE3_ADMISSION_MODE", "verify")
    result = verify_admission_payload(
        {
            "image_ref": "registry.example.com/m:1",
            "signature_digest": "",
            "data_sha256": "a" * 64,
        }
    )
    assert result["allowed"] is False
