"""Gateway-v2 rows stay out of the v1 total, and the ingest does not write the v1 list."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
_SHADOW = _ROOT / "control" / "ai_mesh_control" / "policy" / "v2_shadow.py"
_INGEST = _ROOT / "control" / "ai_mesh_control" / "core" / "v2_audit_ingest.py"


def test_shadow_rows_are_not_added_to_the_primary_total() -> None:
    module = _load()
    primary, shadow = module.split_source(
        [{"source": "mcp_scan", "action": "block"}, {"source": module.SOURCE, "action": "redact"}],
    )
    assert len(primary) == 1
    assert len(shadow) == 1
    assert module.SOURCE == "gateway_v2"


def test_the_v2_ingest_does_not_write_the_v1_telemetry_list() -> None:
    text = _INGEST.read_text(encoding="utf-8")
    assert "telemetry:events" not in text
    assert "REDIS_TELEMETRY_KEY" not in text


def test_a_v2_row_does_not_gain_policy_or_medium() -> None:
    module = _load()
    meta = {"source": "gateway_v2"}
    assert module.feed_category(meta, None) == ""
    assert module.feed_severity(meta) == ""
    assert module.feed_category({"source": "security_scan"}, None) == "Policy"
    assert module.feed_severity({"source": "security_scan"}) == "medium"
    assert module.feed_severity({"source": "gateway_v2", "severity": "high"}) == "high"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("v2_shadow", _SHADOW)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
