#!/usr/bin/env python3
"""Phase 3 UNIT matrix: enforce_output × OutputGuard.inspect (no live FirewallConfig)."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW = ROOT / "gateway" / "ai_mesh_gateway"
sys.path.insert(0, str(GW))

from enforcement import (  # noqa: E402
    _REDACTABLE_OUTPUT_THREAT_TYPES,
    enforce_output,
)
from output_guard import OutputGuard  # noqa: E402
from patterns import (  # noqa: E402
    detect_credential_exposure,
    detect_hallucination_markers,
    detect_ip_leakage,
    detect_pii,
)
from scanner import InputScanner  # noqa: E402

OUT = Path(__file__).resolve().parent / "config-action-matrix-unit.json"

UI_ACTIONS = ["allow", "flag", "redact", "rewrite", "block"]

# User-specified canaries (+ notes where detector wiring differs).
CANARIES = {
    "pii": "Contact john.doe@corp.example",
    "credential/secret": "key AKIAIOSFODNN7EXAMPLE",
    "ip_leakage": "host 10.0.0.5 and 169.254.169.254",
    "hallucination": (
        "According to Dr. Smith et al., research shows that approximately 47% of "
        "patients improve. The University of Atlantis Research confirmed this. "
        "As of 2019 the agency will expand coverage worldwide."
    ),
}

# AKIA is detect_pii(aws_access_key), NOT detect_credential_exposure.
# For credential-only inspect, use a true CREDENTIAL_EXPOSURE canary as well.
CREDENTIAL_INSPECT_CANARY = "postgresql://admin:S3cr3tPass@db.internal:5432/app"

THREAT_META = {
    "pii": {
        "enforce_threat_types": ["pii"],
        "enable_keys": {
            "output_pii_enabled": True,
            "output_credential_enabled": False,
            "output_ip_leakage_enabled": False,
            "hallucination_flag_enabled": False,
            "output_exfil_enabled": False,
            "output_tier2_enabled": False,
        },
        "action_key": "output_pii_action",
        "canary_key": "pii",
    },
    "credential/secret": {
        # enforce_output: both credential and secret are redactable categories
        "enforce_threat_types": ["credential", "secret"],
        "enable_keys": {
            "output_pii_enabled": False,
            "output_credential_enabled": True,
            "output_ip_leakage_enabled": False,
            "hallucination_flag_enabled": False,
            "output_exfil_enabled": False,
            "output_tier2_enabled": False,
        },
        "action_key": "output_credential_action",
        "canary_key": "credential/secret",
        "inspect_canary_override": CREDENTIAL_INSPECT_CANARY,
        "inspect_canary_note": (
            "User canary 'key AKIAIOSFODNN7EXAMPLE' hits detect_pii(aws_access_key), "
            "not detect_credential_exposure; inspect uses connection_string canary for "
            "credential detector. Separate pii-path note recorded for AKIA."
        ),
    },
    "ip_leakage": {
        "enforce_threat_types": ["ip_leakage"],
        "enable_keys": {
            "output_pii_enabled": False,
            "output_credential_enabled": False,
            "output_ip_leakage_enabled": True,
            "hallucination_flag_enabled": False,
            "output_exfil_enabled": False,
            "output_tier2_enabled": False,
        },
        "action_key": "output_ip_leakage_action",
        "canary_key": "ip_leakage",
    },
    "hallucination": {
        "enforce_threat_types": ["hallucination"],
        "enable_keys": {
            "output_pii_enabled": False,
            "output_credential_enabled": False,
            "output_ip_leakage_enabled": False,
            "hallucination_flag_enabled": True,
            "output_exfil_enabled": False,
            "output_tier2_enabled": False,
        },
        "action_key": "output_hallucination_action",
        "canary_key": "hallucination",
    },
}

KNOWN_COERCIONS = [
    {
        "id": "rewrite+stream→block",
        "rule": "enforce_output: rewrite + is_streaming=True → block",
    },
    {
        "id": "flag+enforcement_mode block→block",
        "rule": "enforce_output: flag + enforcement_mode='block' → block",
    },
    {
        "id": "IP flag/rewrite→redact inside inspect",
        "rule": "OutputGuard.inspect: ip_action in (rewrite,flag) coerced to redact",
    },
    {
        "id": "maskable block→redact",
        "rule": (
            "enforce_output: block + threat in {pii,phi,pci,secret,credential} → redact; "
            "inspect also downgrades block→redact for _REDACTABLE_OUTPUT_CATEGORIES"
        ),
    },
    {
        "id": "IP block without output_block_on_ip_leakage→redact",
        "rule": (
            "OutputGuard.inspect: ip block softened to redact unless "
            "OutputGuard._config.output_block_on_ip_leakage=True"
        ),
    },
]


def _expected_enforce(ui_action: str, threat: str, *, is_streaming: bool) -> str:
    """Mirror enforce_output coercions for mismatch annotation (not a second authority)."""
    act = ui_action
    if act == "block" and threat in _REDACTABLE_OUTPUT_THREAT_TYPES:
        act = "redact"
    if act == "rewrite" and is_streaming:
        act = "block"
    elif act == "flag":  # enforcement_mode always 'block' in this matrix
        act = "block"
    # redaction_possible=True in this matrix → no noop escalate
    return act


async def _inspect_cell(
    threat_label: str,
    ui_action: str,
    meta: dict,
    scanner: InputScanner,
) -> dict:
    canary_user = CANARIES[meta["canary_key"]]
    canary = meta.get("inspect_canary_override") or canary_user
    org_config = dict(meta["enable_keys"])
    org_config[meta["action_key"]] = ui_action

    # Default guard config: do NOT set output_block_on_ip_leakage (matches typical org).
    guard = OutputGuard(
        scanner,
        config={
            "output_guard_enabled": True,
            "output_tier2_enabled": False,
            "output_block_on_ip_leakage": False,
            "hallucination_flag_enabled": True,
        },
    )
    verdict = await guard.inspect(canary, context_chunks=None, org_config=org_config)

    # Detector preflight on the canary actually used
    preflight = {
        "detect_pii": list(detect_pii(canary).keys()),
        "detect_credential_exposure": list(detect_credential_exposure(canary).keys()),
        "detect_ip_leakage": list(detect_ip_leakage(canary).keys()),
        "detect_hallucination_markers": list(detect_hallucination_markers(canary).keys()),
    }

    row = {
        "threat": threat_label,
        "ui_action": ui_action,
        "canary_user": canary_user,
        "canary_inspect": canary,
        "org_config": org_config,
        "inspect_action": verdict.action,
        "inspect_threat_type": verdict.threat_type,
        "inspect_confidence": verdict.confidence,
        "inspect_detail": (verdict.detail or "")[:240],
        "inspect_matched_patterns": list(verdict.matched_patterns or []),
        "preflight": preflight,
        "ui_vs_inspect_mismatch": (
            ui_action != "allow" and verdict.action != ui_action
        )
        or (ui_action == "allow" and verdict.action != "allow"),
        "notes": [],
    }
    if meta.get("inspect_canary_note"):
        row["notes"].append(meta["inspect_canary_note"])

    # Known inspect-side coercions
    if threat_label == "ip_leakage" and ui_action in ("flag", "rewrite"):
        if verdict.action == "redact":
            row["notes"].append("known: IP flag/rewrite→redact inside inspect")
    if threat_label == "ip_leakage" and ui_action == "block" and verdict.action == "redact":
        row["notes"].append(
            "known: IP block→redact without output_block_on_ip_leakage on guard config"
        )
    if (
        threat_label in ("pii", "credential/secret")
        and ui_action == "block"
        and verdict.action == "redact"
    ):
        row["notes"].append("known: maskable block→redact inside inspect")

    # allow: detector should not fire a non-allow when action is allow (detector skipped)
    if ui_action == "allow":
        row["ui_vs_inspect_mismatch"] = verdict.action != "allow"
        if verdict.action == "allow":
            row["notes"].append("action=allow skips detector (expected allow)")

    return row


async def main() -> None:
    scanner = InputScanner(config={})
    scanner.tier2_enabled = False
    scanner._bedrock_scanner = None

    enforce_grid = []
    for threat_label, meta in THREAT_META.items():
        for threat_type in meta["enforce_threat_types"]:
            for ui_action in UI_ACTIONS:
                for is_streaming in (False, True):
                    d = enforce_output(
                        verdict_action=ui_action,
                        verdict_threat_type=threat_type,
                        is_streaming=is_streaming,
                        enforcement_mode="block",
                        redaction_possible=True,
                    )
                    expected = _expected_enforce(
                        ui_action, threat_type, is_streaming=is_streaming
                    )
                    cell = {
                        "threat_label": threat_label,
                        "verdict_threat_type": threat_type,
                        "ui_action": ui_action,
                        "is_streaming": is_streaming,
                        "enforcement_mode": "block",
                        "redaction_possible": True,
                        "resolved_action": d.action,
                        "blocked_by": d.blocked_by,
                        "expected_resolved": expected,
                        "ui_vs_resolved_mismatch": ui_action != d.action,
                        "coercion_notes": [],
                    }
                    if ui_action == "rewrite" and is_streaming and d.action == "block":
                        cell["coercion_notes"].append("rewrite+stream→block")
                    if ui_action == "flag" and d.action == "block":
                        cell["coercion_notes"].append("flag+enforcement_mode block→block")
                    if (
                        ui_action == "block"
                        and threat_type in _REDACTABLE_OUTPUT_THREAT_TYPES
                        and d.action == "redact"
                    ):
                        cell["coercion_notes"].append("maskable block→redact")
                    enforce_grid.append(cell)

    inspect_grid = []
    for threat_label, meta in THREAT_META.items():
        for ui_action in UI_ACTIONS:
            inspect_grid.append(await _inspect_cell(threat_label, ui_action, meta, scanner))

    # Extra: user AKIA canary under credential-only config (document miss)
    akia_note = None
    if True:
        org = dict(THREAT_META["credential/secret"]["enable_keys"])
        org["output_credential_action"] = "redact"
        guard = OutputGuard(
            scanner,
            config={"output_guard_enabled": True, "output_tier2_enabled": False},
        )
        v = await guard.inspect(
            CANARIES["credential/secret"], context_chunks=None, org_config=org
        )
        akia_note = {
            "canary": CANARIES["credential/secret"],
            "org_config": org,
            "inspect_action": v.action,
            "inspect_threat_type": v.threat_type,
            "detect_pii": list(detect_pii(CANARIES["credential/secret"]).keys()),
            "detect_credential_exposure": list(
                detect_credential_exposure(CANARIES["credential/secret"]).keys()
            ),
            "note": (
                "AKIA under credential-only config → allow (misses credential detector; "
                "would hit pii detector if output_pii_enabled)"
            ),
        }

    # Hallucination skip note if no fire
    hall_rows = [r for r in inspect_grid if r["threat"] == "hallucination"]
    hall_skip = None
    if all(r["inspect_action"] == "allow" and r["ui_action"] != "allow" for r in hall_rows):
        hall_skip = "hallucination detector did not fire on fabricated canary (below threshold)"

    ui_mismatches_enforce = [
        c for c in enforce_grid if c["ui_vs_resolved_mismatch"]
    ]
    ui_mismatches_inspect = [
        c for c in inspect_grid if c["ui_vs_inspect_mismatch"]
    ]

    # Combined "UI action != resolved action" — primary is enforce_output resolved;
    # inspect mismatches are separate (detector may coerce before enforce).
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "unit",
        "live_firewall_config_mutated": False,
        "enforcement_mode": "block",
        "redaction_possible": True,
        "known_coercions": KNOWN_COERCIONS,
        "canaries": CANARIES,
        "credential_inspect_canary_override": CREDENTIAL_INSPECT_CANARY,
        "akia_under_credential_only": akia_note,
        "hallucination_skip_note": hall_skip,
        "enforce_output_grid": enforce_grid,
        "inspect_grid": inspect_grid,
        "summary": {
            "enforce_cells": len(enforce_grid),
            "inspect_cells": len(inspect_grid),
            "ui_vs_enforce_mismatch_count": len(ui_mismatches_enforce),
            "ui_vs_inspect_mismatch_count": len(ui_mismatches_inspect),
            "ui_mismatch_count_primary": len(ui_mismatches_enforce),
            "ui_vs_enforce_mismatches": [
                {
                    "threat": c["verdict_threat_type"],
                    "ui_action": c["ui_action"],
                    "is_streaming": c["is_streaming"],
                    "resolved_action": c["resolved_action"],
                    "coercion_notes": c["coercion_notes"],
                }
                for c in ui_mismatches_enforce
            ],
            "ui_vs_inspect_mismatches": [
                {
                    "threat": c["threat"],
                    "ui_action": c["ui_action"],
                    "inspect_action": c["inspect_action"],
                    "notes": c["notes"],
                }
                for c in ui_mismatches_inspect
            ],
        },
    }

    OUT.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    print(json.dumps({
        "path": str(OUT),
        "ui_vs_enforce_mismatch_count": len(ui_mismatches_enforce),
        "ui_vs_inspect_mismatch_count": len(ui_mismatches_inspect),
        "enforce_cells": len(enforce_grid),
        "inspect_cells": len(inspect_grid),
        "hallucination_skip_note": hall_skip,
    }, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
