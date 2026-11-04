"""GW31 domain value types and codes."""
from __future__ import annotations

import dataclasses

import pytest

from gateway_v2.domain import posture
from gateway_v2.domain.identity import Principal
from gateway_v2.domain.rag import RagSettings, TrustLevel
from gateway_v2.edge import errors


def test_settings_defaults_leave_numeric_to_contract() -> None:
    s = RagSettings()
    assert s.rag_enabled and s.rag_redaction_enabled and s.rag_tier2_enabled
    assert s.rag_max_documents is None and s.rag_relevance_threshold is None
    assert s.vector_db_isolation == "project"


def test_settings_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        RagSettings().rag_enabled = False  # type: ignore[misc]


def test_principal_project_defaults() -> None:
    assert Principal("k", "o", 1.0, 1.0, 0).project == "default"


def test_trust_order() -> None:
    assert TrustLevel.UNTRUSTED < TrustLevel.LOW < TrustLevel.MEDIUM < TrustLevel.HIGH


@pytest.mark.parametrize(
    "code",
    [v for k, v in vars(posture).items() if k.startswith("RAG_") or k == "PROJECT_INVALID"],
)
def test_every_rag_code_is_mapped(code: str) -> None:
    assert code in errors._ENVELOPE
