"""Shared fixtures for chat-pipeline golden snapshot tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

GOLDEN_DIR = Path(__file__).resolve().parent
SNAPSHOT_DIR = GOLDEN_DIR / "snapshots"


def normalize_stages(stages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Strip volatile fields so snapshots are stable across runs."""
    if not stages:
        return []
    out: list[dict[str, Any]] = []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        row = {
            "stage": stage.get("stage") or stage.get("pipeline_stage") or "",
            "action": stage.get("action") or "",
        }
        if stage.get("threat_type"):
            row["threat_type"] = stage["threat_type"]
        if stage.get("detection_tier"):
            row["detection_tier"] = stage["detection_tier"]
        if stage.get("matched_rules"):
            row["matched_rules"] = sorted(set(stage["matched_rules"]))
        if stage.get("matched_policy_names"):
            row["matched_policy_names"] = sorted(set(stage["matched_policy_names"]))
        out.append(row)
    return out


def load_snapshot(name: str) -> dict[str, Any]:
    path = SNAPSHOT_DIR / f"{name}.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_snapshot(name: str, payload: dict[str, Any]) -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOT_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture
def golden_snapshot_dir() -> Path:
    return SNAPSHOT_DIR
