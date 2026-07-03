"""Resilience regression: boot-time control-plane calls must FAIL FAST.

Root cause captured here (evidence: this session, 2026-07-03):
    The lifespan startup() called _check_version() then an OUTER loop of 5 _register()
    attempts; _register() ITSELF used _http_request_with_retry's inner 5-attempt x ~30s
    budget -> ~25 registration attempts x up to 30s + a ~5x30s version check. When the
    control plane was unhealthy at boot, startup() blocked for MANY MINUTES and every
    gunicorn worker sat at "Waiting for application startup" -- the gateway was unavailable
    even though the Redis policy cache already had the compiled policies.

Registration is best-effort coordination, NOT enforcement (the gateway enforces from the
Redis POLICY_SYNC cache with no control plane), and `_background_register_loop` retries
forever with backoff once the app is serving. So the SYNCHRONOUS boot calls must be bounded.
These tests lock that: a single short-timeout attempt per call, no inner backoff/retry, so a
slow/unhealthy control plane can never re-introduce the multi-minute boot stall.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402


def test_startup_control_call_timeout_is_bounded():
    assert 0 < main.STARTUP_CONTROL_CALL_TIMEOUT_SECONDS <= 10
    # strictly tighter than the startup registration budget that used to be nested here
    assert main.STARTUP_CONTROL_CALL_TIMEOUT_SECONDS < main.STARTUP_RETRY_MAX_DELAY_SECONDS


def test_check_version_single_bounded_attempt(monkeypatch):
    calls = []
    sleeps = []

    def fake_http_request(method, url, data=None, api_key=None, timeout=30):
        calls.append({"method": method, "url": url, "timeout": timeout})
        return None, "timed out"  # control unreachable

    monkeypatch.setattr(main, "CONFIG", {"backend_url": "http://control:8000", "api_key": "k"})
    monkeypatch.setattr(main, "_http_request", fake_http_request)
    monkeypatch.setattr(main.time, "sleep", lambda s: sleeps.append(s))

    main._check_version()  # must return quietly, not raise

    assert len(calls) == 1, f"version check must be ONE attempt, got {len(calls)}"
    assert sleeps == [], "no backoff on the boot version check"
    assert calls[0]["timeout"] == main.STARTUP_CONTROL_CALL_TIMEOUT_SECONDS
    assert calls[0]["url"].endswith("/api/agents/version/")


def test_register_single_attempt_no_nested_retry_storm(monkeypatch):
    calls = []
    sleeps = []

    def fake_http_request(method, url, data=None, api_key=None, timeout=30):
        calls.append({"method": method, "url": url, "timeout": timeout})
        return None, "timed out"  # control unreachable

    monkeypatch.setattr(main, "CONFIG", {
        "backend_url": "http://control:8000",
        "api_key": "k",
        "gateway_name": "gw-test",
        "gateway_location": "loc",
    })
    monkeypatch.setattr(main, "_http_request", fake_http_request)
    monkeypatch.setattr(main.time, "sleep", lambda s: sleeps.append(s))

    ok = main._register()

    assert ok is False  # control down -> not registered (background loop will retry)
    assert len(calls) == 1, (
        f"register must be a SINGLE fast attempt (the caller owns retries); got {len(calls)} "
        "-- a >1 count means the inner 5x30s nested-retry storm is back"
    )
    assert sleeps == [], "no inner backoff inside _register"
    assert calls[0]["timeout"] == main.STARTUP_CONTROL_CALL_TIMEOUT_SECONDS
    assert calls[0]["url"].endswith("/api/gateways/instances/register/")


def test_background_register_loop_still_exists():
    # The safety net that makes bounding the sync calls safe: registration is retried
    # forever in the background after the app is already serving.
    assert callable(getattr(main, "_background_register_loop", None))
