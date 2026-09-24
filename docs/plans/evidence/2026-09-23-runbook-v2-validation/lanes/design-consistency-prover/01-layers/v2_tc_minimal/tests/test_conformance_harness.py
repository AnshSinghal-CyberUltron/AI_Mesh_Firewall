"""Default-suite lock: env resolver stays tiny and rejects unknown apps."""

from __future__ import annotations

import pytest

from gateway_v2.contracts.openai_conformance.harness import (
    conformance_app_name,
    is_tcp_mode,
    tcp_base_url,
)


def test_default_app_is_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AMF_CONFORMANCE_APP", raising=False)
    monkeypatch.delenv("AMF_CONFORMANCE_BASE_URL", raising=False)
    assert conformance_app_name() == "v1"
    assert tcp_base_url() is None
    assert is_tcp_mode() is False


def test_unknown_app_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMF_CONFORMANCE_APP", "v3")
    with pytest.raises(RuntimeError, match="AMF_CONFORMANCE_APP"):
        conformance_app_name()
