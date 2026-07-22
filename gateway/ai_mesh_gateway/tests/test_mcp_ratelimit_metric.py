"""CHG-0089: the MCP per-org rate limiter (_mcp_org_rate_limit_raw → _enforce_org_tpm_
rate_limit / _enforce_org_burst_rpm) returned a 429 but never METERED it. Only the CHAT
per-model limiter was metered (main.py record_rate_limit), so MCP 429 storms under load
were invisible to Prometheus dashboards/alerting (item 13/20). MCP throttling is now
recorded as a `rate_limited` decision in amf_gateway_mcp_scan_decisions_total, and the
TPM→burst short-circuit + allow (None) behaviour is preserved.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_GW = Path(__file__).resolve().parents[1]
if str(_GW) not in sys.path:
    sys.path.insert(0, str(_GW))
_SHARED = _GW.parents[1] / "shared"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.append(str(_SHARED))

import mcp_proxy as MP  # noqa: E402
import metrics as M  # noqa: E402

_SENTINEL = "RL429"  # stands in for the 429 JSONResponse


class _Auth:
    org_slug = "orgRLtest"
    user_id = 1
    project_id = "p"


def _cnt():
    if not M._PROM_AVAILABLE:
        return 0.0
    return M.REGISTRY.get_sample_value(
        "amf_gateway_mcp_scan_decisions_total",
        {"org": "orgrltest", "decision": "rate_limited"}) or 0.0


def _fake_main(tpm, burst):
    fm = MagicMock()
    fm._enforce_org_tpm_rate_limit = AsyncMock(return_value=tpm)
    fm._enforce_org_burst_rpm = AsyncMock(return_value=burst)
    return fm


def _as_gateway_main(fm):
    """Install the fake under BOTH module identities the resolver considers.

    ``main`` is importable as ``main`` and as ``ai_mesh_gateway.main``, and
    ``mcp_proxy._gateway_app_module()`` prefers the packaged one whose ``CONFIG``
    is populated. Overriding only the bare ``main`` key leaves the real packaged
    module in place, the resolver picks IT, and the fake is never consulted —
    the stub silently no-ops. Bind both so resolution is deterministic.
    """
    return patch.dict("sys.modules", {"main": fm, "ai_mesh_gateway.main": fm})


@pytest.mark.asyncio
async def test_tpm_trip_meters_and_short_circuits():
    fm = _fake_main(_SENTINEL, None)
    with _as_gateway_main(fm):
        before = _cnt()
        resp = await MP._mcp_org_rate_limit_raw(_Auth())
    assert resp == _SENTINEL
    assert fm._enforce_org_burst_rpm.await_count == 0, "burst must be short-circuited when TPM trips"
    if M._PROM_AVAILABLE:
        assert _cnt() == before + 1


@pytest.mark.asyncio
async def test_burst_trip_meters():
    fm = _fake_main(None, _SENTINEL)
    with _as_gateway_main(fm):
        before = _cnt()
        resp = await MP._mcp_org_rate_limit_raw(_Auth())
    assert resp == _SENTINEL
    if M._PROM_AVAILABLE:
        assert _cnt() == before + 1


@pytest.mark.asyncio
async def test_allowed_does_not_meter():
    fm = _fake_main(None, None)
    with _as_gateway_main(fm):
        before = _cnt()
        resp = await MP._mcp_org_rate_limit_raw(_Auth())
    assert resp is None
    if M._PROM_AVAILABLE:
        assert _cnt() == before


@pytest.mark.asyncio
async def test_none_auth_is_noop():
    assert await MP._mcp_org_rate_limit_raw(None) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
