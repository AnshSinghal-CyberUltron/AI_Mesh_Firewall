"""GW35 lint: vocabulary mirror, signed-row reporting, retired-route drift, stage wording."""

from __future__ import annotations

import json
import re
from pathlib import Path

import lint.console_inventory as inventory
from lint import admin_ledger
from lint.check_console_inventory import main as inventory_main
from lint.console_inventory import repo_root

_ROOT = repo_root()


def test_the_frontend_vocabulary_matches_the_schema() -> None:
    assert inventory.vocabulary_messages(_ROOT) == []
    assert set(inventory.python_vocab(_ROOT)) == {
        "allow", "flag", "redact", "block", "error", "skip",
    }


def test_a_vocabulary_mismatch_is_reported(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(inventory, "frontend_vocab", lambda _repo: ("allow", "block"))
    messages = inventory.vocabulary_messages(_ROOT)
    assert messages and "schema-only" in messages[0] and "error" in messages[0]


def test_the_contract_diff_names_unguarded_reads_and_skips_guarded_files() -> None:
    lines = inventory.contract_diff(_ROOT)
    assert all(": reads " in line for line in lines)
    guarded = "frontend/src/utils/liveGateway.js"
    assert not any(line.startswith(guarded) for line in lines)
    assert inventory_main(["--contract-diff"]) == 0


def test_the_contract_set_has_the_v2_fields() -> None:
    assert {"final_action", "plan_version", "deciding_rule", "ran", "source"} <= (
        inventory.v2_contract_reads()
    )


def test_unsigned_rows_report_pending_and_only_fail_when_signing_is_required() -> None:
    pending = admin_ledger.pending_signature(_ROOT)
    assert {"/v1/usage", "/v1/observability", "/v1/policy/check"} <= set(pending)
    assert admin_ledger.main([]) == 0
    assert admin_ledger.main(["--require-signed"]) == 1


def test_the_three_d11_rows_are_retire_rows_owned_by_gw35() -> None:
    rows = json.loads((_ROOT / admin_ledger.LEDGER_PATH).read_text(encoding="utf-8"))["routes"]
    by_path = {row["path"]: row for row in rows}
    for path in ("/v1/usage", "/v1/observability", "/v1/policy/check"):
        row = by_path[path]
        assert (row["decision"], row["owner"], row["callers"]) == ("retire", "gw35", [])
        assert row["signed"] is None and row["decision_ref"]


def _retired_table() -> set[str]:
    text = (_ROOT / "control/ai_mesh_control/core/retired_routes.py").read_text(encoding="utf-8")
    block = text.split("RETIRED_ON_V2 = MappingProxyType(", 1)[1].split("\n)\n", 1)[0]
    return set(re.findall(r'"(/v1/[A-Za-z0-9_/-]+)":', block))


def test_the_control_retired_table_equals_the_retire_rows_with_console_callers() -> None:
    rows = json.loads((_ROOT / admin_ledger.LEDGER_PATH).read_text(encoding="utf-8"))["routes"]
    expected: set[str] = set()
    for row in rows:
        path = str(row["path"])
        if row["decision"] != "retire" or "/rag/" in path:
            continue  # RAG and vector proxies are GW18's, not retired by this card
        if path.startswith("/api/admin/gateway/") and any(
            str(c).startswith("frontend/") for c in row["callers"]
        ):
            expected.add("/v1/admin/" + path.removeprefix("/api/admin/gateway/").replace("/", "-"))
        elif path.startswith("/v1/mcp/internal/"):
            expected.add(path)
    assert _retired_table() == expected


def test_no_doc_names_circuit_breaker_as_a_dropped_stage() -> None:
    names = ("kill" + "_switch", "model" + "_input")
    stale = re.compile(r"`circuit_breaker`\s*(?:,|and)?\s*(?:are|is) not separate stage")
    docs = [
        _ROOT / "docs/AI_MESH_MASTER_RUNBOOK_v3_BACKEND_REWRITE.md",
        _ROOT / "docs/AI_MESH_RUNBOOK_v3.1_PROGRESS.md",
        *sorted((_ROOT / "docs/plans").glob("*.md")),
    ]
    for doc in docs:
        text = Path(doc).read_text(encoding="utf-8", errors="replace")
        assert not stale.search(text), doc
    plan = _ROOT / "docs/plans/2026-10-26-gw14b-console-contract.md"
    contract = plan.read_text(encoding="utf-8")
    assert all(name in contract for name in names)
    assert "awaiting owner signature" in contract
