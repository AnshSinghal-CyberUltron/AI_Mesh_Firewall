"""Optional: T02 recorder still answers on loopback admin (reuse, do not rebuild)."""

from __future__ import annotations

import os
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("AMF_T02_RECORDER_ADMIN"),
    reason="AMF_T02_RECORDER_ADMIN unset (CI has no T02 sidecar)",
)


def test_t02_recorder_health() -> None:
    url = os.environ["AMF_T02_RECORDER_ADMIN"].rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            body = resp.read()
            status = resp.status
    except urllib.error.URLError as exc:
        pytest.fail(f"T02 recorder unreachable: {exc}")
    assert status == 200
    assert b"ok" in body.lower() or b"healthy" in body.lower() or body
