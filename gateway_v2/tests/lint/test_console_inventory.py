"""The checked-in GW14b inventory matches this tree, and a new read fails the gate."""

from __future__ import annotations

import json

from lint.check_console_inventory import main
from lint.console_inventory import CONFIG_PATH, CONSUMERS_PATH, diff_reads, repo_root


def test_a_new_frontend_read_is_not_in_the_inventory() -> None:
    committed: list[dict[str, object]] = []
    fresh: list[dict[str, object]] = [
        {
            "field": "final_action",
            "via": "pipeline_trace",
            "files": ["frontend/src/utils/liveGateway.js"],
        },
    ]
    messages = diff_reads(committed, fresh)
    assert messages == [
        "frontend read final_action via pipeline_trace in "
        "frontend/src/utils/liveGateway.js is not in the inventory",
    ]


def test_checked_in_inventory_matches_the_tree() -> None:
    assert main([]) == 0
    root = repo_root()
    consumers = json.loads((root / CONSUMERS_PATH).read_text(encoding="utf-8"))
    defaults = consumers["drain"]["silent_defaults"]
    assert {row["field"] for row in defaults} == {"event_type", "module_id", "risk_score"}
    config = json.loads((root / CONFIG_PATH).read_text(encoding="utf-8"))
    assert config["exposed_count"] == len(config["exposed_fields"])
    assert "blocked_keywords" in config["exposed_fields"]
    assert "updated_by" not in config["exposed_fields"]
