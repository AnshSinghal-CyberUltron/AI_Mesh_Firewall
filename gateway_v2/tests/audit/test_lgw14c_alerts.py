"""GW14c: the alert rules reference metrics that exist, at the threshold the card signed.

This is the gate that would have caught R2-11's M4 class of problem from the other direction: an
alert whose `expr` names a series nothing emits is an alert that never fires, and it looks exactly
like an alert that never needed to.

Three assertions:

* every metric name in every `expr` is a series `audit/metrics.py` actually publishes;
* the memory alarm is at **0.6**, the card's number, not the reference README's suggested 0.7;
* the two ratios are both referenced, because the pair is what makes the M3 condition visible.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gateway_v2.audit.metrics import AuditMetrics

RULES = (
    Path(__file__).resolve().parents[3]
    / "deploy"
    / "observability"
    / "gw14c-audit-memory-alerts.yml"
)

METRIC_NAME = re.compile(r"\b(amf_(?:audit|store)_[a-z0-9_]+)\b")


@pytest.fixture(scope="module")
def text() -> str:
    assert RULES.exists(), f"{RULES} is missing"
    return RULES.read_text(encoding="utf-8")


def _exprs(text: str) -> list[str]:
    """Every `expr:` body, including the `>-` folded ones."""
    out: list[str] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("expr:"):
            continue
        body = stripped[len("expr:"):].strip()
        if body in {">-", ">", "|", "|-"}:
            body = ""
            for follow in lines[index + 1:]:
                if not follow.strip() or follow.strip().startswith(("- alert:", "labels:")):
                    break
                body += " " + follow.strip()
        out.append(body.strip())
    return out


def test_every_metric_in_every_expr_is_actually_published(text: str) -> None:
    """An alert naming a series nothing emits never fires, and looks like one that never had to."""
    published = set(AuditMetrics().series())
    referenced: set[str] = set()
    for expr in _exprs(text):
        referenced.update(METRIC_NAME.findall(expr))

    assert referenced, "no metrics were found in the rules; the parser is wrong, not the rules"
    missing = sorted(referenced - published)
    assert not missing, f"alert rules reference series that are never emitted: {missing}"


def test_the_memory_alarm_is_at_the_cards_sixty_percent(text: str) -> None:
    """The card says 60%. The reference README suggested 0.7. The card wins, and an alert at an
    unrelated number would quietly redefine what was signed."""
    exprs = [expr for expr in _exprs(text) if "amf_store_memory_used_ratio" in expr]

    assert any("> 0.6" in expr for expr in exprs), f"no 60% threshold found in {exprs}"


def test_both_ratios_are_referenced(text: str) -> None:
    """`acknowledged_ratio` reading 1.0 while `completeness_ratio` does not IS the M3 condition.
    An operator needs both names in front of them for that to be readable."""
    assert "amf_audit_completeness_ratio" in text
    assert "amf_audit_acknowledged_ratio" in text


def test_the_eviction_and_loss_conditions_both_have_rules(text: str) -> None:
    for alert in (
        "AuditStoreMemoryHigh",
        "StoreEvictionPolicyUnsafe",
        "StoreKeysEvicted",
        "AuditRecordsLost",
        "AuditTrimOutrunningExport",
        "AuditProducerDropping",
    ):
        assert f"alert: {alert}" in text, f"{alert} is missing"


def test_the_file_declares_that_it_is_not_loaded_yet(text: str) -> None:
    """Same convention as gw05b-state-freshness-alerts.yml: ship the thresholds with the card,
    and say plainly that nothing serves these series yet."""
    assert "NOT LOADED YET" in text
    assert "rule_files" in text


def test_the_rules_parse_as_yaml(text: str) -> None:
    yaml = pytest.importorskip("yaml", reason="pyyaml is not installed in this venv")
    parsed = yaml.safe_load(text)

    assert isinstance(parsed, dict)
    groups = parsed["groups"]
    assert len(groups) >= 2
    for group in groups:
        assert group["name"].startswith("amf-")
        for rule in group["rules"]:
            assert rule["expr"]
            assert rule["labels"]["severity"] in {"warning", "critical"}
            assert rule["annotations"]["summary"]
            assert rule["annotations"]["description"]
