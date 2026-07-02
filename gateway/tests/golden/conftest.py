"""Shared fixtures for chat-pipeline golden snapshot tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

GOLDEN_DIR = Path(__file__).resolve().parent
SNAPSHOT_DIR = GOLDEN_DIR / "snapshots"
_LIVE_SESSION_CACHE = GOLDEN_DIR / ".live_session.json"
_LIVE_SESSION_TTL_S = 3600

# Stages that matter for enforcement contract (drop auth/rate_limit/model_* noise).
_ENFORCEMENT_STAGES = frozenset({
    "policy",
    "policy_redact",
    "input_scan",
    "route",
    "output_guard",
    "output_guardrail",
})


def _map_stage_name(name: str) -> str:
    mapping = {
        "kill_switch": "route",
        "model_routing": "route",
        "model_state": "route",
        "output_scan": "output_guard",
        "output_guardrail": "output_guard",
    }
    return mapping.get(name, name)


def normalize_stages(stages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Strip volatile fields so snapshots are stable across runs."""
    if not stages:
        return []
    out: list[dict[str, Any]] = []
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        stage_name = _map_stage_name(
            str(stage.get("stage") or stage.get("name") or stage.get("pipeline_stage") or "")
        )
        if stage_name and stage_name not in _ENFORCEMENT_STAGES:
            continue
        row = {
            "stage": stage_name,
            "action": stage.get("action") or "",
        }
        if stage.get("threat_type"):
            row["threat_type"] = stage["threat_type"]
        if stage.get("detection_tier") or stage.get("tier"):
            row["detection_tier"] = stage.get("detection_tier") or stage.get("tier")
        if stage.get("matched_rules") or stage.get("matched_rule_names"):
            row["matched_rules"] = sorted(
                set(stage.get("matched_rules") or stage.get("matched_rule_names") or [])
            )
        if stage.get("matched_policy_names") or stage.get("matched_policies"):
            row["matched_policy_names"] = sorted(
                set(stage.get("matched_policy_names") or stage.get("matched_policies") or [])
            )
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


def _load_cached_live_session() -> dict[str, str] | None:
    preset_key = os.environ.get("GATEWAY_API_KEY", "").strip()
    preset_model = os.environ.get("SIM_MODEL", "").strip()
    if preset_key:
        return {"api_key": preset_key, "model": preset_model or "gemma-free"}
    if not _LIVE_SESSION_CACHE.is_file():
        return None
    try:
        import time

        cached = json.loads(_LIVE_SESSION_CACHE.read_text(encoding="utf-8"))
        if time.time() - float(cached.get("ts", 0)) > _LIVE_SESSION_TTL_S:
            return None
        key = str(cached.get("api_key") or "").strip()
        if not key:
            return None
        return {"api_key": key, "model": str(cached.get("model") or "gemma-free")}
    except Exception:
        return None


def _save_cached_live_session(session: dict[str, str]) -> None:
    import time

    payload = {"api_key": session["api_key"], "model": session.get("model", ""), "ts": time.time()}
    _LIVE_SESSION_CACHE.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture(scope="session")
def golden_snapshot_dir() -> Path:
    return SNAPSHOT_DIR


@pytest.fixture(scope="session")
def live_gateway_session() -> dict[str, str] | None:
    """Provision one simulator key + model for the live golden suite."""
    if os.environ.get("GATEWAY_LIVE", "1").strip().lower() in ("0", "false", "no"):
        return None
    cached = _load_cached_live_session()
    if cached:
        return cached
    try:
        from live_driver import _detect_model, _login, _simulator_key, live_gateway_reachable

        if not live_gateway_reachable():
            return None
        import httpx

        with httpx.Client() as client:
            jwt = _login(client)
            session = {
                "api_key": _simulator_key(client, jwt),
                "model": _detect_model(client, jwt),
            }
            _save_cached_live_session(session)
            return session
    except Exception:
        return None


def use_live_gateway() -> bool:
    return os.environ.get("GATEWAY_LIVE", "1").strip().lower() not in ("0", "false", "no")
