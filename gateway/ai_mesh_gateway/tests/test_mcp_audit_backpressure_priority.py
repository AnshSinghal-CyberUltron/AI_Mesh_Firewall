"""CHG-0094: MCP audit backpressure must NOT drop security-decision records.

``_spawn_audit_event`` sheds audit POSTs when too many are already draining to control
(bounded, best-effort). Previously the drop was indiscriminate: an attack producing many
block/redact decisions could fill the queue and DROP the very block/redact audits it
created — the ...->tag->AUDIT chain broke silently under load. CHG-0094 gives SECURITY
decisions (block/redact/rate_limited/error) a higher inflight ceiling so they survive a
burst that sheds the high-volume allow/monitor/clean records, and meters every drop.

These tests drive the REAL ``_spawn_audit_event`` decision (the task spawn + the actual
control POST are stubbed) and assert the shed decision + the drop metric.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))
_SHARED = _GW.parents[1] / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

import mcp_proxy  # noqa: E402
import metrics  # noqa: E402


class _FakeTask:
    def add_done_callback(self, _cb):
        pass


@pytest.fixture(autouse=True)
def _isolate_audit_state(monkeypatch):
    """Stub the task spawn + control POST so no network/loop is needed, capture drops,
    and restore the module inflight counter/task set around each test."""
    drops: list[tuple[str, str]] = []
    monkeypatch.setattr(mcp_proxy, "_post_audit_event", lambda h, p: object())
    monkeypatch.setattr(mcp_proxy.asyncio, "create_task", lambda _coro: _FakeTask())
    monkeypatch.setattr(metrics, "record_mcp_audit_dropped",
                        lambda pri, dec: drops.append((pri, dec)))
    saved = mcp_proxy._AUDIT_INFLIGHT
    saved_tasks = set(mcp_proxy._AUDIT_TASKS)
    yield drops
    mcp_proxy._AUDIT_INFLIGHT = saved
    mcp_proxy._AUDIT_TASKS.clear()
    mcp_proxy._AUDIT_TASKS.update(saved_tasks)


def _spawn(decision: str) -> bool:
    return mcp_proxy._spawn_audit_event({"h": "1"}, {"decision": decision, "tool_name": "x"})


def test_below_cap_all_decisions_spawn(_isolate_audit_state):
    mcp_proxy._AUDIT_INFLIGHT = 0
    assert _spawn("allow") is True
    assert _spawn("block") is True
    assert _isolate_audit_state == []  # nothing dropped


def test_security_audit_survives_normal_backpressure(_isolate_audit_state):
    """At the NORMAL cap, an allow audit is shed but block/redact/rate_limited survive."""
    mcp_proxy._AUDIT_INFLIGHT = mcp_proxy._AUDIT_MAX_INFLIGHT  # 64
    assert _spawn("allow") is False, "high-volume allow audit should shed first"
    # security decisions keep going (higher ceiling) — the audit chain holds under load
    assert _spawn("block") is True
    assert _spawn("redact") is True
    assert _spawn("rate_limited") is True
    assert _spawn("error") is True
    assert ("normal", "allow") in _isolate_audit_state
    # no security decision was dropped
    assert not any(pri == "high" for pri, _ in _isolate_audit_state)


def test_monitor_and_clean_treated_as_normal(_isolate_audit_state):
    mcp_proxy._AUDIT_INFLIGHT = mcp_proxy._AUDIT_MAX_INFLIGHT
    assert _spawn("monitor") is False
    assert _spawn("clean") is False
    assert ("normal", "monitor") in _isolate_audit_state
    assert ("normal", "clean") in _isolate_audit_state


def test_security_audit_dropped_at_hard_cap_and_metered(_isolate_audit_state):
    """Even a security audit drops at the HARD ceiling — bounded memory — but the drop
    is metered as high-priority so the lost security record is visible/alertable."""
    mcp_proxy._AUDIT_INFLIGHT = mcp_proxy._AUDIT_MAX_INFLIGHT_HIGH  # 256
    assert _spawn("block") is False
    assert ("high", "block") in _isolate_audit_state


def test_high_cap_exceeds_normal_cap():
    # the whole mechanism depends on security decisions having strictly more headroom
    assert mcp_proxy._AUDIT_MAX_INFLIGHT_HIGH > mcp_proxy._AUDIT_MAX_INFLIGHT
    assert "block" in mcp_proxy._AUDIT_HIGH_PRIORITY_DECISIONS
    assert "redact" in mcp_proxy._AUDIT_HIGH_PRIORITY_DECISIONS
    assert "rate_limited" in mcp_proxy._AUDIT_HIGH_PRIORITY_DECISIONS
    assert "allow" not in mcp_proxy._AUDIT_HIGH_PRIORITY_DECISIONS


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
