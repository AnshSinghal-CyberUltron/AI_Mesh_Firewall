"""ReDoS guard must not reject safe PIPE catalog regex shorthands."""

from __future__ import annotations

import re

import pytest

from ai_mesh_gateway.policy_engine import _compile_regex, _has_redos_shape, evaluate


@pytest.mark.parametrize(
    "pattern",
    [
        r"\b(?:MRN|medical\s+record)\s*#?\s*\d{6,10}\b",
        r"\b(?:NPI|DEA)\s*#?\s*\d{7,10}\b",
        r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b",
        r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
    ],
)
def test_safe_catalog_patterns_compile(pattern: str) -> None:
    assert not _has_redos_shape(pattern), f"false ReDoS positive: {pattern!r}"
    _compile_regex(pattern)


def test_phi_mrn_policy_redacts_in_evaluate() -> None:
    compiled = [
        {
            "policy": {
                "id": 2,
                "code": "PKG2_PIPE_PHI",
                "name": "PHI / Health Data",
                "category": "phi",
                "policy_domain": "pipeline",
            },
            "rules": [
                {
                    "id": 1,
                    "name": "Redact medical record numbers",
                    "rule_type": "regex",
                    "action": "redact",
                    "condition": {
                        "field": "both",
                        "regex": r"\b(?:MRN|medical\s+record)\s*#?\s*\d{6,10}\b",
                    },
                    "redaction_config": {"replacement": "[REDACTED_MRN]"},
                },
            ],
        }
    ]
    result = evaluate(
        "Patient MRN 4421901 diagnosed with diabetes",
        "",
        compiled_policies=compiled,
    )
    assert result.action == "redact"
    assert "Redact medical record numbers" in result.matched_rule_names


def test_nested_quantifier_still_rejected() -> None:
    assert _has_redos_shape(r"(a+)+")
    with pytest.raises(re.error):
        _compile_regex(r"(a+)+")
