"""Auth helpers for org live-test gateway keys (simulators, playgrounds)."""

from __future__ import annotations

# Keys used only for in-product live testing must not be blocked by accumulated
# key risk_score — otherwise attack-simulator runs poison the key and every
# later scenario is rejected at threat_intel before input_scan / PII detection.
_LIVE_TEST_PROJECT_PREFIXES = ("isolation-playground-", "simulator-")


def is_live_test_project_id(project_id: str | None) -> bool:
    pid = project_id or ""
    return any(pid.startswith(prefix) for prefix in _LIVE_TEST_PROJECT_PREFIXES)


def should_skip_threat_intel(auth_ctx) -> bool:
    """Return True when threat-intel auto-block must not apply to live-test keys."""
    if auth_ctx is None:
        return False
    project_id = getattr(auth_ctx, "project_id", "") or ""
    if is_live_test_project_id(project_id):
        return True
    permissions = getattr(auth_ctx, "permissions", None) or {}
    return bool(permissions.get("playground"))
