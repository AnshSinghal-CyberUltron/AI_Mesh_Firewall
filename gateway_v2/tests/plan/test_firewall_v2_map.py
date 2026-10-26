"""The 65 FirewallConfig fields are mapped or rejected by name, and a supported set compiles."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from gateway_v2.domain.category import Category
from gateway_v2.domain.plan import Action, FailurePosture, Mode, RuleScope, StreamingMode, Surface
from gateway_v2.plan.compiler import RuleDraft, compile_plan

_MAP = (
    Path(__file__).resolve().parents[3]
    / "control"
    / "ai_mesh_control"
    / "core"
    / "firewall_v2_map.py"
)
_INVENTORY = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "plans"
    / "evidence"
    / "2026-10-26-gw14b"
    / "config-keys"
    / "firewallconfig.json"
)


def test_the_map_covers_every_exposed_field() -> None:
    exposed = json.loads(_INVENTORY.read_text(encoding="utf-8"))["exposed_fields"]
    assert set(_load().FIELDS) == set(exposed)
    assert len(exposed) == 65


def test_turning_rag_on_names_the_field() -> None:
    errors = _load().selection_errors({"rag_enabled": True}, {"rag_enabled": False})
    assert set(errors) == {"rag_enabled"}
    assert "rag_enabled" in errors["rag_enabled"]


def test_resubmitting_the_stored_rag_value_is_not_an_error() -> None:
    errors = _load().selection_errors(
        {"rag_enabled": True, "pii_detection_enabled": False},
        {"rag_enabled": True, "pii_detection_enabled": True},
    )
    assert errors == {}


def test_audit_mode_and_rewrite_are_named() -> None:
    errors = _load().selection_errors(
        {"enforcement_mode": "audit", "output_pii_action": "rewrite"},
        {"enforcement_mode": "block", "output_pii_action": "redact"},
    )
    assert set(errors) == {"enforcement_mode", "output_pii_action"}


def test_a_supported_payload_compiles_and_omits_rag() -> None:
    module = _load()
    drafts = module.drafts_for(_supported())
    assert drafts
    assert all("rag" not in row["rule_id"] for row in drafts)
    plan = compile_plan("org", tuple(_rule(row) for row in drafts), compiled_at=1.0)
    assert plan.version.startswith("1.1.")
    assert plan.streaming_mode is StreamingMode.INCREMENTAL


def _supported() -> dict[str, object]:
    return {
        "firewall_enabled": True,
        "enforcement_mode": "block",
        "content_filtering_enabled": True,
        "pii_detection_enabled": True,
        "jailbreak_detection_enabled": True,
        "prompt_injection_threshold": 0.8,
        "toxicity_threshold": 0.7,
        "tier2_fail_closed_enabled": True,
        "response_filtering_enabled": True,
        "output_pii_enabled": True,
        "output_pii_action": "redact",
        "output_credential_enabled": True,
        "output_credential_action": "block",
        "output_ip_leakage_enabled": True,
        "output_ip_leakage_action": "flag",
    }


def _rule(row: dict[str, object]) -> RuleDraft:
    surfaces = row["surfaces"]
    assert isinstance(surfaces, list)
    threshold = row["threshold"]
    return RuleDraft(
        rule_id=str(row["rule_id"]),
        category=Category(str(row["category"])),
        mode=Mode(str(row["mode"])),
        action=Action(str(row["action"])),
        threshold=float(threshold) if isinstance(threshold, (int, float)) else None,
        priority=100,
        scope=RuleScope(str(row["scope"])),
        on_unavailable=FailurePosture(str(row["on_unavailable"])),
        surfaces=frozenset(Surface(str(item)) for item in surfaces),
    )


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("firewall_v2_map", _MAP)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
