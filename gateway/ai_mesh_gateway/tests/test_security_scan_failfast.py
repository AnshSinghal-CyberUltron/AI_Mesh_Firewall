"""Resilience regression: the ADVISORY backend security scan must FAIL FAST.

Root cause captured here (evidence: gateway logs, 2026-07-03):
    A chat request that is ALLOWED forwards to the model (which answered in ~2s),
    but the response path AWAITS the org "deep scan" task (main.py ~7840). That
    task calls ``_security_scan`` -> ``_http_request_with_retry``. Previously
    ``_security_scan`` inherited the STARTUP retry budget (5 attempts x up to ~30s
    backoff + a 30s/attempt connect timeout). So whenever the control plane was
    unhealthy, EVERY allowed chat request hung ~40s and timed out on the client —
    NOT a model-degradation issue, a control-plane retry storm on a best-effort call.

The advisory scan can only ADD a high-certainty block (main.py ~5896 / ~7841); it
never gates an ``allow`` and the local policy engine + Tier-1/Tier-2 scanners are the
authoritative enforcement. It must therefore be bounded so a slow/unhealthy control
plane can never stall the inline request path. These tests lock that invariant:

  * exactly ONE attempt (no multi-attempt retry storm),
  * NO backoff sleep on that single attempt,
  * a SHORT per-attempt timeout (<= the startup default, in fact <= 5s),
  * a control-down result returns promptly as a best-effort miss (code is None),

so a regression that re-widens the advisory scan's retry/timeout budget fails here
instead of silently re-introducing the 40s allowed-request hang.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402


def test_security_scan_constants_are_bounded():
    # A single attempt with a short timeout — never the startup retry budget.
    assert main.SECURITY_SCAN_MAX_ATTEMPTS == 1
    assert 0 < main.SECURITY_SCAN_TIMEOUT_SECONDS <= 5.0
    # And it must be strictly tighter than the startup-registration budget, which is
    # what the pre-fix code reused on the inline request path.
    assert main.SECURITY_SCAN_MAX_ATTEMPTS < main.STARTUP_RETRY_MAX_ATTEMPTS
    assert main.SECURITY_SCAN_TIMEOUT_SECONDS < 30


def test_security_scan_single_attempt_no_backoff_when_control_down(monkeypatch):
    """Simulate an unhealthy/unreachable control plane and prove the advisory scan
    makes ONE fast attempt with a short timeout and does not sleep/retry."""
    calls = []
    sleeps = []

    def fake_http_request(method, url, data=None, api_key=None, timeout=30):
        calls.append({"method": method, "url": url, "timeout": timeout})
        # control unreachable -> _http_request returns (None, <error string>)
        return None, "timed out"

    monkeypatch.setattr(main, "CONFIG", {
        "backend_url": "http://control:8000",
        "api_key": "test-key",
    })
    monkeypatch.setattr(main, "_http_request", fake_http_request)
    # any backoff sleep would be a retry-storm regression on the inline path
    monkeypatch.setattr(main.time, "sleep", lambda s: sleeps.append(s))

    code, body = main._security_scan("hello world", "")

    # best-effort miss, surfaced promptly
    assert code is None
    # exactly one attempt — no 5x retry storm
    assert len(calls) == 1, f"expected a single advisory attempt, got {len(calls)}"
    # no backoff sleeps at all
    assert sleeps == [], f"advisory scan must not sleep/backoff, slept: {sleeps}"
    # the attempt used the bounded short timeout, not the 30s startup default
    assert calls[0]["timeout"] == main.SECURITY_SCAN_TIMEOUT_SECONDS
    assert calls[0]["timeout"] <= 5.0
    assert calls[0]["url"].endswith("/api/security/scan/")


def test_security_scan_config_override_is_honored(monkeypatch):
    """Ops may tune the bound via org-synced CONFIG without a code change."""
    seen = {}

    def fake_http_request(method, url, data=None, api_key=None, timeout=30):
        seen["timeout"] = timeout
        return 200, {"recommended_action": "allow"}

    monkeypatch.setattr(main, "CONFIG", {
        "backend_url": "http://control:8000",
        "api_key": "k",
        "security_scan_timeout_seconds": 2.5,
        "security_scan_max_attempts": 1,
    })
    monkeypatch.setattr(main, "_http_request", fake_http_request)

    code, body = main._security_scan("x", "")
    assert code == 200
    assert seen["timeout"] == 2.5


def test_http_request_with_retry_threads_timeout(monkeypatch):
    """The plumbing that makes the bound possible: timeout must reach _http_request."""
    seen = {}

    def fake_http_request(method, url, data=None, api_key=None, timeout=30):
        seen["timeout"] = timeout
        return 200, {"ok": True}

    monkeypatch.setattr(main, "_http_request", fake_http_request)
    code, body = main._http_request_with_retry(
        "POST", "http://x/y", data={"a": 1}, api_key="k", max_attempts=1, timeout=3.0,
    )
    assert code == 200
    assert seen["timeout"] == 3.0
